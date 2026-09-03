---
name: sirius-pr-review-env-gotchas
description: "How to fetch upstream PRs, push to contributor forks, and run VSS/multi-chunk repros on this box (gh via ~/aocsa/gh-activate.sh as aocsa, ssh key cannot push to forks, 4 GPUs, test-only options)"
metadata: 
  node_type: memory
  type: project
  originSessionId: a581a762-ebcc-4be8-888a-79cdad20331d
  modified: 2026-09-02T02:55:00.000Z
---

Environment facts learned while reviewing sirius-db/sirius PR #1206 (2026-09-01):

- `gh` (2.99) lives in an isolated conda env: `source /home/prestouser/aocsa/gh-activate.sh` first (sets
  GH_CONFIG_DIR=/home/prestouser/aocsa/gh-config and prepends miniforge3/envs/gh/bin), then `gh auth status` shows
  account `aocsa` (a maintainer). Never run `gh auth setup-git` from it (it would rewrite the global git
  credential helper). A pixi copy at ~/.pixi/bin/gh also exists and reads the same config. Use gh for every
  GitHub read/write; unauthenticated api.github.com calls are rate-limited from this box.
- The box's ssh key can read but NOT push to contributor forks (e.g. mbrobbel/sirius) even when
  "Allow edits from maintainers" is on. Push over HTTPS with gh's token instead, without touching
  ~/.gitconfig: `git -c credential.helper= -c credential.helper='!gh auth git-credential' push https://github.com/<owner>/sirius.git <local>:<branch>`.
- The clone at /home/prestouser/aocsa/sirius is shared with other sessions: never checkout/fetch/reset there.
  For upstream-PR work use the dedicated clone /home/prestouser/aocsa/sirius-mbrobbel (origin = aocsa fork,
  `upstream` = sirius-db/sirius, PR heads fetched as `pr-<N>`); the translator crate's CI runs from
  `experimental/starrocks` with `pixi run -e cn cargo {fmt,clippy,test}` and needs only the
  `experimental/starrocks/{starrocks,brpc}` submodules (~1 min end to end).
- In a fresh worktree run `git submodule update --init --recursive` for ALL submodules (substrait too)
  before `pixi run make`; with ccache a full build takes ~5 min. If a stale `duckdb/` dir holds only a
  `CMakePresets.json` symlink, delete it first or the clone fails.
- `build/release/duckdb` has the extension statically linked; `pin_table`/`sirius_knn_search` work without LOAD.
- `SET scan_task_batch_size = ...` (and other internal options) only exist when `SIRIUS_ENABLE_TEST_OPTIONS=1`
  is set in the environment (the unittest binary sets it itself).
- On the 4-GPU GB200 box a multi-chunk pin is spread across GPUs, so VSS index build fails with
  "pinned table spans multiple GPUs". Use `CUDA_VISIBLE_DEVICES=0` for single-GPU VSS repros.

**Why:** each of these cost a failed attempt before the review could run anything.
**How to apply:** follow this checklist before building or running SQL repros for a Sirius PR; see also [[gb200-box-nightly-ci-window]].
