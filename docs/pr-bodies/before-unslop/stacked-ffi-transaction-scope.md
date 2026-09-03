<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** #1481 merged the C++ `sirius::ffi::Fragment`, and #1328 moved the DuckDB submodule to 1.5.5. Since then, on `dev`, every well-formed `Fragment::build()` throws `TransactionContext::ActiveTransaction called without active transaction`. `build()` commits the transaction it uses for type resolution and `CREATE VIEW sirius_stream_<id>` *before* it opens the query lifecycle (`StandaloneQueryScope`), and only then lowers the Substrait plan — so the Substrait consumer's catalog lookup and the planner's binding run with no transaction open. DuckDB 1.5.4 tolerated that; 1.5.5 throws. CI never saw it because the only two C++ Fragment cases exercise `build()` failure paths, and the Rust crate has no Fragment binding yet.

**What changed** (one reviewable unit: the transaction scope of Substrait lowering on the FFI path, plus the test that pins it).

- `src/sirius_ffi.cpp`, `lower_substrait()`: opens a transaction on `ClientContext::transaction` when none is active, commits it on success and rolls it back (rethrowing) on failure. `client.transaction` rather than `Connection::BeginTransaction()` on purpose: the latter runs `BEGIN TRANSACTION` as an ordinary statement, which would take the lifecycle mutex the enclosing `StandaloneQueryScope` already holds. `Context::execute_substrait` already opens its own transaction before calling `lower_substrait`, so `owned_transaction` is false there and that path is unchanged.
- `src/sirius_ffi.cpp`, `Context::Impl::bring_up`: honors `SIRIUS_LOG_BACKEND` / `SIRIUS_LOG_DIR` / `SIRIUS_LOG_LEVEL` — the env knobs `docs/super-sirius/configuration.md` already documents for the extension. The extension installs the engine log sink from `SiriusContextExtensionCallback`'s constructor, which the FFI path never constructs, so those variables were dead for every embedder and engine-side stalls were invisible on a compute node. Acts only when at least one of the three is set, so embedders that set none keep today's behavior (no surprise `./log` directory). No new knob is introduced.
- `test/cpp/exec/test_sirius_ffi_fragment.cpp`: new `[isolated_context][sirius_ffi]` case. It declares an input stream, hand-builds a Substrait `ReadRel` of `sirius_stream_<id>` with the bundled protobuf (the FFI surface links no plan-from-SQL helper), and requires `build()` not to throw; it then builds a second fragment on the same connection to prove the owned transaction was closed again (a still-open one would fail the next `BeginTransaction()` with "cannot start a transaction within a transaction"). On unfixed `dev` the first `REQUIRE_NOTHROW` fails with the `ActiveTransaction` message.
- `CMakeLists.txt`: the two protobuf header roots (`substrait/third_party`, `substrait/third_party/substrait`) on `sirius_unittest` so the test can include `substrait/plan.pb.h`; the generated code is already linked in through `sirius_extension`.

Verified: GB200 aarch64, CUDA 13.0 (nvcc 13.0, driver 580.105.08), /home/prestouser/aocsa/sirius-stacks @ 98661d7d: baseline dev release build exit 0; incremental `pixi run make` exit 0 (463/463); `./build/release/extension/sirius/test/cpp/sirius_unittest "[sirius_ffi]"` -> All tests passed (8 assertions in 3 test cases); `... "[streaming_fragment]"` -> All tests passed (75 assertions in 5 test cases); 0 failed / 0 skipped; rumdl: no .md files changed vs origin/dev (nothing to lint).

**Intentionally not handled.** Everything else `feat/pin-table-cn` changes in `src/sirius_ffi.cpp` — the exchange staging arena and `export_packed` / `push_packed`, `pin_table`, `declare_input_cardinality` / `estimated_rows`, and the byte-range extraction that sits in the same `lower_substrait` try block — lands in later layers of this stack; `src/include/sirius_ffi.hpp` is untouched here. Layer 1 of the ffi stack; the next layer adds the Rust bindings for `sirius::ffi::Fragment`.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Refs #1481 (merged: the C++ `sirius::ffi::Fragment` surface this fix makes buildable)
- Refs #1328 (merged: the DuckDB 1.5.5 bump that turned the missing transaction into a throw)
- Supersedes #1644 (old draft; this fix was carried inside it and is split out here)
