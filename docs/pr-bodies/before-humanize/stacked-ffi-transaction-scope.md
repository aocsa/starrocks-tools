<!-- NOTE: All PRs should be opened as "Draft" until they meet PR "reviewability" and marked "Ready for review" -->

## Description

**Motivation.** #1481 merged the C++ `sirius::ffi::Fragment`, and #1328 moved the DuckDB submodule to 1.5.5. Since then, on `dev`, every well-formed `Fragment::build()` throws `TransactionContext::ActiveTransaction called without active transaction`. The cause is ordering. `build()` commits the transaction it uses for type resolution and `CREATE VIEW sirius_stream_<id>`, then opens the `StandaloneQueryScope` query lifecycle, and only then lowers the Substrait plan. So the Substrait consumer's catalog lookup and the planner's binding run with no transaction open. DuckDB 1.5.4 tolerated that. 1.5.5 throws. CI never saw it because the only two C++ Fragment cases exercise `build()` failure paths, and the Rust crate has no Fragment binding yet.

**What changed.** This is one reviewable unit. It changes the transaction scope of Substrait lowering on the FFI path, and adds the test that pins it.

- In `src/sirius_ffi.cpp`, `lower_substrait()` now opens a transaction on `ClientContext::transaction` when none is active. It commits on success, and on failure it rolls back and rethrows. I used `client.transaction` rather than `Connection::BeginTransaction()` on purpose. The latter runs `BEGIN TRANSACTION` as an ordinary statement, which would take the lifecycle mutex the enclosing `StandaloneQueryScope` already holds. `Context::execute_substrait` already opens its own transaction before calling `lower_substrait`, so `owned_transaction` is false there and that path is unchanged.
- Also in `src/sirius_ffi.cpp`, `Context::Impl::bring_up` now honors `SIRIUS_LOG_BACKEND`, `SIRIUS_LOG_DIR` and `SIRIUS_LOG_LEVEL`, the env vars `docs/super-sirius/configuration.md` already documents for the extension. The extension installs the engine log sink from the `SiriusContextExtensionCallback` constructor, which the FFI path never constructs. So those variables were dead for every embedder, and engine-side stalls were invisible on a compute node. I gated the new code on at least one of the three being set. Embedders that set none keep today's behavior and get no surprise `./log` directory. This adds no new setting.
- `test/cpp/exec/test_sirius_ffi_fragment.cpp` gets a new `[isolated_context][sirius_ffi]` case. It declares an input stream and hand-builds a Substrait `ReadRel` of `sirius_stream_<id>` with the bundled protobuf, because the FFI library links no plan-from-SQL helper. It requires `build()` not to throw, then builds a second fragment on the same connection to prove the owned transaction was closed again. A still-open one would fail the next `BeginTransaction()` with "cannot start a transaction within a transaction". On unfixed `dev` the first `REQUIRE_NOTHROW` fails with the `ActiveTransaction` message.
- `CMakeLists.txt` adds the two protobuf header roots, `substrait/third_party` and `substrait/third_party/substrait`, to `sirius_unittest` so the test can include `substrait/plan.pb.h`. The generated code is already linked in through `sirius_extension`.

What I want eyes on is the `owned_transaction` gate and the commit/rollback pairing around the try block. Everything else in that hunk is the existing lowering code reindented into the try block, plus a comment explaining why the transaction has to be owned here.

Verified: GB200 aarch64, CUDA 13.0 (nvcc 13.0, driver 580.105.08), /home/prestouser/aocsa/sirius-stacks @ 98661d7d: baseline dev release build exit 0; incremental `pixi run make` exit 0 (463/463); `./build/release/extension/sirius/test/cpp/sirius_unittest "[sirius_ffi]"` -> All tests passed (8 assertions in 3 test cases); `... "[streaming_fragment]"` -> All tests passed (75 assertions in 5 test cases); 0 failed / 0 skipped; rumdl: no .md files changed vs origin/dev (nothing to lint).

**Intentionally not handled.** Everything else `feat/pin-table-cn` changes in `src/sirius_ffi.cpp` lands in later layers of this stack. That covers the exchange staging arena and `export_packed` / `push_packed`, `pin_table`, `declare_input_cardinality` / `estimated_rows`, and the byte-range extraction that sits in the same `lower_substrait` try block. `src/include/sirius_ffi.hpp` is untouched here. This is layer 1 of the ffi stack. The next layer adds the Rust bindings for `sirius::ffi::Fragment`.

## Checklist
- [x] Read CONTRIBUTING.md and ensure PR meets "reviewability" checklist
- [x] Cover changes with new or existing tests
- [ ] Document configuration changes in code and summarize in the description above
- [ ] Update human and agent documentation (README.md, docs/, skills, CLAUDE.md)

## References
- Refs #1481, already merged. It added the C++ `sirius::ffi::Fragment` API this fix makes buildable.
- Refs #1328, already merged. It bumped DuckDB to 1.5.5, which turned the missing transaction into a throw.
- Supersedes #1644, an old draft that carried this fix. It is split out here.
