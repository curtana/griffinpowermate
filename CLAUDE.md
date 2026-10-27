# PowerMate

Griffin PowerMate USB 노브를 Windows 11에서 쓰는 Python 컨트롤러(제스처·앱별 프로필·LED·OSD)

## 일하는 방식
- 이 저장소는 **매니저(메인 세션) + 서브에이전트 팀** 구조로 개발한다. 역할·절차·규칙의 정본은 [docs/PROCESS_ROLES.md](docs/PROCESS_ROLES.md)다 — 작업을 시작하기 전에 0~12장을 읽는다.
- 에이전트 정의: `.claude/agents/`, 실행 통제 후크·잔여 점검: `.claude/tools/`, 반복 워크플로: `.claude/workflows/dev-verify.js`.
- 테스트: `python _test_gesture.py && python _test_features.py && python _smoketest_gui.py 2>&1 | tail -n 30`
- 브랜치: `feature/<이슈>-<주제>` → develop → main
- 임시 파일: `G:\tmp\PowerMate\<작업명>`(지워도 되는 것만), 남길 결과: `_workspace/`(git 제외)
