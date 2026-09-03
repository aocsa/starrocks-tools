export const meta = {
  name: 'sf1000-planning-cardinality-backpressure',
  description: 'Analyze SF1000 StarRocks-on-Sirius profiles for planning, cardinality estimation and backpressure; verify findings adversarially; synthesize a report',
  phases: [
    { title: 'Analyze', detail: 'one analyst per query family and one for the standalone-vs-CN planning contrast' },
    { title: 'Verify', detail: 'two skeptics per finding, data lens and code lens' },
    { title: 'Synthesize', detail: 'report + completeness critic' },
  ],
}
const B = '/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/perf/sf1000'
const WT = '/home/prestouser/aocsa/sirius-stacks-wt/perf'
const COMMON = `You are analyzing an SF1000 TPC-H profiling campaign of StarRocks-on-Sirius (a GPU SQL engine embedded in a StarRocks compute node, "CN").
Read ${B}/WORKFLOW-PLAN.md first: it lists every evidence file, the tools and the facts already established. Evidence root: ${B}. Source tree: ${WT}
(engine C++ under src/, CN Rust under experimental/starrocks/src, translator under experimental/starrocks/crates/starrocks-plan-translator, StarRocks FE Java under experimental/starrocks/starrocks/fe).
Key pre-digested files: ${B}/results.md (timings per arm), ${B}/survey/plan-summary.txt and ${B}/survey/explain/qNN.costs.txt (FE plans), ${B}/card-compare-cn1.txt and card-compare-cn4.txt (FE estimate vs actual rows per exchange),
${B}/cn1-cnlog.txt / cn4-cnlog.txt (+ .json: per run fragments, declared cardinalities, nixl transmits, OOM reschedules), ${B}/cn1-quent.txt / cn4-quent.txt / standalone-quent.txt (+ .json: task state time, reservation vs peak, batches, dwell, staging leases),
${B}/cn1-ops.txt etc (per-operator Computing time), ${B}/standalone/runs/qNN.explain.txt (DuckDB/Sirius plan of the same SQL), ${B}/cn1/dump/ (FE fragment params + Substrait per fragment), ${B}/compare-*.txt (oracle correctness).
You may run the python tools in ${B} and read the source; do not modify the source tree, do not start clusters or GPU jobs (they are exclusive resources owned by the orchestrator), do not write outside ${B}/agent-notes/.
The three focus dimensions are: (A) query planning, (B) cardinality estimation, (C) backpressure (memory pressure, parked intermediates, staging leases, queue waits, OOM reschedules, scaling 1->4 CN).
Every finding must cite concrete evidence (file + the line or number) and, where it proposes a change, name the file/function and rate confidence. Distinguish measured facts from inference.`
const FINDINGS = { type: 'object', required: ['findings'], properties: { findings: { type: 'array', items: { type: 'object', required: ['id', 'dimension', 'claim', 'evidence', 'impact', 'confidence'], properties: {
  id: { type: 'string' }, dimension: { type: 'string', enum: ['planning', 'cardinality', 'backpressure', 'correctness', 'other'] }, claim: { type: 'string' },
  evidence: { type: 'array', items: { type: 'string' } }, impact: { type: 'string' }, proposed_change: { type: 'string' }, code_refs: { type: 'array', items: { type: 'string' } },
  confidence: { type: 'string', enum: ['high', 'medium', 'low'] }, queries: { type: 'array', items: { type: 'string' } } } } } } }
const VERDICT = { type: 'object', required: ['refuted', 'reasoning'], properties: { refuted: { type: 'boolean' }, reasoning: { type: 'string' }, corrections: { type: 'string' }, extra_evidence: { type: 'array', items: { type: 'string' } } } }
const FAMILIES = [
  { key: 'agg-only', queries: 'q01 q06', focus: 'two-phase aggregation, scan task queueing (Queued time), reservation vs peak, 1 vs 4 CN scaling, standalone parity' },
  { key: 'broadcast-joins', queries: 'q03 q10 q12 q14 q19', focus: 'FE BROADCAST of orders/lineitem-sized build sides chosen with cardinality 1; actual rows per exchange; parked bytes and dwell; what DuckDB standalone chose instead (build side, join order)' },
  { key: 'multi-fragment-small', queries: 'q02 q11 q15', focus: 'many small fragments, GATHER/SHUFFLE hops, per-fragment fixed overhead, and the q15 run-to-run row-count difference (streams 18/22 carried 0 rows in one run: relate to float-sum determinism and the SIRIUS_CANONICAL_FLOAT_SUMS gate; check the canon vs nocanon follow-up arms if present)' },
  { key: 'semi-anti-joins', queries: 'q04 q13 q20 q22', focus: 'LEFT SEMI / RIGHT OUTER / ANTI joins whose build side is the big table (q04: 3.79B lineitem rows broadcast as build side), BUCKET_SHUFFLE, agg phases; what a stats-aware plan would do' },
  { key: 'five-way-join', queries: 'q07', focus: 'the passing 5-join query: 1.8B-row PARTITIONED exchange and 1.5B-row BROADCAST of orders; per-fragment time, dwell, 1 vs 4 CN, nixl transmit metrics' },
  { key: 'oom-failures', queries: 'q05 q08 q09 q17 q18 q21', focus: 'why these OOM at GPU_SCAN after 100 reschedules on a 100 GiB pool: which intermediates are parked (declared cardinalities, batches), the reschedule storm timeline from engine logs, why the engine cannot spill parked batches, whether standalone Sirius runs them (standalone-failed arm), and what change would let them run (streaming receivers, spill of parked batches, FE-side plan changes, admission control)' },
  { key: 'planning-contrast', queries: 'all', focus: 'systematic comparison of FE plans (fragments, join distribution, agg phases) against DuckDB/Sirius standalone EXPLAIN plans for every query that ran on both; the FE StatisticsCalculator.computeFileScanNode row count 1; concrete options ranked: FE statistics for FILES() (parquet footer row counts), join hints, translator-side broadcast refusal or build/probe swap using the exact declared cardinalities, CN-side re-planning; and what StarRocks session variables could change today' },
]
phase('Analyze')
const analyses = await parallel(FAMILIES.map(f => () => agent(`${COMMON}

Your family: ${f.key}. Queries: ${f.queries}. Focus: ${f.focus}.
Work through the evidence for these queries in all arms (cn1, cn4, standalone, plus standalone-failed / q15 canon arms if present), read the relevant source when a mechanism matters (engine.rs run_fragment_inner, local_exchange.rs, gpu_pipeline_executor.cpp reschedule loop, exchange_staging_arena.cpp, the FE StatisticsCalculator and the join distribution rules), and produce 3-8 findings ranked by impact. Prefer fewer, well-evidenced findings. Write your working notes to ${B}/agent-notes/${f.key}.md (tables you computed, commands you ran) and return the findings as structured output.`,
  { label: `analyze:${f.key}`, phase: 'Analyze', schema: FINDINGS })))
