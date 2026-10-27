# -*- coding: utf-8 -*-
r"""
.claude/tools/hook_guard.py — Claude Code 실행 통제 후크(docs/PROCESS_ROLES.md 9장). FSS-team-bootstrap이 생성(2026-10-27).

배경: 에이전트 셸에는 대화형 입력이 없다. 입력 없는 python REPL 하나가 64분간 5.4 GB를 쓰며 살아 있었고 보고에는
"바로 종료했다"고 적힌 사고가 있었다. 지시만으로는 막지 못해 기계적으로 막는다.
후크 입력에는 서브에이전트가 실행한 도구 호출에만 agent_id·agent_type이 붙는다 — 매니저와 서브에이전트를 구분할 수 있다.

모드(첫 인자)
  pretool   PreToolUse(Bash·PowerShell): 입력을 기다리는 형태(python REPL·`python -`·`-i`, 페이저·편집기,
            메시지 없는 `git commit`, git -i/-p, Read-Host·Get-Credential·pause·Out-GridView)를 누구든 거부한다.
            서브에이전트(agent_id 있음)의 시스템 python도 거부한다 → venv python만.
            PreToolUse(Write·Edit·NotebookEdit): reviewer는
            메인 저장소 `_workspace/` 안에서만 허용.
            한계: 명령 문자열과 대상 경로만 본다 — 스크립트 안에서 하는 쓰기는 못 잡는다(9-6).
  substop   SubagentStop: 서브에이전트가 남긴 실행 중 백그라운드 작업과 명백한 이상(REPL 형태 python, 100 MB 초과·
            계속 커지는 tasks/b*.output)을 기록·경고한다. 종료는 막지 않는다(막아도 정리 지시가 전달되지 않는다).
  approval  PermissionRequest: 승인 요청을 기록만 한다(결정하지 않음). 감시자가 읽어 알린다.

원칙: 후크 자체의 오류는 절대 명령을 막지 않는다(fail-open).
기록: <저장소>/_workspace/hooks/guard.log·approvals.log·agent_background.jsonl (git 제외).
"""
from __future__ import annotations

import datetime as _dt
import json
import ntpath
import os
import re
import subprocess
import sys
import time
from typing import Iterator, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("TEAM_REPO", os.path.dirname(os.path.dirname(HERE)))   # .claude/tools/ 의 두 단계 위
LOG_DIR = os.path.join(REPO, "_workspace", "hooks")
SUB_PYTHON_ONLY = "true" == "true"
PY_REPL_DENY = "true" == "true"   # false(프로젝트 예외)면 `python -` 허용, 단독 python·-i는 계속 거부
VENV_PY = r"G:\workspace\PowerMate\.venv\Scripts\python.exe"
VENV_PY_POSIX = "/g/workspace/PowerMate/.venv/Scripts/python.exe"
TASKS_ROOT = os.environ.get("TASKS_ROOT", r"C:\Users\curta\AppData\Local\Temp\claude\G--workspace-PowerMate")
BIG_MB = int(os.environ.get("LEFTOVER_BIG_MB", "100"))
OUT_DIR = "_workspace"
TMP_ROOT = r"G:\tmp\PowerMate"
PROTECTED = []           # 보호(읽기 전용) 데이터 — 회귀(골든) 모듈이 꺼져 있으면 비어 있다
PROTECTED_ENV = ""       # 보호 데이터를 가리키는 환경변수(명령 속 $VAR·$env:VAR·%VAR%)
WORKSPACE_ONLY_ROLES = tuple(["reviewer"])   # Write·Edit를 메인 저장소 OUT_DIR 안으로 제한할 agent_type
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# ───────── 명령 분해 ─────────
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(cmd: str) -> str:
    """heredoc 본문을 지워 본문 속 단어를 명령으로 오인하지 않게 한다. `<<` 표지 줄은 남긴다."""
    lines = cmd.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC.search(line)
        if m:
            term = m.group(2)
            while i < len(lines) and lines[i].strip() != term:
                i += 1
            i += 1
    return "\n".join(out)


