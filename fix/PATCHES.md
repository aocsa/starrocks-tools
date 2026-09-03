Exact commits of the three implemented fixes as `git format-patch` output against `perf/profile-sf1000` (45dab3be):
`oom-failfast-patches/`, `parked-bookkeeping-patches/`, `files-cardinality-patches/`. Apply with `git am`.
Note: `fix/files-cardinality` on aocsa/sirius is a squash of its two commits WITHOUT the `.github/workflows/experimental.yml`
hunk (the publishing token lacked the `workflow` scope); the patches here carry the complete commits including that hunk.
