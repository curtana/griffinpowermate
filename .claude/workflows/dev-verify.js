export const meta = {
  name: 'dev-verify',
  description: '이슈 하나를 개발자에게 맡기고, 코드 검수(와 골든 검증)를 병렬로 돌려 통과할 때까지(최대 3회) 반복한다',
  phases: [
    { title: 'Implement' },
    { title: 'Verify' },
  ],
}

// 사용법: Workflow({ name: 'dev-verify', args: { issue: 12 } })
// 작업지시서는 매니저가 이슈 코멘트로 미리 올려 둔 상태를 전제로 한다 — 이 워크플로는 "구현+검증 반복"만 한다.
// developer가 구현→PR을 만들면 reviewer(코드 품질)와 golden-verifier(회귀, lean — 있을 때)를 동시에 돌린다.
// 어느 쪽이든 실패하면 developer에게 재작업을 맡기고 다시 검증한다. 최대 3회(3회 규칙), 그래도 안 되면
// 실패로 반환한다 — 매니저가 오너와 협의한다.
// 조기 에스컬레이션(PROCESS_ROLES 3장·11-4): 다음이면 3회를 기다리지 않고 재작업 없이 반환한다(escalated에 사유).
//   owner    실패 책임(owner)이 manager(지시서·설계 결함) 또는 golden(기대값·환경)
//   repeat   직전 라운드와 같은 실패 ID(테스트ID·검수 지적 위치)가 다시 나옴
//   no_diff  재작업 뒤 커밋이 그대로이거나 직전 커밋 대비 diff가 비어 있음
// 3회째 검증에서는 '기준 자체 재검토'(criteria_review)를 받는다.
// args.golden === false 이면 골든 검증을 뺀다(결과 무변경 작업을 묶음으로 나눌 때는 단계 마무리에 한 번만).
// 생성: FSS-team-bootstrap 2026-10-27

if (!args || !args.issue) {
  throw new Error("args.issue (이슈 번호, 예: {issue: 12}) 가 필요하다")
}

const ISSUE = args.issue
const HAS_GOLDEN = false
const WITH_GOLDEN = HAS_GOLDEN && args.golden !== false
const MAX_ROUNDS = 3

const DEV_SCHEMA = {
  type: 'object',
  properties: {
    pr_number: { type: 'number' },
    branch: { type: 'string' },
    worktree: { type: 'string' },
    commit: { type: 'string' },
    summary: { type: 'string' },
    rework_diff_stat: { type: 'string' },   // 재작업 때만: `git diff --stat <직전 커밋> HEAD` 출력(비면 '')
  },
  required: ['pr_number', 'branch', 'worktree', 'commit', 'summary'],
}

// 실패 보고 공통 필드(PROCESS_ROLES 11-4·3장). 통과면 owner·failure_ids는 비워 둔다.
const FAILURE_FIELDS = {
  owner: { type: 'string', enum: ['developer', 'manager', 'golden'] },
  failure_ids: { type: 'array', items: { type: 'string' } },
  criteria_review: { type: 'string' },
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'conditional', 'reject'] },
    findings: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    ...FAILURE_FIELDS,
  },
  required: ['verdict', 'summary'],
}

const GOLDEN_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail', 'blocked'] },
    summary: { type: 'string' },
    ...FAILURE_FIELDS,
  },
  required: ['verdict', 'summary'],
}

function failurePrompt(idHint, finalRound) {
  return ` 판정이 통과가 아니면 owner와 failure_ids를 채워라(PROCESS_ROLES 11-4). owner: developer(구현 결함 — ` +
    `지시서대로 고치면 풀림) / manager(지시서·설계 결함 — 지시서대로 하면 이 실패가 남: 모순·DoD 달성 불가·범위 밖 결정 필요) / ` +
    `golden(기대값·환경 — 코드와 무관하게 실패: 기대값·데이터·실행 환경·CI 문제, 기준 커밋에서도 같은 실패). 섞이면 ` +
    `developer가 아닌 쪽을 적고 근거(파일:줄·이슈 결정·기준 커밋 재현)를 summary에 붙여라. failure_ids: ${idHint} — ` +
    `라운드끼리 같은 실패인지 이 값으로 비교하니 같은 실패는 같은 표기로 적어라.` +
    (finalRound
      ? ` 이번이 3회째 검증이다. 통과가 아니면 criteria_review에 '기준 자체 재검토'를 적어라 — 지시서 DoD가 달성 ` +
        `가능한가, 검수 기준·기대값이 이번 변경 의도와 맞는가, 환경 전제가 맞는가. 근거를 대고 모르면 ` +
        `"미확인". 기준을 바꾸자는 결론은 제안일 뿐이며 통과 판정의 근거로 쓰지 마라.`
      : '')
}

