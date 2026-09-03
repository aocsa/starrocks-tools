#!/usr/bin/env bash
# Create one worktree + branch per fix off perf/profile-sf1000, with the same setup the perf worktree has:
# .pixi symlink, submodules, StarRocks patches, prebuilt FE copied, engine + CN built (ccache makes the engine build minutes).
set -u
CLONE=/home/prestouser/aocsa/sirius-stacks; WTS=/home/prestouser/aocsa/sirius-stacks-wt; SRC=$WTS/perf
SP=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
for pair in fix-oom-failfast:fix/oom-failfast fix-parked-bookkeeping:fix/parked-bookkeeping fix-files-cardinality:fix/files-cardinality fix-fragment-fusion:fix/fragment-fusion; do
  name=${pair%%:*}; br=${pair##*:}; WT=$WTS/$name
  echo "##### $name ($br) $(date -u +%T)"
  if [ ! -d $WT ]; then git -C $CLONE worktree add -q $WT -b $br perf/profile-sf1000 2>&1 | tail -n 2 || { echo "worktree add failed"; continue; }; fi
  [ -e $WT/.pixi ] || ln -s $CLONE/.pixi $WT/.pixi
  ( cd $WT && git submodule update --init --recursive 2>&1 | tail -n 2 )
  ( cd $WT/experimental/starrocks && bash scripts/apply-starrocks-patches.sh 2>&1 | tail -n 3 )
  mkdir -p $WT/experimental/starrocks/starrocks/output && rsync -a --exclude meta/ $SRC/experimental/starrocks/starrocks/output/fe $WT/experimental/starrocks/starrocks/output/ && echo "FE copied"
  echo "== engine build $(date -u +%T)"; pixi run --manifest-path $CLONE/pixi.toml bash -c "cd $WT && make release 2>&1 | tail -n 2"
  echo "== CN build $(date -u +%T)"; env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn bash $SP/cn-build.sh $WT/experimental/starrocks build --release 2>&1 | grep -E 'error|Finished' | tail -n 2
  ls -la --time-style=+%H:%M $WT/build/release/extension/sirius/sirius.duckdb_extension $WT/experimental/starrocks/target/release/sirius-starrocks-cn 2>/dev/null | awk '{print $6,$7}'
done
echo "##### PREP DONE $(date -u +%T)"
