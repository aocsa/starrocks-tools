#!/usr/bin/env bash
# Sequential cargo steps (shared target lock). Records each exit code; does not stop on failure.
S=/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad
STEP=$S/fix/notes/pb-verify-step.sh
SUM=$S/fix/notes/parked-bookkeeping-verifier-chain.txt
: > "$SUM"
"$STEP" test-noengine test --workspace --no-default-features; echo "test-noengine exit=$?" >> "$SUM"
"$STEP" build-release build --release; echo "build-release exit=$?" >> "$SUM"
CUDA_VISIBLE_DEVICES=1 "$STEP" test-gpu test --release -p sirius-starrocks-cn; echo "test-gpu exit=$?" >> "$SUM"
echo "CHAIN DONE $(date -u +%T)" >> "$SUM"
cat "$SUM"