def mask_quoted_separators(s: str) -> str:
    """따옴표 안의 구분자(| ; & 줄바꿈)를 공백으로 바꿔 문자열 속 `a|python`을 명령으로 쪼개지 않게 한다."""
    out, q = [], None
    for ch in s:
        if q:
            if ch == q:
                q = None
            elif ch in "|;&\n":
                ch = " "
        elif ch in ("'", '"'):
            q = ch
        out.append(ch)
    return "".join(out)


def mask_quoted_content(s: str) -> str:
    """따옴표 안 내용을 'x'로 가린다 — 커밋 메시지·grep 패턴 속 단어를 명령으로 오인하지 않게."""
    out, q = [], None
    for ch in s:
        if q:
            if ch == q:
                q = None
            else:
                ch = "x" if not ch.isspace() else ch
        elif ch in ("'", '"'):
            q = ch
        out.append(ch)
    return "".join(out)


_SPLIT = re.compile(r"\n|&&|\|\||;|\|&?|(?<![<>])&(?![>\d])")   # `2>&1`·`&>`의 &는 리디렉션이라 자르지 않는다


def segments(cmd: str) -> Iterator[str]:
    """명령을 단위 명령으로 나눈다(보수적 근사 — 완전한 셸 해석기는 아니다)."""
    for seg in _SPLIT.split(mask_quoted_separators(strip_heredocs(cmd))):
        seg = seg.strip()
        if not seg or re.fullmatch(r"(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=\S*", seg):   # 변수 대입만(P=/c/.../python)
            continue
        seg = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+", "", seg)      # FOO=bar cmd
        seg = re.sub(r"^timeout\s+(?:-\S+\s+)*\S+\s+", "", seg)           # timeout 30m cmd
        seg = re.sub(r"^(?:exec|command|nohup|&)\s+", "", seg)
        if seg:
            yield seg


_PY_HEAD = re.compile(r"^(\"[^\"]*?(?:python[0-9.]*|py)(?:\.exe)?\"|[^\s\"]*?(?:python[0-9.]*|py)(?:\.exe)?)(?=\s|$)", re.I)


def _python_call(seg: str):
    """(실행 파일, 나머지 인자) 또는 None."""
    m = _PY_HEAD.match(seg)
    if not m:
        return None
    exe = m.group(1).strip('"')
    base = re.split(r"[\\/]", exe)[-1].lower()
    if not re.fullmatch(r"(python[0-9.]*|py)(\.exe)?", base):
        return None
    return exe, seg[m.end():].strip()


_TOKEN = re.compile(r"\"[^\"]*\"?|'[^']*'?|\S+")
_REDIR = re.compile(r"^(?:\d+|\*|&)?(>>?|<<?<?)(.*)$")


def python_wait_kind(rest: str) -> Optional[str]:
    """python 인자로 입력 대기 여부를 판정한다. 리디렉션(`2>&1`·`> f`·`< f`)과 옵션(`-u`·`-X utf8`)을 걷어내고 본다.
    "repl": 실행할 것이 없거나 `-i` → 입력을 기다린다. "stdin": `python -`·`< 파일`·heredoc처럼 표준 입력을 스크립트로 읽는다.
    None: 스크립트·`-c`·`-m`·`--version` 등 바로 끝나는 호출."""
    toks = _TOKEN.findall(rest)
    args, stdin_in = [], False
    i = 0
    while i < len(toks):
        m = _REDIR.match(toks[i])
        if m:
            stdin_in |= m.group(1).startswith("<")
            if not m.group(2):
                i += 1               # `> 파일`처럼 대상이 다음 토큰
        else:
            args.append(toks[i])
        i += 1
    interactive = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-":
            return "repl" if interactive else "stdin"
        if a in ("--version", "--help", "-h", "-V", "-VV", "-?"):
            return None
        if a.startswith("--"):       # --check-hash-based-pycs <mode> 등
            i += 2 if a == "--check-hash-based-pycs" else 1
            continue
        if a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                if ch in "cm":
                    return "repl" if interactive else None
                if ch == "i":
                    interactive = True
                if ch in "XW":       # 값을 받는 옵션: 붙어 있으면 그 토큰, 아니면 다음 토큰
                    if j == len(a) - 1:
                        i += 1
                    break
            i += 1
            continue
        return "repl" if interactive else None    # 스크립트 파일
    if interactive:
        return "repl"
    return "stdin" if stdin_in else "repl"


