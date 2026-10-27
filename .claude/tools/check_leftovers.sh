#!/bin/bash
# 잔여 점검(Leftover check) — 서브에이전트 완료 보고 직전과 매니저의 완료 수령 확인에 쓴다. 읽기 전용.
# 배경: 입력 없는 python REPL이 64분간 5.4 GB를 쓰며 살아 있었는데 보고에는 "바로 종료했다"고 적힌 사고가 있었다.
#       자가 보고 대신 이 스크립트 출력으로 확인한다(docs/PROCESS_ROLES.md 9장). FSS-team-bootstrap 생성(2026-10-27).
#
# 사용: check_leftovers.sh <작업명> [--tasks-dir <tasks 폴더>]
#   tasks 폴더 = 관리자 세션의 .../<세션ID>/tasks (작업지시서에 적힌 값). 생략하면 가장 최근 세션 폴더를 추정한다.
#
# 점검 항목(아무것도 죽이거나 지우지 않는다)
#   ① python 프로세스: 명령줄이 이 프로젝트 경로(PROJECT_FILTER)에 맞는 것 → 잔여(LEFTOVER).
#      입력 없는 REPL 형태(python / python - 단독, 스크립트·-c·-m 없음)는 경로와 무관하게 REPL로 표시.
#   ② tasks 폴더의 b*.output: 100 MB 초과(BIG) 또는 최근 60초 안에 크기가 늘어남(GROWING).
#   ④ 임시 폴더 G:\tmp\PowerMate\*<작업명>* 잔존(경고만 — 종료 코드에 반영하지 않음).
# 출력: 항목별 한 줄씩, 마지막 줄 "LEFTOVERS: none" 또는 "LEFTOVERS: <n> found".
# 종료 코드: 0 = 잔여 없음, 1 = 잔여 있음, 2 = 사용법 오류.

PROJECT_FILTER="${PROJECT_FILTER:-PowerMate|[\\\\/]wt-|/g/tmp/PowerMate}"
TMP_ROOT="${TMP_ROOT:-/g/tmp/PowerMate}"
TASKS_ROOT="${TASKS_ROOT:-/c/Users/curta/AppData/Local/Temp/claude/G--workspace-PowerMate}"
BIG_MB="${LEFTOVER_BIG_MB:-100}"

NAME="$1"; shift || true
if [ -z "$NAME" ] || [ "${NAME#-}" != "$NAME" ]; then
  echo "사용: check_leftovers.sh <작업명> [--tasks-dir <tasks 폴더>]"; exit 2
fi
TASKS_DIR="${WATCHER_TASKS_DIR:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --tasks-dir) TASKS_DIR="$2"; shift 2 ;;
    *) echo "알 수 없는 인자: $1"; exit 2 ;;
  esac
done
if [ -z "$TASKS_DIR" ]; then
  TASKS_DIR=$(ls -dt "$TASKS_ROOT"/*/tasks 2>/dev/null | head -n 1)
  [ -n "$TASKS_DIR" ] && echo "INFO tasks 폴더 추정: $TASKS_DIR"
fi
TASKS_DIR="${TASKS_DIR//\\//}"
case "$TASKS_DIR" in [A-Za-z]:/*) TASKS_DIR="/$(echo "${TASKS_DIR:0:1}" | tr 'A-Z' 'a-z')${TASKS_DIR:2}" ;; esac

found=0

# ①·③ 프로세스 목록: PowerShell을 -EncodedCommand로 한 번 호출(인용 문제 회피, PS 코드는 ASCII만).
PS_CODE=$(cat <<'PS'
[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='SilentlyContinue'
Get-CimInstance Win32_Process -Filter "Name like 'python%.exe' OR Name='bash.exe'" | ForEach-Object {
  '{0}|{1}|{2}|{3}' -f $_.Name, $_.ProcessId, [int]((Get-Date)-$_.CreationDate).TotalMinutes, ("$($_.CommandLine)" -replace '[\r\n|]+',' ')
}
'END'
PS
)
PROCS=$(powershell -NoProfile -NonInteractive -EncodedCommand "$(printf '%s' "$PS_CODE" | iconv -f UTF-8 -t UTF-16LE | base64 -w0)" 2>/dev/null | tr -d '\r')
if ! printf '%s\n' "$PROCS" | grep -q '^END$'; then
  echo "WARN 프로세스 조회 실패(PowerShell) — ①·③ 확인 못 함"
fi
REPL_RE='python[0-9.]*(\.exe)?"?[[:space:]]*(-[[:space:]]*)?$'
while IFS='|' read -r pname pid age cmd; do
  [ -z "$pid" ] && continue
  case "$pname" in
    python*)
      if printf '%s' "$cmd" | grep -Eqi "$REPL_RE"; then
        echo "LEFTOVER REPL python pid=$pid ${age}분 경과: $cmd"; found=$((found+1))
      elif printf '%s' "$cmd" | grep -Eqi "$PROJECT_FILTER"; then
        echo "LEFTOVER python pid=$pid ${age}분 경과: $cmd"; found=$((found+1))
      fi ;;
  esac
done < <(printf '%s\n' "$PROCS" | grep -v '^END$')

# ② tasks 폴더 b*.output
if [ -n "$TASKS_DIR" ] && [ -d "$TASKS_DIR" ]; then
  now=$(date +%s)
  for f in "$TASKS_DIR"/b*.output; do
    [ -e "$f" ] || continue
    size=$(stat -c %s "$f" 2>/dev/null || echo 0); mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    mb=$((size / 1048576))
    if [ "$mb" -gt "$BIG_MB" ]; then
      echo "LEFTOVER BIG $(basename "$f") ${mb} MB (마지막 기록 $(date -d "@$mt" '+%H:%M:%S'))"; found=$((found+1))
    elif [ $((now - mt)) -lt 60 ]; then
      s1=$size; sleep 3; s2=$(stat -c %s "$f" 2>/dev/null || echo 0)
      if [ "$s2" -gt "$s1" ]; then
        echo "LEFTOVER GROWING $(basename "$f") ${mb} MB (3초에 $((s2 - s1)) B 증가)"; found=$((found+1))
      fi
    fi
  done
else
  echo "WARN tasks 폴더 없음 — ② 확인 못 함 (--tasks-dir 지정 권장)"
fi

# ⑤ 서브에이전트가 남긴 백그라운드 명령의 고아 프로세스(hook_guard.py substop 기록 기준)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORPHAN_PY=""   # PYTHON(또는 예전 VENV_PY) env → 설정한 python → PATH의 python3·python 순
for c in "${PYTHON:-}" "${VENV_PY:-}" "/c/Python314/python" "/c/Python314/python" python3 python; do
  [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && { ORPHAN_PY="$c"; break; }
done
if [ ! -f "$HERE/leftover_orphans.py" ]; then
  :
elif [ -z "$ORPHAN_PY" ]; then
  echo "WARN python을 찾지 못함 — ⑤ 확인 못 함 (PYTHON=<python 경로>로 지정)"
else
  VENV_PY="$ORPHAN_PY"
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    echo "$line"
    case "$line" in LEFTOVER*) found=$((found+1)) ;; esac
  done < <("$VENV_PY" "$HERE/leftover_orphans.py" 2>/dev/null | tr -d '\r')
fi

# ④ 임시 폴더(경고만)
for d in "$TMP_ROOT"/*"$NAME"*; do
  [ -e "$d" ] && echo "WARN 임시 폴더 남음: $d (작업 끝나면 정리)"
done

if [ "$found" -eq 0 ]; then echo "LEFTOVERS: none"; exit 0; fi
echo "LEFTOVERS: $found found"; exit 1
