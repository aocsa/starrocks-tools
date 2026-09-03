#!/usr/bin/env bash
# usage: pb-verify-step.sh <name> <cargo args...>   (runs one cargo step via cn-build.sh, logs to notes/)
S=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
WT=/home/prestouser/aocsa/sirius-stacks-wt/fix-parked-bookkeeping/experimental/starrocks
M=/home/prestouser/aocsa/sirius-stacks/experimental/starrocks/pixi.toml
name=$1; shift
L=$S/fix/notes/parked-bookkeeping-verifier-$name.log
echo "=== $name: $(date -u +%T) args: $*" | tee "$L"
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path "$M" -e cn bash "$S/cn-build.sh" "$WT" "$@" >> "$L" 2>&1
rc=$?
echo "exit=$rc $(date -u +%T)" | tee -a "$L"
echo "--- tail ---"; grep -vE 'WARN cache for Repodata' "$L" | tail -8
exit $rc
