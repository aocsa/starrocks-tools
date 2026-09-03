#!/usr/bin/env bash
# Reviewer's re-run of the CI trio and the engine-linked suite for fix/parked-bookkeeping.
S=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping/experimental/starrocks
M=/home/prestouser/aocsa/sirius-stacks/experimental/starrocks/pixi.toml
L=$S/fix/review-pb
run() {
  name=$1; shift
  echo "=== $name: $(date -u +%T)"
  env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path "$M" -e cn bash "$S/cn-build.sh" "$WT" "$@" > "$L/$name.log" 2>&1
  echo "exit=$? $(date -u +%T)"
}
run fmt fmt -- --check
run clippy clippy --all-targets --no-default-features -- -D warnings
run test-noengine test -p sirius-starrocks-cn --no-default-features
export CUDA_VISIBLE_DEVICES=1
run test-gpu test --release -p sirius-starrocks-cn
echo DONE