const all = analyses.filter(Boolean).flatMap((a, i) => (a.findings || []).map(x => ({ ...x, family: FAMILIES[i].key })))
log(`${all.length} raw findings from ${analyses.filter(Boolean).length} analysts`)
// dedupe by (dimension, first 60 chars of claim) and keep the top 12 by confidence then breadth
const seen = new Set(); const uniq = []
for (const f of all) { const k = f.dimension + '|' + f.claim.toLowerCase().slice(0, 60); if (seen.has(k)) continue; seen.add(k); uniq.push(f) }
const rank = { high: 3, medium: 2, low: 1 }
uniq.sort((a, b) => (rank[b.confidence] - rank[a.confidence]) || ((b.queries || []).length - (a.queries || []).length))
const top = uniq.slice(0, 12); const rest = uniq.slice(12)
log(`verifying ${top.length} findings; ${rest.length} lower-ranked findings pass through unverified`)
phase('Verify')
const LENSES = [
  { lens: 'data', prompt: 'Refute this finding FROM THE MEASUREMENTS: re-derive the numbers from the evidence files and tools, check the run-to-run consistency, check whether another query or arm contradicts it, check units and attribution (time windows, query ids). Default to refuted=true only when you can show a concrete contradiction or a missing link in the evidence.' },
  { lens: 'code', prompt: 'Refute this finding FROM THE SOURCE: read the code paths it names (and the ones it should have named) in the engine, CN and FE, and check whether the mechanism claimed is what the code does, whether the proposed change is feasible where it is proposed, and whether an existing knob or code path already covers it. Default to refuted=true only with a concrete code-level contradiction.' },
]
const verified = await parallel(top.map(f => () => parallel(LENSES.map(L => () => agent(`${COMMON}

${L.prompt}

Finding under test (from analyst "${f.family}"):
${JSON.stringify(f, null, 1)}

Return refuted=true/false with your reasoning, corrections to the claim or numbers if any, and extra evidence you found.`,
  { label: `verify:${L.lens}:${f.id}`, phase: 'Verify', schema: VERDICT })))
  .then(vs => ({ ...f, verdicts: vs.map((v, i) => ({ lens: LENSES[i].lens, ...(v || { refuted: null, reasoning: 'verifier failed' }) })) }))))
const confirmed = verified.filter(Boolean).filter(f => !f.verdicts.some(v => v.refuted === true))
const disputed = verified.filter(Boolean).filter(f => f.verdicts.some(v => v.refuted === true))
log(`${confirmed.length} confirmed, ${disputed.length} disputed`)
phase('Synthesize')
const report = await agent(`${COMMON}

Write the report ${B}/../../../../../home/prestouser/.claude/plans/starrocks-sirius-perf/sf1000-planning-cardinality-backpressure.md is NOT writable by you; instead write it to ${B}/agent-notes/REPORT.md (the orchestrator copies it). Audience: the Sirius engineers. Style: plain prose for humans, short sentences, tables for numbers, quote the evidence lines, no marketing, no em-dashes.
Sections: 1) Setup (build, data, arms, how "supported" was decided, what passed/failed and why, correctness vs oracle incl. the known decimal truncation), 2) Results table (from ${B}/results.md), 3) Findings by dimension (planning, cardinality, backpressure), each with evidence and the proposed change with file paths and confidence, confirmed findings first, then disputed ones with the objection stated, 4) What to do first (ordered list of changes, expected effect, risk), 5) Open questions / what was not measured.
Confirmed findings: ${JSON.stringify(confirmed, null, 1)}
Disputed findings: ${JSON.stringify(disputed, null, 1)}
Unverified lower-ranked findings: ${JSON.stringify(rest, null, 1)}
Also read the analysts' notes in ${B}/agent-notes/*.md for tables worth including.`, { label: 'synthesize:report', phase: 'Synthesize' })
const critic = await agent(`${COMMON}

Read ${B}/agent-notes/REPORT.md. You are the completeness critic: list what is missing or overstated with respect to the three focus dimensions and the evidence bundle (an arm not used, a claim without a number, a proposed change without a file, a contradiction between sections, a query family ignored). Then FIX the report in place (edit ${B}/agent-notes/REPORT.md) for every item you can fix from the evidence, and return the list of items you could not fix.`, { label: 'synthesize:critic', phase: 'Synthesize' })
return { confirmed: confirmed.length, disputed: disputed.length, unverified: rest.length, critic }