def is_venv(exe: str) -> bool:
    parts = exe.replace("\\", "/").lower().split("/")
    return "venv" in parts or ".venv" in parts


_GIT_COMMIT_MSG = re.compile(r"\s(-[A-Za-z]*[mFC]\b|--message\b|--file\b|--no-edit\b|--reuse-message\b|--fixup\b)")


# ───────── 보호 데이터 쓰기 판정 ─────────
def _protected_re():
    """보호 경로 표기(X:\\a\\b·X:/a/b·/x/a/b, 대소문자 무관)와 환경변수. 앞뒤 경계를 둬서 임시 사본(…\\tmp\\…\\b)이나
    접두사만 같은 이름(X:\\bOld)은 보호 데이터로 보지 않는다."""
    alts = []
    for p in PROTECTED:
        m = re.match(r"^([A-Za-z]):[\\/]+(.*)$", p)
        parts = [x for x in re.split(r"[\\/]+", m.group(2)) if x] if m else []
        if not parts:
            continue
        d = m.group(1).lower()
        alts.append("(?<![\\w/\\\\.])(?:" + d + ":[\\\\/]+|/" + d + "/)" + "[\\\\/]+".join(map(re.escape, parts))
                    + "(?=[\\\\/\"'\\s;|&)]|$)")
    if PROTECTED_ENV:
        e = re.escape(PROTECTED_ENV.lower())
        alts += ["\\$\\{?" + e + "\\b\\}?", "\\$env:" + e + "\\b", "%" + e + "%"]
    return re.compile("|".join(alts), re.I) if alts else None


_PROT = _protected_re()
# 인자 어디에든 보호 경로가 나오면 쓰기로 보는 명령(삭제·이동·생성·내용 변경)
_W_ANY = {"rm", "rmdir", "unlink", "shred", "del", "erase", "rd", "mv", "move", "ren", "rename", "touch", "mkdir",
          "md", "truncate", "chmod", "chown", "attrib", "icacls", "tee",
          "remove-item", "ri", "move-item", "mi", "rename-item", "rni", "set-content", "sc", "add-content", "ac",
          "clear-content", "clc", "out-file", "new-item", "ni", "clear-item", "cli", "set-item", "si"}
# 대상(마지막 또는 두 번째 위치 인자)이 보호 경로일 때만 쓰기 — 보호 데이터에서 임시 폴더로 복사는 허용
_W_DEST_LAST = {"cp", "rsync", "ln", "install", "scp"}
_W_DEST_SECOND = {"copy-item", "cpi", "copy", "xcopy", "robocopy"}
_PS_VALUE_PARAMS = {"-path", "-literalpath", "-filter", "-include", "-exclude", "-credential"}
_SHELLS = {"bash", "sh", "powershell", "pwsh", "cmd"}
_CD = {"cd", "chdir", "pushd", "set-location", "sl"}


def _is_prot(s: str) -> bool:
    return bool(_PROT and _PROT.search(s))


def _tokens(seg: str) -> List[str]:
    """따옴표 묶음을 한 토큰으로(따옴표는 벗긴다). 셸 해석기 수준은 아니다."""
    return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', seg)]


