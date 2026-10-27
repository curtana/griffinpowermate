# -*- coding: utf-8 -*-
"""
.claude/tools/leftover_orphans.py — check_leftovers.sh ⑤: 서브에이전트가 남긴 백그라운드 명령의 고아 프로세스 찾기(읽기 전용).

근거: 하네스는 서브에이전트가 끝날 때 그 백그라운드 작업의 bash를 [killed]로 끝내지만, bash 안에서 띄운 자식
(sleep.exe·python.exe 등)은 고아로 살아남을 수 있다. hook_guard.py substop이 남긴 명령을
`_workspace/hooks/agent_background.jsonl`에 적어 두므로, 최근 기록의 명령과 명령줄이 맞는 살아 있는 프로세스를 찾는다.

출력: 찾은 것마다 `LEFTOVER ORPHAN pid=… <이름> …분 경과 (에이전트 …, 작업 …): <명령줄>` 한 줄. 아무것도 죽이지 않는다.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys



def main_repo(path: str) -> str:
    """워크트리(.claude/worktrees/…)에서 돌아도 후크 기록이 있는 메인 체크아웃을 가리킨다(git common dir의 부모)."""
    try:
        out = subprocess.run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out and os.path.basename(out.rstrip("/\\")) == ".git":
            return os.path.dirname(os.path.normpath(out))
    except (OSError, subprocess.SubprocessError):
        pass
    return path


REPO = os.environ.get("TEAM_REPO") or main_repo(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOG = os.path.join(REPO, "_workspace", "hooks", "agent_background.jsonl")
WINDOW_H = float(os.environ.get("ORPHAN_WINDOW_HOURS", "12"))

PS = ("$ErrorActionPreference='SilentlyContinue';"
      "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | ForEach-Object { "
      "'{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.Name, [int]((Get-Date)-$_.CreationDate).TotalMinutes, "
      "(\"$($_.CommandLine)\" -replace '[\\r\\n|]+',' ') }")


def last_segment(cmd: str) -> str:
    segs = [s.strip() for s in re.split(r"&&|\|\||;|\n", cmd or "") if s.strip()]
    return segs[-1] if segs else ""


def matcher(cmd: str):
    """(실행 파일 기본 이름, 인자 문자열) — 명령의 마지막 단위 명령 기준."""
    seg = last_segment(cmd)
    parts = seg.split(None, 1)
    if not parts:
        return None
    exe = re.split(r"[\\/]", parts[0].strip('"'))[-1].lower()
    exe = exe[:-4] if exe.endswith(".exe") else exe
    args = parts[1].strip() if len(parts) > 1 else ""
    return exe, args


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if not os.path.exists(LOG):
        return 0
    cutoff = dt.datetime.now() - dt.timedelta(hours=WINDOW_H)
    wanted = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                t = dt.datetime.strptime(rec["time"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            if t < cutoff:
                continue
            age_min = (dt.datetime.now() - t).total_seconds() / 60
            for task in rec.get("tasks") or []:
                m = matcher(task.get("command") or "")
                if m and m[0] not in ("bash", "sh", "cd", "echo"):
                    wanted.append((m, rec.get("agent"), task.get("id"), age_min))
    if not wanted:
        return 0
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], capture_output=True,
                             text=True, timeout=20, encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        print("WARN 프로세스 조회 실패 — ⑤ 확인 못 함")
        return 0
    for line in out.splitlines():
        p = line.split("|", 3)
        if len(p) != 4:
            continue
        pid, name, age, cl = p
        base = name.lower()[:-4] if name.lower().endswith(".exe") else name.lower()
        for (exe, args), agent, tid, rec_age in wanted:
            try:
                started_before_record = int(age) > rec_age + 120   # 기록보다 훨씬 전에 시작한 건 다른 것
            except ValueError:
                started_before_record = False
            if base == exe and (not args or args.replace('"', "") in cl.replace('"', "")) and not started_before_record:
                print(f"LEFTOVER ORPHAN pid={pid} {name} {age}분 경과 (에이전트 {agent}, 작업 {tid}): {cl[:160]}")
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