function devPrompt(mode, extra) {
  const base = `이슈 #${ISSUE}를 구현해라. \`gh issue view ${ISSUE} --comments\`로 ` +
    `본문·최신 작업지시서를 읽고 그대로 따라라. develop을 베이스로 새 워크트리·브랜치` +
    `(\`feature/${ISSUE}-<주제>\`)를 만들어 작업해라. 완료되면 커밋하고 push한 뒤 ` +
    `develop 대상 PR을 만들어라(머지는 하지 마라). **이 실행에서는 검수자·골든 검증자를 ` +
    `네가 직접 부르지 마라** — 이 워크플로가 구현 직후 검증을 돌리고 결과를 다시 너에게 준다. ` +
    `PR 번호·브랜치명·워크트리 절대경로·최신 커밋 해시·요약을 구조화해 반환해라. 3회 규칙은 이 워크플로가 ` +
    `관리한다. 실행 규칙(PROCESS_ROLES 9장): REPL·\`python -\`·대화형 명령 금지(후크가 거부), 장시간 명령은 ` +
    `timeout으로, 완료 보고에 \`bash .claude/tools/check_leftovers.sh issue${ISSUE}\` 출력을 붙여라.`
  if (mode === 'rework') {
    return base + `\n\n이전 라운드 검증 결과: ${extra.results}\n지적된 문제를 고쳐서 같은 워크트리·브랜치에 ` +
      `커밋·push해라(새 PR을 만들지 말고 기존 PR에 반영). 지시서·설계 문서를 처음부터 다시 읽지 말고 지적 사항과 바뀐 파일만 확인해라. ` +
      `반환할 때 rework_diff_stat에 \`git diff --stat ${extra.prevCommit} HEAD\` 출력을 그대로 넣어라(바뀐 것이 없으면 빈 문자열). ` +
      `지적이 지시서 자체의 결함이라 지시서대로는 고칠 수 없다고 판단되면 고치지 말고 커밋 없이 summary 첫머리에 "owner: manager"와 근거를 적어 반환해라.`
  }
  return base
}

phase('Implement')
let dev = await agent(devPrompt('initial'), {
  agentType: 'developer', schema: DEV_SCHEMA, phase: 'Implement', label: `이슈#${ISSUE} 구현`,
})

let round = 1
let passed = false
let lastReview = null
let lastGolden = null
let escalated = null
let prevIds = new Set()