def _verb(tok: str) -> str:
    base = re.split(r"[\\/]", tok)[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _is_relative(p: str) -> bool:
    return bool(p) and not re.match(r"^(?:[A-Za-z]:|[\\/~$&%])", p)


def _copy_dest(verb: str, args: List[str]) -> Optional[str]:
    pos: List[str] = []
    i = 0
    while i < len(args):
        a, al = args[i], args[i].lower()
        if len(al) >= 3 and "-destination".startswith(al):          # PowerShell -Destination(약어 -Dest 포함)
            return args[i + 1] if i + 1 < len(args) else None
        if al == "-t" or al == "--target-directory":
            return args[i + 1] if i + 1 < len(args) else None
        if al.startswith("--target-directory="):
            return a.split("=", 1)[1]
        if al in _PS_VALUE_PARAMS:                                   # 값은 원본 쪽이라 대상 후보가 아니다
            i += 2
            continue
        if a.startswith("-") or (verb in _W_DEST_SECOND and re.match(r"^/[A-Za-z?]{1,4}(?::\S*)?$", a)):
            i += 1
            continue
        pos.append(a)
        i += 1
    if verb in _W_DEST_SECOND:
        return pos[1] if len(pos) > 1 else ("." if pos else None)   # 대상 생략 = 현재 폴더
    return pos[-1] if len(pos) >= 2 else None


def _seg_writes_protected(seg: str, in_prot: bool, earlier: bool, depth: int) -> bool:
    masked = mask_quoted_content(seg)
    for m in re.finditer(r">+\|?\s*", masked):                      # 리디렉션 대상(따옴표 밖의 > 만)
        q = re.match(r'"([^"]*)"|\'([^\']*)\'|(\S+)', seg[m.end():])
        target = next((g for g in q.groups() if g is not None), "") if q else ""
        if _is_prot(target) or (in_prot and _is_relative(target)):
            return True
    toks = _tokens(seg)
    if not toks:
        return False
    verb, args = _verb(toks[0]), toks[1:]
    if verb == "xargs":
        rest = [a for a in args if not a.startswith("-")]
        if rest and _verb(rest[0]) in (_W_ANY | _W_DEST_LAST | _W_DEST_SECOND) and earlier:
            return True
        return bool(rest) and _seg_writes_protected(" ".join(rest), in_prot, earlier, depth)
    if verb in _SHELLS and depth < 3:
        for i, a in enumerate(args):
            if a.lower() in ("-c", "-command", "/c", "/k"):
                return protected_write_reason(" ".join(args[i + 1:]), _depth=depth + 1) is not None
    mentions = any(_is_prot(a) for a in args)
    if verb == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args):
        verb = "rm"                                                  # 제자리 수정 = 쓰기
    if verb in _W_ANY:
        return mentions or (in_prot and any(_is_relative(a) for a in args if not a.startswith("-")))
    if verb in _W_DEST_LAST or verb in _W_DEST_SECOND:
        dest = _copy_dest(verb, args)
        return bool(dest) and (_is_prot(dest) or (in_prot and _is_relative(dest)))
    if verb == "find" and mentions:
        low = [a.lower() for a in args]
        if "-delete" in low:
            return True
        for i, a in enumerate(low):
            if a in ("-exec", "-execdir", "-ok") and i + 1 < len(low) and _verb(low[i + 1]) in _W_ANY:
                return True
    if verb == "dd":
        return any(a.lower().startswith("of=") and _is_prot(a) for a in args)
    return False


def protected_write_reason(cmd: str, _depth: int = 0) -> Optional[str]:
    """보호 데이터에 쓰거나 지우는 명령이면 거부 사유, 아니면 None. 누구든(매니저 포함) 적용."""
    if not _PROT:
        return None
    in_prot = False
    earlier = False
    for seg in segments(cmd):
        toks = _tokens(seg)
        if toks and _verb(toks[0]) in _CD:
            dest = next((a for a in toks[1:] if not a.startswith("-")), "")
            if not dest or not _is_relative(dest):                   # 상대 경로 cd는 상태 유지(보수적)
                in_prot = _is_prot(dest)
            earlier = earlier or in_prot
            continue
        if _seg_writes_protected(seg, in_prot, earlier, _depth):
            return (f"보호 데이터({', '.join(PROTECTED + ([f'${PROTECTED_ENV}'] if PROTECTED_ENV else []))})는 읽기 전용이다 — "
                    f"쓰기·삭제·이동 명령은 거부한다. 파괴적 검증은 {TMP_ROOT}\\<작업명>에 복사한 사본에서 하고, "
                    f"남길 결과는 {OUT_DIR}/에 둔다.")
        earlier = earlier or _is_prot(seg)
    return None


