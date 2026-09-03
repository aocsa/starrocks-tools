Raw outputs of the three Claude Code workflow runs (one directory per run): `journal.jsonl` has one `{"type":"result",...}` line per
finished agent with its full structured return value; `agent-<id>.jsonl` is that agent's complete transcript (prompts, tool calls,
outputs); `agent-<id>.meta.json` its label, phase, model, timing and token count.
- `implement-workflow-wf_e62ea13d-e04`: fixes 1, 2, 3 (implementer, reviewer, fixer, verifier per fix). Fix 3's reviewer/verifier were
  still running when the box was shut down, so their results may be missing from the journal.
- `design-workflow-wf_2a5a9181-944`: 12 designers, 8 judges, 4 spec writers, 1 integrator (the specs in ../../fix/).
- `sf1000-analysis-workflow-wf_aee3e418-46b`: 7 analysts, 24 verifiers, synthesizer, critic (the report in ../../docs/reports/).
The implementers' and reviewers' working notes and logs are under ../../fix/implementation-notes/.
