export const meta = {
  name: 'carve-q1q6-missing-pieces',
  description: 'Sequentially carve the ten uncarved Q1/Q6 pieces from aocsa/feat/pin-table-cn into demo/q1q6-integration: carve, read-only review, fix+commit per piece',
  phases: [
    { title: 'Carve', detail: 'apply the piece hunks, build, test; no commit' },
    { title: 'Review', detail: 'read-only review of the uncommitted diff against SOT and the spec' },
    { title: 'Commit', detail: 'apply review fixes, re-verify, one commit' },
  ],
}
const SP = '/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad'
const COMMON = `${SP}/carve-specs/00-common.md`
const PIECES = (args && args.pieces) || [
  { id: 'F4', spec: '01-F4.md' }, { id: 'F5', spec: '02-F5.md' }, { id: 'T6', spec: '03-T6.md' },
  { id: 'C2a', spec: '04-C2a.md' }, { id: 'C2b', spec: '05-C2b.md' }, { id: 'C5', spec: '06-C5.md' },
  { id: 'C6a', spec: '07-C6a.md' }, { id: 'C6b', spec: '08-C6b.md' }, { id: 'C7', spec: '09-C7.md' },
  { id: 'bench', spec: '10-bench.md' },
]
const CARVE_SCHEMA = { type: 'object', required: ['ok', 'summary', 'files', 'verification', 'drift', 'open_issues'], properties: {
  ok: { type: 'boolean' }, summary: { type: 'string' },
  files: { type: 'array', items: { type: 'string' } },
  verification: { type: 'array', items: { type: 'object', required: ['command', 'result'], properties: { command: { type: 'string' }, result: { type: 'string' } } } },
  drift: { type: 'array', items: { type: 'string' } }, open_issues: { type: 'array', items: { type: 'string' } } } }
const REVIEW_SCHEMA = { type: 'object', required: ['verdict', 'findings'], properties: {
  verdict: { type: 'string', enum: ['approve', 'fix-required'] },
  findings: { type: 'array', items: { type: 'object', required: ['severity', 'file', 'summary'], properties: { severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, line: { type: 'integer' }, summary: { type: 'string' }, fix: { type: 'string' } } } } } }
const FIX_SCHEMA = { type: 'object', required: ['ok', 'commit', 'title', 'summary', 'verification', 'drift', 'open_issues'], properties: {
  ok: { type: 'boolean' }, commit: { type: 'string' }, title: { type: 'string' }, summary: { type: 'string' },
  verification: { type: 'array', items: { type: 'object', required: ['command', 'result'], properties: { command: { type: 'string' }, result: { type: 'string' } } } },
  drift: { type: 'array', items: { type: 'string' } }, open_issues: { type: 'array', items: { type: 'string' } } } }

const preamble = (p) => `You are one stage of a sequential carve of piece ${p.id}. Read these two files completely before doing anything else:
1. ${COMMON}  (rules, commands, exclusions)
2. ${SP}/carve-specs/${p.spec}  (this piece)
Then read the plan sections and appendix line ranges the piece spec names. Work only in /home/prestouser/aocsa/sirius-stacks-wt/demo. Use Bash for git/build/test, and prefer reading files with sed/grep over dumping whole files into your context (compute_node_service.rs is ~8000 lines on SOT). Keep build logs in files under ${SP}/ and tail them.`

const results = []
for (const p of PIECES) {
  log(`piece ${p.id}: carve`)
  const carve = await agent(`${preamble(p)}

STAGE: CARVE. Apply exactly this piece's hunks from SOT into the demo worktree, make it compile, run the verification list in the spec, and STOP WITHOUT COMMITTING (leave the changes in the working tree; do not stage them either). If a required test cannot pass without touching something outside the piece, do the minimal fix and record it as drift. If you cannot complete the piece, set ok=false and explain precisely what is left and why; still leave the tree compiling if at all possible. Before returning, run 'git status --short' and make sure only files this piece owns are modified/added, and 'grep -rn "<<<<<<<" <changed files>' is empty. Return the structured result: ok, summary (5-10 sentences), files (paths touched), verification (each command you ran with its result, including test counts), drift (each deliberate deviation from SOT text with a one-line why), open_issues.`, { label: `carve:${p.id}`, phase: 'Carve', schema: CARVE_SCHEMA })
  if (!carve) { results.push({ id: p.id, stage: 'carve', ok: false, error: 'agent returned null' }); break }

  log(`piece ${p.id}: review`)
  const review = await agent(`${preamble(p)}

STAGE: REVIEW (read-only: you may run git diff/show/grep/sed and read files, but you must not modify any file, run builds, or run tests). The carve stage reports: ${JSON.stringify(carve)}.
Review the uncommitted working-tree diff ('git -C /home/prestouser/aocsa/sirius-stacks-wt/demo diff' plus 'git -C ... status --short' for new files) against (a) the piece spec's take/exclude lists, (b) SOT's text for the same hunks ('git diff HEAD aocsa/feat/pin-table-cn -- <file>' shows what is still missing or different), and (c) the global exclusions. Look specifically for: hunks taken that belong to an excluded or later piece; hunks the spec assigns that are missing; deviations from SOT text not listed in the carve's drift list; conflict markers, leftover shims, dead code, TODOs, commented-out code; test cases the spec names that are absent; wrong feature gates (cfg on a feature that does not exist yet); files outside the piece touched; anything that would fail 'cargo clippy -D warnings' or clang-format. Do not nitpick SOT's own style. Return verdict 'approve' or 'fix-required' with findings (severity blocker/major/minor, file, line if known, summary, concrete fix). Minor-only findings still return 'approve' but list them.`, { label: `review:${p.id}`, phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high' })

  log(`piece ${p.id}: fix and commit (${review ? review.verdict : 'no review'})`)
  const fix = await agent(`${preamble(p)}

STAGE: FIX AND COMMIT. Carve stage report: ${JSON.stringify(carve)}. Review stage report: ${JSON.stringify(review)}.
1. Apply every blocker and major finding; apply minor findings when they are cheap and clearly correct; otherwise record them as open_issues. If a finding is wrong, say why in open_issues and skip it.
2. Re-run the verification commands the spec lists that are affected by your changes (at minimum: the CI-style build/lint/test for the crate or the C++ target you touched; GPU tests you changed). If the carve stage reported a failing verification, it must pass now.
3. Format: cargo fmt for Rust; pre-commit run --files for C++/docs (see common rules). Stage only the files this piece owns; check 'git status --short' and 'git diff --cached --stat'.
4. Commit exactly once with the spec's title and a 3-10 line plain-prose body naming the piece id and its future branch, what is deliberately left out and which later piece brings it, and the drift items; end with the Co-Authored-By trailer from the common rules. Then 'git log -1 --format=%H' and 'git status --short' (must be clean apart from untracked build dirs).
Return ok, commit (full SHA), title, summary, verification (commands + results with counts), drift (final list), open_issues.`, { label: `fix:${p.id}`, phase: 'Commit', schema: FIX_SCHEMA })

  results.push({ id: p.id, carve, review, fix })
  if (!fix || !fix.ok) { log(`piece ${p.id} did not complete; stopping the chain`); break }
  log(`piece ${p.id} committed ${fix.commit}`)
}
return results
