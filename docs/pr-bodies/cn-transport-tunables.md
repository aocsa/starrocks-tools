<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** The multi-CN transport that follows this PR has six environment knobs (RPC and nixl
transfer timeouts, the bandwidth canary size and floor, the warmup budget and peer count). On the
source branch the warmup path read its two knobs with `.and_then(|v| v.parse().ok())`, which
turned `SIRIUS_CN_NIXL_WARMUP_TIMEOUT_SECS=6O` (letter O, not zero) into the 180 s default with no
diagnostic. The knob looked dead. The other four were hardcoded constants with no env path at all;
the registry is what gives them one. An unparsable timeout is a startup error, not a default.

**What changed** (one reviewable unit: the registry, its wiring, and its doc).
- `experimental/starrocks/src/tunable.rs` (new): a `Knob<T>` with name, default and `[min, max]`,
  six knob constants, and `Tunables::{resolve, get}` over a `OnceLock`. Three rules. A value that
  does not parse or falls outside its range is rejected, never clamped and never ignored. The set
  is resolved once at bring-up and a bad value fails the process. The resolved set is logged, so
  the log says what the CN got rather than what the launcher echoed. Unset and empty both mean
  the compiled default. `NaN` is rejected before the range check (it compares false against both
  bounds). `SIRIUS_CN_NIXL_CANARY_FLOOR_GBPS=0` is the documented off switch and logs a warning.
- `experimental/starrocks/src/main.rs`: `Tunables::resolve()` is the first statement of `run`,
  before any port is bound or the GPU pool is reserved.
- `experimental/starrocks/src/lib.rs`: `mod tunable;` and `pub use tunable::Tunables;`.
- `experimental/starrocks/docs/TUNABLES.md` (new): how the registry behaves and a table of the six
  knobs. The engine-side knobs the source branch listed there are not on `dev` yet, so I left
  them out; their PRs add the rows. `SIRIUS_CN_DUMP_FRAGMENTS` and `SIRIUS_CN_TRANSLATE_ONLY` are
  on `dev` already and stay undocumented here; this PR covers the transport registry only.

I made the fields and `Tunables::get` `pub`: the crate is a library, so public items are not dead
code, and `clippy -D warnings` stays green without an `allow(dead_code)` a later PR must remove.

**Configuration changes.** Six new env vars, defaults equal to the constants the source branch had
hardcoded: `SIRIUS_CN_RPC_TIMEOUT_SECS` (60, in [1, 3600]), `SIRIUS_CN_NIXL_XFER_TIMEOUT_SECS`
(30, in [1, 3600]), `SIRIUS_CN_NIXL_CANARY_BYTES` (16 MiB, in [1 MiB, 1 GiB]),
`SIRIUS_CN_NIXL_CANARY_FLOOR_GBPS` (2.0, in [0, 10000], 0 disables),
`SIRIUS_CN_NIXL_WARMUP_TIMEOUT_SECS` (180, in [1, 3600]), `SIRIUS_CN_NIXL_WARMUP_EXPECT_PEERS`
(0 means no early exit, max 4096). Each is documented on its constant and in `docs/TUNABLES.md`.

**Tests.** Ten unit tests in `tunable.rs`, all in CI's `--no-default-features` job: defaults pinned
to the old constants, a value taken, whitespace tolerated, empty read as unset, `6O` rejected,
out-of-range rejected instead of clamped, `NaN` and negatives rejected on the float knob, `0` taken
as the floor's off switch, expect-peers `0` mapped to `None`, one bad knob failing the whole
resolution. Tests that touch the environment serialize on a mutex.

**How I tested it.** On a GB200 box (aarch64) I ran the CI trio: cargo fmt, clippy with warnings as errors, and the CN test suite without the engine feature. All 181 tests passed, the ten new tunable tests among them. Pre-commit skips everything under experimental/, so I ran the markdown linter on docs/TUNABLES.md by hand and it came back clean.

**Intentionally not handled.** Nothing on `dev` reads these values yet, so the registry validates
and logs only. The nixl transport, the PRPC client and the session warmup land in later PRs and read
them through `Tunables::get()`; this PR exists so each of those stays small, and it gates the PRPC
client PR. The two warmup switches `SIRIUS_CN_NIXL_WARMUP` and `SIRIUS_CN_NIXL_WARMUP_PEERS` ship
with the warmup PR and should join this registry there rather than keep their raw `env::var` reads.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [x] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Carved from the `aocsa/feat/pin-table-cn` umbrella (draft #1686); supersedes that part of draft #1644.