while (dev && round <= MAX_ROUNDS && !passed) {
  phase('Verify')
  log(`검증 ${round}회차 — PR #${dev.pr_number}` + (WITH_GOLDEN ? ' (검수·골든 동시)' : ' (검수만)'))

  const checks = [
    () => agent(
      `PR #${dev.pr_number}(이슈 #${ISSUE})를 검수해라. \`gh pr diff ${dev.pr_number}\`로 diff를 읽고, ` +
      `워크트리 ${dev.worktree}(커밋 ${dev.commit})에서 코드를 직접 확인해라. 전체 테스트는 재실행하지 말고 ` +
      `\`gh pr checks ${dev.pr_number}\`로 CI 결과를 확인한 뒤 바뀐 모듈의 테스트만 직접 실행해라. ` +
      `골든 검증자는 부르지 마라(필요하면 이 워크플로가 따로 돌린다) — 그것을 이유로 conditional·reject를 내지 마라. ` +
      `판정(verdict: pass/conditional/reject)과 발견 사항·요약을 구조화해 반환하고 \`gh pr comment ${dev.pr_number}\`로도 기록해라.` +
      failurePrompt('막는 지적(치명·중요)마다 `경로::함수명`(함수 밖이면 `경로`) — 줄 번호는 재작업으로 바뀌니 쓰지 마라', round === MAX_ROUNDS),
      { agentType: 'reviewer', schema: REVIEW_SCHEMA, phase: 'Verify', label: `검수 r${round}` }
    ),
  ]
  if (WITH_GOLDEN) checks.push(
    () => agent(
      `이슈 #${ISSUE} / PR #${dev.pr_number}(워크트리 ${dev.worktree}, 커밋 ${dev.commit})와 관련된 골든 검증(lean)을 ` +
      `돌려라. \`docs/PROCESS_ROLES.md\` 부록 G의 대표 데이터 중 이 변경이 건드린 모듈에 해당하는 것만 골라라` +
      `(모르겠으면 PR diff를 먼저 읽어라). 요청자 식별자는 \`workflow-issue${ISSUE}-r${round}\`. ` +
      `판정(verdict: pass/fail/blocked)과 요약을 구조화해 반환해라.` +
      failurePrompt('실패한 테스트마다 테스트ID(`모듈.클래스.메서드`, subTest면 뒤에 `[인자]`)', round === MAX_ROUNDS),
      { agentType: 'golden-verifier', schema: GOLDEN_SCHEMA, phase: 'Verify', label: `골든 r${round}` }
    ),
  )
  const results = await parallel(checks)

  const review = results[0]
  const golden = WITH_GOLDEN ? results[1] : null
  lastReview = review
  lastGolden = golden

  const reviewOk = !!review && review.verdict === 'pass'
  const goldenOk = !WITH_GOLDEN || (!!golden && golden.verdict !== 'fail')
  log(`r${round} 결과 — 검수: ${review ? review.verdict : '응답없음'}, ` +
    `골든: ${WITH_GOLDEN ? (golden ? golden.verdict : '응답없음') : '생략'}`)

  if (reviewOk && goldenOk) {
    passed = true
    break
  }

  // 조기 에스컬레이션 판정(3장) — 개발자 재작업으로 풀리지 않는 실패는 3회를 기다리지 않는다.
  const failing = [reviewOk ? null : review, goldenOk ? null : golden].filter(Boolean)
  const nonDev = failing.map(f => f.owner).filter(o => o === 'manager' || o === 'golden')
  if (nonDev.length) {
    escalated = { reason: 'owner', owner: nonDev.includes('manager') ? 'manager' : 'golden', round }
    break
  }
  const ids = new Set(failing.flatMap(f => f.failure_ids || []))
  const repeated = [...ids].filter(id => prevIds.has(id))
  if (repeated.length) {
    escalated = { reason: 'repeat', failure_ids: repeated, round }
    break
  }
  prevIds = ids
  if (round === MAX_ROUNDS) break

  phase('Implement')
  const prevCommit = dev.commit
  dev = await agent(
    devPrompt('rework', { results: JSON.stringify({ review, golden }), prevCommit }),
    { agentType: 'developer', schema: DEV_SCHEMA, phase: 'Implement', label: `이슈#${ISSUE} 재작업 r${round}` }
  )
  if (dev && (dev.commit === prevCommit || (typeof dev.rework_diff_stat === 'string' && !dev.rework_diff_stat.trim()))) {
    escalated = { reason: 'no_diff', round, developer_summary: dev.summary }
    break
  }
  round++
}

const criteriaReview = [lastReview, lastGolden]
  .filter(r => r && r.criteria_review).map(r => r.criteria_review)
const ESCALATE_NOTE = {
  owner: '실패 책임이 개발자가 아님(owner) — 재작업 없이 매니저가 지시서·기대값·환경을 판단',
  repeat: '같은 실패가 2회 연속 — 같은 방식의 재작업으로는 풀리지 않는다고 보고 매니저에게 올림',
  no_diff: '재작업 전후 diff가 그대로 — 개발자가 고치지 못했거나 지시서 결함으로 판단',
}

return {
  issue: ISSUE,
  pr_number: dev ? dev.pr_number : null,
  branch: dev ? dev.branch : null,
  passed,
  rounds: round,
  last_review: lastReview,
  last_golden: lastGolden,
  golden_skipped: !WITH_GOLDEN,
  escalated,
  criteria_review: criteriaReview.length ? criteriaReview : null,
  note: passed
    ? '검증 통과 — 매니저가 머지 여부만 결정하면 됨'
    : escalated
      ? `${round}회차에서 조기 에스컬레이션 — ${ESCALATE_NOTE[escalated.reason]}. 매니저가 회차 코멘트에 사유를 남기고 ` +
        `지시서 수정 또는 오너 협의로 넘긴다(3장). 보고에 '기준 자체 재검토' 항목을 넣는다.`
      : `${MAX_ROUNDS}회 시도 후 미통과 — 3회 규칙에 따라 오너 협의 필요(criteria_review의 '기준 자체 재검토' 포함)`,
}
