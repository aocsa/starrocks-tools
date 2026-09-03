<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Why a submodule patch and not a Sirius-owned proto.** The three RPCs this PR adds
(`exchange_nixl_md`, `request_staging_lease`, `transmit_packed`) are methods of StarRocks'
`PInternalService`. That is the service `build.rs` hands to prost to generate the CN's dispatch
trait and router, and CN-to-CN calls ride the same brpc endpoint the FE already talks to. A
separate `sirius_exchange.proto` would mean a second generated trait and a second dispatch table
for three methods. So I kept them in `internal_service.proto` and carry the edit as
`patches/nixl-exchange-proto.patch`, applied into the submodule working tree. This is what the
multi-CN branch has run with. The cost is a submodule that is dirty by design; `ignore = dirty`
in `.gitmodules` keeps it out of `git status`. If that is not acceptable upstream, vendoring a
Sirius-owned proto is the alternative. It drops the patch and the guard at the cost of a second
service. Reviewers, please answer that question explicitly before the transport PRs stack on this.

**What changed.**
- `experimental/starrocks/patches/nixl-exchange-proto.patch` (64 lines) adds six messages and
  three rpcs to `PInternalService`, commented as Sirius-only extensions.
- `experimental/starrocks/scripts/apply-starrocks-patches.sh` applies every `patches/*.patch`
  with `git apply`. It is idempotent and prints `already applied` when the reverse check passes.
- `experimental/starrocks/build.rs` gains `require_exchange_proto_patch`. When
  `internal_service.proto` lacks `message PExchangeNixlMd` it fails the build with one line that
  names the patch and the `git -C <submodule> apply <patch>` remedy, instead of letting the
  transport handlers fail later with `unresolved imports`.
- `experimental/starrocks/pixi.toml` gains an `apply-starrocks-patches` task, now a dependency of
  `cn-build`, `cn-test`, `cn-run` and `cn-test-no-engine`.
- `.github/workflows/experimental.yml` gains a `Patch starrocks proto` step right after the
  submodule init, running the same script developers run. This step is mandatory. CI checks the
  submodule out unpatched, so from this PR on an unpatched tree fails both the clippy step and
  the test step inside build.rs.

**Behaviour is unchanged.** The three RPCs generate trait methods whose default body returns
"method not implemented", the same answer the CN gives any method it does not implement today.
No `Cargo.toml`, `Cargo.lock` or `src/` change until the transport PRs implement the handlers.

**`rows = 10`.** `PTransmitPackedParams.rows` ships in the patch so the wire shape is final. Its
consumer is the exchange input cardinality work (#1694 on the engine side); nothing here reads it.

**How I tested it.** On a GB200 box (aarch64) I ran the CI trio in the pixi cn env, that is cargo fmt, clippy with warnings as errors, and the workspace test suite without the engine feature, 171 tests green. As the negative check, with the submodule unpatched a cargo check of the CN crate fails inside build.rs with the patch named and the `git apply` remedy printed, and the apply script is idempotent (a second run prints `already applied`).

**Not handled here.** The handlers, the PRPC client and the nixl agent land in the transport
PRs, and the nixl agent tier PR is gated on this one. Fork PR against `dev`, no stack.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [ ] Cover changes with new or existing tests
- [x] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Refs #1694, exchange input cardinality on the engine side, the consumer of `rows = 10`.
- Carved from the `aocsa/feat/pin-table-cn` umbrella (draft #1686, being closed and re-cut).
