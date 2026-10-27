# -*- coding: utf-8 -*-
"""
권한 표류 점검 — .claude/settings.local.json의 allow 규칙을 1층(사용자 전역)·2층(.claude/settings.json)과
대조해 정리 후보를 보고만 한다. 아무 파일도 바꾸지 않는다(지우기는 사람이 결정).

사용: python .claude/tools/check_permissions.py [--repo <저장소 루트>] [--user-settings <경로>] [--python-repl-allowed]

python REPL 금지 여부(python_repl_deny): --python-repl-allowed > <repo>/.claude/team.config.json > 렌더 값(기본 true).
false(프로젝트 예외)면 python REPL deny와의 충돌은 보고하지 않고, deny에 남은 그 규칙을 참고로 알린다.

보고 범주
- 겹침: 1층·2층 규칙이 이미 덮는 규칙(이제 불필요)
- 일회성: 커밋 메시지 원문, scratchpad·임시 경로, 존재하지 않는 절대 경로, 지나치게 긴 원문
- 정책 충돌: deny에 있는 명령을 allow도 하고 있는 규칙(예: python REPL 금지인데 `python -` 허용)
출력 마지막 줄: PERMCHECK: 겹침 <n>, 일회성 <n>, 충돌 <n> (전체 <n>), 종료 코드 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

RULE = re.compile(r"^(\w+)\((.*)\)$", re.S)
ONE_OFF = re.compile(r"scratchpad|/Temp/claude/|\\Temp\\claude\\|__TRACKED_VAR__|-m '|-m \"|>> |\$\(date")
ABS_PATH = re.compile(r"(?<![\w:/])(?:[A-Za-z]:[\\/]|/[a-z]/)[^\s\"')*;&|]+")   # URL(http://…)은 제외
PY_REPL_DENY = "true" != "false"   # 렌더 전(스킬 원본 직접 실행)이면 기본 true


def is_python_repl_rule(rule: str) -> bool:
    """후크 설치가 넣는 python REPL deny 모양: python·python -, & C:\\...\\python.exe -."""
    m = re.fullmatch(r"(?:&\s+)?(\S+?)(?:\s+-)?", split(rule)[1].strip())
    return bool(m) and bool(re.fullmatch(r"(python[0-9.]*|py)(\.exe)?", re.split(r"[\\/]", m.group(1))[-1], re.I))


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def split(rule: str):
    m = RULE.match(rule)
    return (m.group(1), m.group(2)) if m else (rule, "")


def covers(base: str, rule: str) -> bool:
    bt, ba = split(base)
    rt, ra = split(rule)
    if bt != rt:
        return False
    if ba == ra:
        return True
    if ba.endswith(" *") and not re.search(r"[;&|`]", ra):
        return ra == ba[:-2] or ra.startswith(ba[:-1])
    return False


def to_win(p: str) -> str:
    m = re.match(r"^/([a-z])/(.*)$", p)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else p


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    ap.add_argument("--user-settings", default=os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
    ap.add_argument("--python-repl-allowed", action="store_true",
                    help="이 프로젝트는 `python -`를 허용한다(python_repl_deny=false)")
    a = ap.parse_args()
    repl_deny = load(os.path.join(a.repo, ".claude", "team.config.json")).get("python_repl_deny", PY_REPL_DENY)
    repl_deny = bool(repl_deny) and not a.python_repl_allowed
    user = load(a.user_settings).get("permissions", {})
    shared = load(os.path.join(a.repo, ".claude", "settings.json")).get("permissions", {})
    local = load(os.path.join(a.repo, ".claude", "settings.local.json")).get("permissions", {})
    bases = [("1층", r) for r in user.get("allow", [])] + [("2층", r) for r in shared.get("allow", [])]
    denies = user.get("deny", []) + shared.get("deny", []) + local.get("deny", [])
    stale = []
    if not repl_deny:            # 예외 프로젝트: python REPL deny와는 충돌로 보지 않는다
        stale = [d for d in denies if is_python_repl_rule(d)]
        denies = [d for d in denies if not is_python_repl_rule(d)]
    allow = local.get("allow", [])
    found = {"겹침": [], "일회성": [], "충돌": []}
    for r in allow:
        hit = next((f"{lvl} {b}" for lvl, b in bases if covers(b, r)), None)
        if hit:
            found["겹침"].append(f"{r}  ← {hit}")
            continue
        clash = next((d for d in denies if covers(d, r) or split(d)[1] == split(r)[1]), None)
        if clash:
            found["충돌"].append(f"{r}  ← deny {clash}")
            continue
        _, arg = split(r)
        gone = [p for p in ABS_PATH.findall(arg) if "*" not in p
                and not any(os.path.exists(to_win(p) + ext) for ext in ("", ".exe", ".cmd", ".bat"))]
        if ONE_OFF.search(arg) or len(arg) > 120 or gone:
            why = f"없는 경로 {gone[0]}" if gone else ("긴 원문" if len(arg) > 120 else "일회성 모양")
            found["일회성"].append(f"{r[:140]}  ← {why}")
    for k, items in found.items():
        print(f"## {k} {len(items)}건")
        for x in items:
            print(f"- {x}")
    if stale:
        print(f"참고: python_repl_deny=false인데 deny에 python REPL 규칙 {len(stale)}건이 남아 `python -`를 계속 막는다"
              f" — {', '.join(stale[:4])}{' …' if len(stale) > 4 else ''}")
    print(f"PERMCHECK: 겹침 {len(found['겹침'])}, 일회성 {len(found['일회성'])}, 충돌 {len(found['충돌'])}"
          f" (전체 {len(allow)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