def _norm_win(p: str, cwd: str = "") -> Optional[str]:
    """비교용 경로(소문자·역슬래시·.. 정리). `/g/x` → `g:\\x`. 드라이브 없는 절대 경로(`/home/…`, Linux CI)도
    절대 경로로 본다 — 양쪽을 같은 방식으로 정리하므로 비교는 OS와 무관하다. 상대 경로는 cwd 기준, cwd가 없으면 None."""
    p = (p or "").strip().strip("\"'")
    m = re.match(r"^/([A-Za-z])(?=/|$)", p)
    if m:
        p = m.group(1) + ":" + (p[2:] or "/")
    if not re.match(r"^(?:[A-Za-z]:)?[\\/]", p):
        base = _norm_win(cwd) if cwd else None
        if not base or not p:
            return None
        p = ntpath.join(base, p)
    return ntpath.normcase(ntpath.normpath(p))


def _under(p: str, root: str) -> bool:
    r = _norm_win(root) or ""
    return p == r or p.startswith(r.rstrip("\\") + "\\")


def check_file_write(file_path: str, agent_type: Optional[str], cwd: str = "",
                     repo: Optional[str] = None) -> Optional[str]:
    """Write·Edit 도구의 대상 경로 판정. 보호 데이터는 누구든 거부, WORKSPACE_ONLY_ROLES는 메인 저장소 OUT_DIR만."""
    p = _norm_win(file_path, cwd)
    if p and any(_under(p, r) for r in PROTECTED):
        return f"보호 데이터({', '.join(PROTECTED)})는 읽기 전용이다 — Write·Edit로 쓸 수 없다. 사본은 {TMP_ROOT}\\<작업명>에."
    role = (agent_type or "").split(":")[-1].lower()
    if role in WORKSPACE_ONLY_ROLES:
        ws = ntpath.join(repo or REPO, OUT_DIR)
        if not p or not _under(p, ws):
            return (f"{role}는 Write·Edit를 메인 저장소 {OUT_DIR}/ 안에서만 쓴다({ws}) — 저장소 코드·문서 수정은 개발자 몫이다"
                    f"(PROCESS_ROLES 9-3). 절대 경로로 다시 지정할 것. 지워도 되는 임시 파일은 Bash로 {TMP_ROOT}\\<작업명>에.")
    return None


# ───────── 판정 ─────────
def check_command(cmd: str, is_subagent: bool) -> Optional[str]:
    """거부 사유(한국어) 또는 None(통과)."""
    reason = protected_write_reason(cmd)
    if reason:
        return reason
    for seg in segments(cmd):
        call = _python_call(seg)
        if call:
            exe, rest = call
            kind = python_wait_kind(rest)
            if kind == "repl" or (kind == "stdin" and PY_REPL_DENY):
                return ("입력을 기다리는 python 호출(REPL·`python -`·`-i`, 리디렉션·옵션만 붙은 호출 포함)은 금지 — "
                        "에이전트 셸에는 대화형 입력이 없어 멈추거나 폭주한다. 스크립트 파일로 저장해 실행하거나 `-c`를 쓸 것.")
            if SUB_PYTHON_ONLY and is_subagent and not is_venv(exe):
                return (f"서브에이전트는 venv python만 쓴다: {VENV_PY_POSIX} (Git Bash) / {VENV_PY} (PowerShell). "
                        "시스템 python은 매니저 전용.")
        low = mask_quoted_content(seg).lower()
        if re.match(r"^(less|more|most|vi|vim|nano|emacs|notepad(\.exe)?)(\s|$)", low):
            return "페이저·편집기는 대화형 입력이 필요해 쓸 수 없다 — 파일은 Read/grep/head로 보고, 수정은 Edit 도구로."
        if re.match(r"^git(\s+-[cC]\s+\S+)*\s+(rebase|add|clean|checkout|restore|reset|stash)\b.*\s(-i|-p|--interactive|--patch)\b", low):
            return "대화형 git(-i/-p)은 쓸 수 없다 — 비대화형 옵션으로."
        m = re.match(r"^git(\s+-[cC]\s+\S+)*\s+commit\b", low)
        if m and not _GIT_COMMIT_MSG.search(seg[m.end():]):
            return "메시지 없는 `git commit`은 편집기를 띄운다 — `-m` 또는 `-F <파일>`로."
        if re.match(r"^(read-host|get-credential|out-gridview|pause)(\s|$)", low) or re.search(
                r"[(=]\s*read-host(\s|$|\))", low):
            return "PowerShell 대화형 명령(Read-Host·Get-Credential·pause·Out-GridView)은 쓸 수 없다."
    return None


