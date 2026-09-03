export const meta = {
  name: 'sf1000-top4-design',
  description: 'Design exploration for the four SF1000 blockers: three designers per issue, two judges, one synthesized spec per issue, one integrator',
  phases: [
    { title: 'Design', detail: 'three independent designers per issue' },
    { title: 'Judge', detail: 'two judges per issue score the designs' },
    { title: 'Spec', detail: 'one spec per issue, then the integrator' },
  ],
}
const SP = '/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad'
const B = SP + '/perf/sf1000'; const D = SP + '/fix/designs'
const WT = '/home/prestouser/aocsa/sirius-stacks-wt/perf'
const REPORT = '/home/prestouser/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md'
const PLAN = '/home/prestouser/.claude/plans/sf1000-top4-fixes-plan.md'
const COMMON = `Context: StarRocks-on-Sirius (GPU SQL engine embedded in a StarRocks compute node, "CN"). An SF1000 profiling campaign found four blockers; you are helping choose the best fix for one of them.
Read first: ${PLAN} (the plan: issues, candidate solutions, criteria, acceptance tests) and the relevant findings in ${REPORT} (sections 3B, 3C, 4). Evidence bundle: ${B} (WORKFLOW-PLAN.md indexes it; cn1-cnlog.txt, cn1-quent.txt, card-compare-*.txt, cn1/engine-cn0.log, cn1/dump/ are the raw material).
Source tree (read-only for you): ${WT} (engine C++ under src/, CN Rust under experimental/starrocks/src, translator under experimental/starrocks/crates/starrocks-plan-translator, StarRocks FE Java under experimental/starrocks/starrocks/fe, engine docs under docs/super-sirius/).
Rules: do not modify the source tree, do not start clusters or GPU jobs, write only under ${D}/. Distinguish measured facts from inference. Cite file:line for every mechanism you rely on.`
const ISSUES = [
  { key: 'oom-failfast', title: 'Fail fast in the OOM reschedule loop', finding: 'B3 (and B2 for what the downgrade path does)', angleHints: 'gpu_pipeline_executor.cpp reservation gate and reschedule path; oom_reschedule_exception; downgrade_executor return values; how the query error reaches the CN and the FE; the contention case that must keep retrying' },
  { key: 'parked-bookkeeping', title: 'Per-query parked-output bookkeeping in the CN', finding: 'B4 (and B1 for the dispatch order)', angleHints: 'engine.rs ParkedOutput/park/drop_parked/Err path; compute_node_service.rs dispatch worker, cancel_plan_fragment, run_ready_fragment; local_exchange.rs take_ready and removal; result_store.rs query_failures; what the FE sends on failure and cancel' },
  { key: 'files-cardinality', title: 'Real FILES() cardinalities in the StarRocks FE', finding: 'C1, P1, P3 (cost model consequences), P4 (schema RPC)', angleHints: 'StatisticsCalculator.computeFileScanNode, TableFunctionTable file sizes and schema RPC, PGetFileSchemaResult proto, CN file_schema.rs, CostModel broadcast/shuffle terms, how patches are delivered (experimental/starrocks/patches + apply-starrocks-patches.sh) and how the FE is rebuilt (pixi fe-build)' },
  { key: 'fragment-fusion', title: 'Make the six failing queries run on 1 CN (fusion, spill, or both)', finding: 'P1, B1, B2, P5 (RIGHT_SEMI gap)', angleHints: 'compute_node_service.rs process_fragment/translate path and local_exchange register_receiver at the TPlan level; ExchangeNode row layout guarantees; translator join lowering; streaming_fragment.cpp output repositories; downgrade_executor tiers; sirius_ffi.cpp output_row_count/export_packed HOST-tier; which of the six each option rescues and at what cost' },
]
const ANGLES = [
  { key: 'surgical', prompt: 'Angle: the smallest change that fixes the measured failure with the least blast radius. Prefer local edits, one file where possible, and an acceptance test that runs in minutes.' },
  { key: 'robust', prompt: 'Angle: the design a senior engineer would ship for the long run: correct under cancellation and concurrency, observable (log/telemetry), configurable where it must be, with the failure modes enumerated.' },
  { key: 'upstream', prompt: 'Angle: what the maintainers of this repository (see CONTRIBUTING.md, docs/super-sirius/*.md, the carve plan conventions in the plan) would accept as a reviewable PR: fits existing abstractions, tests in the existing suites, no new knobs without a doc row, and clear layering (engine vs CN vs FE patch).' },
]
const DESIGN = { type: 'object', required: ['summary', 'mechanism', 'files', 'tests', 'predicted_effect', 'risks', 'effort', 'dependencies'], properties: {
  summary: { type: 'string' }, mechanism: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, tests: { type: 'array', items: { type: 'string' } },
  predicted_effect: { type: 'string' }, risks: { type: 'array', items: { type: 'string' } }, effort: { type: 'string' }, dependencies: { type: 'array', items: { type: 'string' } }, open_questions: { type: 'array', items: { type: 'string' } } } }
