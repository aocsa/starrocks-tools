export const meta = {
  name: 'sf1000-top4-implement',
  description: 'Implement the four SF1000 fixes from their specs, one worktree each: implementer (tests first), adversarial reviewer, fixer, independent verifier',
  phases: [
    { title: 'Implement', detail: 'one implementer per fix in its worktree' },
    { title: 'Review', detail: 'adversarial review of the diff' },
    { title: 'Fix', detail: 'apply review findings' },
    { title: 'Verify', detail: 'fresh build, tests, lint, commit hygiene' },
  ],
}
const SP = '/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad'
const D = SP + '/fix/designs'; const WTS = '/home/prestouser/aocsa/sirius-stacks-wt'; const CLONE = '/home/prestouser/aocsa/sirius-stacks'
const PLAN = '/home/prestouser/.claude/plans/sf1000-top4-fixes-plan.md'
const FIXES = (args && args.fixes) || [
  { key: 'oom-failfast', wt: 'fix-oom-failfast', branch: 'fix/oom-failfast', gpu: '0', layer: 'engine (src/, Catch2 tests under test/cpp/)' },
  { key: 'parked-bookkeeping', wt: 'fix-parked-bookkeeping', branch: 'fix/parked-bookkeeping', gpu: '1', layer: 'CN (experimental/starrocks/src, Rust tests)' },
  { key: 'files-cardinality', wt: 'fix-files-cardinality', branch: 'fix/files-cardinality', gpu: 'none', layer: 'StarRocks FE patch (experimental/starrocks/patches/*.patch applied to the submodule) plus CN/proto if the spec says so' },
  { key: 'fragment-fusion', wt: 'fix-fragment-fusion', branch: 'fix/fragment-fusion', gpu: '2', layer: 'CN and/or engine per the spec' },
]
const RULES = (f) => `HOUSE RULES (non-negotiable):
- Work ONLY inside the worktree ${WTS}/${f.wt} (branch ${f.branch}, based on perf/profile-sf1000). Never touch /home/prestouser/aocsa/sirius, never touch other worktrees, never git stash, never push, never change global git config, never amend or rewrite existing commits.
- Build/test commands run through pixi with the clone's manifests: engine: pixi run --manifest-path ${CLONE}/pixi.toml bash -c "cd ${WTS}/${f.wt} && make release" (ccache; minutes) and Catch2: pixi run --manifest-path ${CLONE}/pixi.toml bash -c "cd ${WTS}/${f.wt} && build/release/extension/sirius/test/cpp/sirius_unittest '[tag]'" with CUDA_VISIBLE_DEVICES=${f.gpu} when a GPU is needed (GPU ${f.gpu} is yours; ${f.gpu === 'none' ? 'this fix needs no GPU' : 'no other GPU'}). CN: env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path ${CLONE}/experimental/starrocks/pixi.toml -e cn bash ${SP}/cn-build.sh ${WTS}/${f.wt}/experimental/starrocks <cargo args> (the script sources scripts/cn-env.sh and execs cargo; use 'build --release', 'test -p sirius-starrocks-cn --no-default-features', 'clippy --all-targets --no-default-features -- -D warnings', 'fmt -- --check'; engine-linked CN tests need LD_LIBRARY_PATH from cn-env.sh and a GPU). FE: cd ${WTS}/${f.wt}/experimental/starrocks && pixi run -e fe fe-build (the worktree's .pixi is a symlink to the clone's envs; a full FE build takes ~280 s and writes starrocks/output/fe; run it with a 900 s tool timeout) only if the spec requires an FE rebuild. FE patches live in experimental/starrocks/patches/*.patch (see scripts/apply-starrocks-patches.sh): develop inside the starrocks submodule checkout, then export the change as a new patch file (git -C starrocks diff -- <files> > ../patches/<name>.patch) and verify apply-starrocks-patches.sh reports it applied/already applied; the submodule pointer itself must not change in your commit.
- Never start a StarRocks FE or a CN cluster and never run SF1000 queries: the orchestrator owns those arms.
- Pre-commit: pixi run --manifest-path ${CLONE}/pixi.toml bash -c "cd ${WTS}/${f.wt} && pre-commit run --files <changed files>".
- Commits: Conventional Commits title, body 3-10 lines for a human reviewer (what/why, tests, what is left), trailer line exactly: Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>. Do not commit build outputs, telemetry, generated YAML, or the .pixi symlink.
- Write your notes to ${SP}/fix/notes/${f.key}-<role>.md.`
const IMPL = { type: 'object', required: ['commits', 'tests_run', 'status', 'notes'], properties: { commits: { type: 'array', items: { type: 'string' } }, tests_run: { type: 'array', items: { type: 'string' } }, status: { type: 'string', enum: ['done', 'partial', 'blocked'] }, notes: { type: 'string' }, left: { type: 'array', items: { type: 'string' } } } }
const REVIEW = { type: 'object', required: ['findings', 'verdict'], properties: { findings: { type: 'array', items: { type: 'object', required: ['severity', 'file', 'summary'], properties: { severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, file: { type: 'string' }, line: { type: 'number' }, summary: { type: 'string' }, fix: { type: 'string' } } } }, verdict: { type: 'string', enum: ['approve', 'request-changes'] }, notes: { type: 'string' } } }
const VERIFY = { type: 'object', required: ['build_ok', 'tests_ok', 'lint_ok', 'commits_ok', 'summary'], properties: { build_ok: { type: 'boolean' }, tests_ok: { type: 'boolean' }, lint_ok: { type: 'boolean' }, commits_ok: { type: 'boolean' }, summary: { type: 'string' }, evidence: { type: 'array', items: { type: 'string' } } } }
const out = await pipeline(FIXES,
  f => agent(`You are implementing one fix for StarRocks-on-Sirius. Read the plan ${PLAN}, your spec ${D}/${f.key}-SPEC.md and ${D}/INTEGRATION.md first; the spec is the contract.
${RULES(f)}
Layer: ${f.layer}.
Method: tests first (write the tests the spec names, see them fail), then the change, then build and run those tests plus the existing suites the spec names; keep the diff to what the spec asks; if the spec is wrong about the code, fix the plan in your notes and implement what the code needs, but do not widen scope. Read docs/super-sirius/README.md's reading order before touching src/. When done, commit (one or two commits) and return the commit SHAs, the tests you ran with their results, and what is left.`,
    { label: `implement:${f.key}`, phase: 'Implement', schema: IMPL }),
  (impl, f) => agent(`You are the adversarial reviewer of the fix ${f.key} in ${WTS}/${f.wt} (commits: ${JSON.stringify(impl && impl.commits)}; implementer notes: ${(impl && impl.notes || '').slice(0, 2000)}).
${RULES(f)}
Read the spec ${D}/${f.key}-SPEC.md and the diff (git -C ${WTS}/${f.wt} log -p perf/profile-sf1000..HEAD). Try to break it: correctness under the measured SF1000 case and the 15 passing queries, cancellation/concurrency, error text, tests that do not actually test the change, scope creep, style versus the surrounding code, commit message quality. Re-run the tests yourself. Do not edit code. Return findings with severity and a verdict.`,
    { label: `review:${f.key}`, phase: 'Review', schema: REVIEW }).then(r => ({ impl, review: r })),
  ({ impl, review }, f) => (review && review.findings && review.findings.some(x => x.severity !== 'minor'))
    ? agent(`Apply the review findings to fix ${f.key} in ${WTS}/${f.wt}. Findings: ${JSON.stringify(review.findings, null, 1)}. Spec: ${D}/${f.key}-SPEC.md.
${RULES(f)}
Fix every blocker and major; fix minors when cheap; rebuild and rerun the tests; add a follow-up commit (do not amend). Return the new commit SHAs and what you changed.`,
        { label: `fix:${f.key}`, phase: 'Fix', schema: IMPL }).then(fx => ({ impl, review, fix: fx }))
    : Promise.resolve({ impl, review, fix: null }),
  (state, f) => agent(`Independent verification of fix ${f.key} in ${WTS}/${f.wt}.
${RULES(f)}
From a clean state (git -C ${WTS}/${f.wt} status must be clean apart from the .pixi symlink): rebuild what the fix touches (engine make release and/or CN cargo build --release and/or fe-build if the spec requires it), run the tests the spec names plus the CI-equivalent checks for the touched layer (engine: the Catch2 tags touched; CN: cargo fmt --check, cargo clippy --all-targets --no-default-features -- -D warnings, cargo test --workspace --no-default-features; pre-commit on the changed files), check every commit message (Conventional Commits, trailer present, no build outputs committed). Return booleans with evidence lines (command + last line of output).`,
    { label: `verify:${f.key}`, phase: 'Verify', schema: VERIFY }).then(v => ({ ...state, verify: v, fix_key: f.key })))
return out.filter(Boolean).map(s => ({ fix: s.fix_key, impl_status: s.impl && s.impl.status, commits: [...(s.impl && s.impl.commits || []), ...(s.fix && s.fix.commits || [])], review: s.review && s.review.verdict, findings: s.review && s.review.findings && s.review.findings.length, verify: s.verify }))