# ───────── 기록 ─────────
def _log(name: str, line: str) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, name), "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S} | {line}\n")
    except OSError:
        pass


def _who(d: dict) -> str:
    return f"{d.get('agent_type')}:{d.get('agent_id')}" if d.get("agent_id") else "main"


# ───────── 모드별 처리 ─────────
def mode_pretool(d: dict) -> Optional[dict]:
    tool = d.get("tool_name", "")
    inp = d.get("tool_input") or {}
    if tool in WRITE_TOOLS:
        cmd = inp.get("file_path") or inp.get("notebook_path") or ""
        reason = check_file_write(cmd, d.get("agent_type") if d.get("agent_id") else None, d.get("cwd") or "")
    elif tool in ("Bash", "PowerShell"):
        cmd = inp.get("command") or ""
        reason = check_command(cmd, bool(d.get("agent_id")))
    else:
        return None
    if not reason:
        return None
    _log("guard.log", f"DENY {_who(d)} {tool} | {reason[:50]} | {cmd[:200]!r}")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "[실행 통제 후크] " + reason}}


_PS_PROBE = ("$ErrorActionPreference='SilentlyContinue';"
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%.exe'\" | ForEach-Object { "
             "'{0}|{1}|{2}' -f $_.ProcessId, [int]((Get-Date)-$_.CreationDate).TotalSeconds, "
             "(\"$($_.CommandLine)\" -replace '[\\r\\n|]+',' ') }")