const SCORES = { type: 'object', required: ['scores', 'winner', 'reasoning'], properties: {
  scores: { type: 'array', items: { type: 'object', required: ['angle', 'fixes_failure', 'correctness_risk', 'reviewability', 'effort', 'measurability', 'total'], properties: { angle: { type: 'string' }, fixes_failure: { type: 'number' }, correctness_risk: { type: 'number' }, reviewability: { type: 'number' }, effort: { type: 'number' }, measurability: { type: 'number' }, total: { type: 'number' }, notes: { type: 'string' } } } },
  winner: { type: 'string' }, merge_suggestions: { type: 'string' }, reasoning: { type: 'string' }, disqualifiers: { type: 'array', items: { type: 'string' } } } }
const SPEC = { type: 'object', required: ['spec_path', 'title', 'summary', 'files', 'tests', 'acceptance', 'build_order_notes'], properties: {
  spec_path: { type: 'string' }, title: { type: 'string' }, summary: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, tests: { type: 'array', items: { type: 'string' } }, acceptance: { type: 'string' }, build_order_notes: { type: 'string' }, needs_user_decision: { type: 'array', items: { type: 'string' } } } }

const results = await pipeline(ISSUES,
  issue => parallel(ANGLES.map(a => () => agent(`${COMMON}

Issue: ${issue.title}. Findings to read: ${issue.finding}. Code to read: ${issue.angleHints}.
${a.prompt}
Produce ONE complete design: mechanism (what changes, in which functions, how it behaves under the measured SF1000 case and under the 15 passing queries), files to touch, tests (unit/component and the SF1000 acceptance), predicted effect with numbers derived from the evidence, risks, effort, dependencies on the other three fixes. Write your full design as markdown to ${D}/${issue.key}-${a.key}.md (include the code excerpts you relied on with file:line) and return the structured summary.`,
    { label: `design:${issue.key}:${a.key}`, phase: 'Design', schema: DESIGN }))),
  (designs, issue) => parallel([
    { lens: 'correctness', prompt: 'Judge from the correctness and risk side: does each design actually fix the measured case, what can it break for the 15 passing queries and under cancellation, is the acceptance test sound. Read the three design files fully and re-check their code citations in the source.' },
    { lens: 'delivery', prompt: 'Judge from the delivery side: size and reviewability as a PR in this repository, dependencies and landing order, effort, how quickly the acceptance test can be run on this box, and whether a smaller version would get 80% of the value.' },
  ].map(j => () => agent(`${COMMON}

Issue: ${issue.title}. Three designs were written to ${D}/${issue.key}-surgical.md, ${D}/${issue.key}-robust.md, ${D}/${issue.key}-upstream.md. Their structured summaries: ${JSON.stringify(designs.filter(Boolean), null, 1)}
${j.prompt}
Score each design 1-5 on: fixes_failure, correctness_risk (5 = lowest risk), reviewability, effort (5 = least), measurability; give a total, name a winner, and say what to merge from the others. List disqualifiers (a design that cannot work as written).`,
    { label: `judge:${issue.key}:${j.lens}`, phase: 'Judge', schema: SCORES }))).then(judges => ({ issue, designs, judges })),
  ({ issue, designs, judges }) => agent(`${COMMON}

Issue: ${issue.title}. Designs: ${D}/${issue.key}-{surgical,robust,upstream}.md. Judges' verdicts: ${JSON.stringify(judges.filter(Boolean), null, 1)}
Write the implementation spec ${D}/${issue.key}-SPEC.md for an engineer who will implement it in a worktree without talking to you: goal and non-goals; exact mechanism with file:line anchors and the code shape (signatures, data structures, error text); tests to write first (names, what they assert, where they live: Catch2 under test/cpp/..., Rust #[cfg(test)] in the crate, FE JUnit); build and test commands for this repository (pixi run make, the CN cargo test recipe in experimental/starrocks/pixi.toml, fe-build for FE patches, and note the FE patch delivery via experimental/starrocks/patches + apply-starrocks-patches.sh); acceptance at SF1000 (what the orchestrator will run and what numbers must come out); risks and how the reviewer should probe them; landing order and dependencies on the other three fixes; commit message title (Conventional Commits). Take the judges' winner, merge what they asked to merge, and state any decision that needs the user. Return the structured summary with spec_path.`,
    { label: `spec:${issue.key}`, phase: 'Spec', schema: SPEC }))
const specs = results.filter(Boolean)
log(`${specs.length} specs written`)
const integ = await agent(`${COMMON}

Four specs were written: ${specs.map(s => s.spec_path).join(', ')}. Read all four. Check them for interactions and contradictions (fail-fast error handling vs the bookkeeping's Err-path expectations; fusion vs spill vs FE cardinalities: which of the six failing queries each rescues and whether two fixes fight; shared files edited by two specs; test suites that would collide). Write ${D}/INTEGRATION.md with: the build/landing order, the shared-file conflicts to expect, the combined SF1000 verification matrix (which arm proves which spec, in which order, with pass criteria), and any spec that must change (edit that spec file in place and say what you changed). Return a short summary.`, { label: 'integrate', phase: 'Spec' })
return { specs: specs.map(s => ({ key: s.spec_path, title: s.title, needs_user_decision: s.needs_user_decision || [] })), integration: integ }
