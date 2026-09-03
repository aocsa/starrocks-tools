# Claude Code assets used for this work

No repo-level skills were created; the reusable Claude assets are:
- `../workflows/*.js`: Workflow-tool scripts (ultracode). `design-workflow.js` (3 designers x 2 judges x spec per issue + integrator),
  `implement-workflow.js` (implementer -> adversarial reviewer -> fixer -> verifier per fix, one worktree each; takes `args.fixes`),
  `workflow-analysis.js` (7 analysts by query family -> 2 skeptics per finding -> synthesizer + critic), `carve-workflow.js`,
  `quent-workflow.js`, and the per-piece carve specs in `../workflows/carve-specs/`.
- `../docs/session-notes/*.md`: the memory notes Claude kept (environment gotchas, Quent behaviour, decimal truncation,
  SF1000 facts, shutdown state). Copy them into the new session's memory directory or paste them into CLAUDE.md-style context.
- `../fix/DECISIONS.md`, `../fix/INTEGRATION.md`, `../evidence/sf1000/WORKFLOW-PLAN.md`: the orchestration context the agents read.
House rules the agents were held to: work only in dedicated worktrees, never the shared clone; never `git stash`; no pushes unless
asked; one GPU per fix worktree, clusters owned by the orchestrator; Conventional Commits with the `Co-Authored-By: Claude Fable 5.1` trailer.