def find_anomalies(tasks_dir: Optional[str], since_sec: float) -> List[str]:
    """명백한 잔여만: since_sec 안에 생긴 REPL 형태 python, 100 MB 초과·계속 커지는 b*.output."""
    found: List[str] = []
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PROBE],
                                 capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace").stdout
            for line in out.splitlines():
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                pid, age, cmdline = parts
                m = re.match(r'^\s*"?([^"]*?python[0-9.]*(?:\.exe)?)"?\s*(.*)$', cmdline, re.I)
                try:
                    young = int(age) <= since_sec
                except ValueError:
                    young = True
                if m and young and m.group(2).strip() in (("", "-") if PY_REPL_DENY else ("",)):
                    found.append(f"REPL python pid={pid.strip()}: {cmdline.strip()[:120]}")
        except (OSError, subprocess.SubprocessError):
            pass
    if tasks_dir and os.path.isdir(tasks_dir):
        sizes = {}
        for name in os.listdir(tasks_dir):
            if name.startswith("b") and name.endswith(".output"):
                p = os.path.join(tasks_dir, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size > BIG_MB * 1048576 and time.time() - st.st_mtime < since_sec:
                    found.append(f"BIG {name} {st.st_size // 1048576} MB")
                elif time.time() - st.st_mtime < 30:
                    sizes[p] = st.st_size
        if sizes:
            time.sleep(2)
            for p, s0 in sizes.items():
                try:
                    if os.stat(p).st_size > s0 + 4096:
                        found.append(f"GROWING {os.path.basename(p)}")
                except OSError:
                    pass
    return found


def _tasks_dir(d: dict) -> Optional[str]:
    """세션의 tasks 폴더: scratchpad_dir의 형제 → session_id 폴더 → 가장 최근 세션 폴더."""
    sp = d.get("scratchpad_dir") or ""
    if sp:
        cand = os.path.join(os.path.dirname(sp.rstrip("\\/")), "tasks")
        if os.path.isdir(cand):
            return cand
    sid = d.get("session_id") or ""
    if sid and os.path.isdir(os.path.join(TASKS_ROOT, sid, "tasks")):
        return os.path.join(TASKS_ROOT, sid, "tasks")
    try:
        dirs = [os.path.join(TASKS_ROOT, n, "tasks") for n in os.listdir(TASKS_ROOT)]
        dirs = [p for p in dirs if os.path.isdir(p)]
        return max(dirs, key=os.path.getmtime) if dirs else None
    except OSError:
        return None


def _agent_age_sec(d: dict) -> float:
    """이 서브에이전트가 시작한 뒤 경과 시간(초) — 그 뒤에 생긴 프로세스만 잔여 후보로 본다."""
    p = d.get("agent_transcript_path")
    if p and os.path.exists(p):
        try:
            return max(60.0, time.time() - os.stat(p).st_ctime + 5)
        except OSError:
            pass
    return 3 * 3600.0


def running_background(d: dict) -> List[str]:
    """후크 입력 background_tasks 중 아직 돌고 있는 것(형식이 바뀌어도 막지 않도록 보수적으로 해석)."""
    out: List[str] = []
    bt = d.get("background_tasks")
    items = bt if isinstance(bt, list) else (list(bt.values()) if isinstance(bt, dict) else [])
    for t in items:
        if not isinstance(t, dict):
            out.append(str(t)[:120])
            continue
        status = str(t.get("status") or t.get("state") or "").lower()
        if status in ("completed", "done", "finished", "killed", "stopped", "failed", "exited"):
            continue
        tid = t.get("id") or t.get("task_id") or t.get("shell_id") or "?"
        desc = t.get("command") or t.get("description") or ""
        out.append(f"작업 {tid} ({status or '상태 미상'}): {str(desc)[:100]}")
    return out


def mode_substop(d: dict) -> Optional[dict]:
    """종료를 막지 않는다 — 하네스는 종료 시 백그라운드 bash를 끝내지만 그 자식은 고아로 남을 수 있어 기록·경고만 한다."""
    bg = running_background(d)
    anomalies = find_anomalies(_tasks_dir(d), _agent_age_sec(d))
    _log("guard.log", f"SUBSTOP {_who(d)} 실행중_백그라운드={len(bg)} 이상={len(anomalies)}")
    if bg:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            raw = d.get("background_tasks")
            items = raw if isinstance(raw, list) else []
            with open(os.path.join(LOG_DIR, "agent_background.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": f"{_dt.datetime.now():%Y-%m-%d %H:%M:%S}", "agent": _who(d),
                                    "tasks": [{"id": t.get("id"), "command": t.get("command")}
                                              for t in items if isinstance(t, dict)]}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    found = [f"백그라운드 {x}" for x in bg] + anomalies
    if not found:
        return None
    msg = ("[실행 통제 후크] 서브에이전트 " + _who(d) + "가 실행 중인 작업을 남기고 종료: " + "; ".join(found)[:300] +
           " — 자식 프로세스가 고아로 남을 수 있다. `bash .claude/tools/check_leftovers.sh <작업명>`로 확인할 것.")
    _log("guard.log", f"WARN-STOP {_who(d)} | {'; '.join(found)[:300]}")
    return {"systemMessage": msg}


def mode_approval(d: dict) -> Optional[dict]:
    tool = d.get("tool_name", "")
    inp = d.get("tool_input") or {}
    what = inp.get("command") or inp.get("file_path") or json.dumps(inp, ensure_ascii=False)[:150]
    _log("approvals.log", f"REQUEST {_who(d)} {tool} | {str(what)[:200]!r}")
    return None


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except (ValueError, OSError):
        return 0
    try:
        handler = {"pretool": mode_pretool, "substop": mode_substop, "approval": mode_approval}.get(mode)
        result = handler(d) if handler else None
    except Exception:  # 후크 오류는 절대 명령을 막지 않는다
        return 0
    if result:
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
