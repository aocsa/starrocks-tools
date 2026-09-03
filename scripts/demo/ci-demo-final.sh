#!/usr/bin/env bash
# Final CI-equivalent checks on demo HEAD: the CN trio exactly as .github/workflows/experimental.yml runs it (no engine feature),
# then the rust bindings job from check.yml (fmt, clippy -D warnings, test --no-run) against the worktree's build/release.
WT=/home/prestouser/aocsa/sirius-stacks-wt/demo; CLONE=/home/prestouser/aocsa/sirius-stacks
echo "== demo HEAD: $(git -C $WT rev-parse --short HEAD) $(date -u +%T)"
echo "== CN trio (experimental.yml) $(date -u +%T)"
env CONDA_OVERRIDE_CUDA=13 pixi run --manifest-path $CLONE/experimental/starrocks/pixi.toml -e cn bash -c "cd $WT/experimental/starrocks && \
  echo '-- fmt' && cargo fmt --package sirius-starrocks-cn --package starrocks-plan-translator --package starrocks-thrift -- --check && echo 'fmt: OK' && \
  echo '-- clippy' && cargo clippy --all-targets --no-default-features -- -D warnings 2>&1 | tail -n 3 && echo 'clippy: exit '\${PIPESTATUS[0]} && \
  echo '-- test' && cargo test --workspace --no-default-features 2>&1 | grep -E '^test result|Running|error|FAILED|panicked' ; echo 'test: exit '\${PIPESTATUS[0]}"
echo "== rust bindings job (check.yml) $(date -u +%T)"
export RUSTFLAGS="-C link-arg=-Wl,--allow-shlib-undefined"
pixi run --manifest-path $CLONE/pixi.toml bash -c "cd $WT && \
  echo '-- fmt' && cargo fmt --manifest-path rust/Cargo.toml --package sirius --package sirius-sys --check && echo 'fmt: OK' && \
  echo '-- clippy' && cargo clippy --manifest-path rust/Cargo.toml --package sirius --package sirius-sys --all-targets -- -D warnings 2>&1 | tail -n 3 && echo 'clippy: exit '\${PIPESTATUS[0]} && \
  echo '-- test --no-run' && cargo test --no-run --manifest-path rust/Cargo.toml --package sirius --package sirius-sys 2>&1 | tail -n 3 && echo 'test-no-run: exit '\${PIPESTATUS[0]}"
echo "== CI-DEMO DONE $(date -u +%T)"
