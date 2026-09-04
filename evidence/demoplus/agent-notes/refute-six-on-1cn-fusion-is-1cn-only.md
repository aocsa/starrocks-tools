# Refutation check: "fusion-is-1cn-only" (task six-on-1cn)

Verdict: **not refuted** on its headline (fusion is inert at N > 1 CNs, so it cannot be why the six
fail at 2/4 CNs, and the six need a multi-CN mechanism). Four of its supporting statements are wrong
or vacuous and should be corrected before the finding is used. Paths below: `S/` =
`/tmp/claude-1002/-home-prestouser-aocsa-sirius/25f3e903-c551-4a98-b7fc-a6e0ed53ade6/scratchpad/`,
`W/` = `/home/prestouser/aocsa/sirius-stacks-wt/demo-plus/experimental/starrocks/`, `C/` =
`/home/prestouser/aocsa/sirius/` (feat/pin-table-cn checkout). Measured = copied from the named file;
inference is marked.

## 1. Confirmed (measured + source)

| statement | evidence |
|---|---|
| 0 fused senders at 4 CNs, 45 at 1 CN | `grep -c 'fused sender fragment'`: `S/demoplus/arms/dp-cn4/cluster.log` 0, `dp-cn4-six-stg48` 0, `dp-cn4-q09-stg72` 0, `dp-cn4-q09-stg72-pool100` 0, `dp-cn1-six` 45 (3 runs x 15). Every arm logs `fusion_mode=Leaf` once per CN (4 / 4 / 4 / 4 / 1 lines). |
| every shuffle sender at 4 CNs has 4 destinations | `dp-cn4/cluster.log` `fragment run started ... role="sender"`: 810 lines `outputs=4`, 228 `outputs=1` (gathers), 48 `outputs=0`; `dp-cn1-six/cluster.log`: 93 `outputs=1`, 18 `outputs=0`, no `outputs=4`. |
| the leaf rule refuses any sender with != 1 destination | `W/crates/starrocks-plan-translator/src/fusion.rs:208-212` `let [destination] = destinations else { return Err(NotSingleDestination { destinations: destinations.len() }) }`; unit test `:1498` asserts `destinations: 4`. `W/src/compute_node_service.rs:1046-1063` additionally requires `HASH_PARTITIONED` (Leaf) and a `DestinationRoute::Local`. `FusionMode` has only `Off / Leaf / LeafAny` (`W/src/tunable.rs:287-297`); there is no fan-out mode, so 2 CNs (`destinations: 2`) is refused the same way. |
| all six die of arena exhaustion at 4 CNs / 16 GiB | `dp-cn4/runs/q{05,08,09,17,18,21}.r0.err`: `exchange staging arena exhausted ... 17179869184 capacity`, 26-47 leases holding 16.03-17.06 GB. |
| five pass at 48 GiB, q09 still exhausts | `dp-cn4-six-stg48/runs/runs.csv` (q05/q08/q17/q18/q21 pass x3, q09 fail); `runs/q09.r0.err` `95 leases outstanding holding 50552338944 bytes` of 51539607552. |
| 1-CN q05 memory numbers | `dp-cn1-six/engine-.cn0.log:376` `[host_pool] ... peak=106962092032 bytes` (~107 GB); `[gpu_pool] ... peak=107374182400` (100 GiB pool cap). |

## 2. Corrections

### 2.1 "0 'fragment fusion skipped' lines" is a log-level artifact, not evidence

At N > 1 the refusal fires in `fusion::sender_shape` (`NotSingleDestination`), and that decline is
logged with `debug!` (`W/src/compute_node_service.rs:1015-1024`); so are the policy declines
(`off`, not a leaf, non-`HASH_PARTITIONED`, `remote destination`, `:989-1063`). Only the
receiver-side `FuseOffer::Declined` reasons are `info!` (`:1097-1103`), and that branch is never
reached at N > 1 because `sender_shape` returns first. `cluster.log` contains 0 `DEBUG` lines in
every arm (`grep -c DEBUG` = 0 for `dp-cn4` and `dp-cn1-six`). So 0 skipped lines is what any arm
shows (the 1-CN arm also has 0) and discriminates nothing. Drop it from the evidence list.

### 2.2 The refusal that fires is `NotSingleDestination`, not `ReceiverExpectsMany(4)`

The spec's section-7 sentence names `NotSingleDestination / ReceiverExpectsMany(4)`.
`ReceiverExpectsMany` lives in `W/src/local_exchange.rs:147,294` inside `offer_local_plan`, which is
only called after `sender_shape` and the three policy checks pass (`compute_node_service.rs:1074-1079`).
At N > 1 it is unreachable. The "every gather receiver expects 4 senders" half of the finding is also
moot in Leaf mode: gathers are `UNPARTITIONED` and are refused at `:1046` for partition type. The
conclusion is unchanged; only the named mechanism is.

### 2.3 "the pinned feat/pin-table-cn path avoided the problem by not shipping the scan output at all" is wrong

`pin_table` is a scan cache, not a plan change: `C/experimental/starrocks/src/fragment_executor.rs:91`
"One table to pin into the engine's scan cache, mirroring `CALL pin_table(...)`", `engine.rs:98`;
`C/src/planner/sirius_plan_get.cpp:79-82` installs a sidecar "only when the pinned cache can serve
this scan"; `C/src/pin_table.cpp:306` "A pin caches the table UNFILTERED". The FE does not know
about the pin, so the plan is the same FILES() plan (`S/perf/sf1000/survey/plan-summary-4cn.txt`
q05: `card_all_one=True`, five `SHUFFLE` exchanges, `10:INNER JOIN (PARTITIONED) build=9:EXCHANGE<-lineitem`)
and the lineitem projection is still hash-partitioned across the CNs and staged in the arena. The
kit on that lineage documents exactly that cost: the SF1000 4-CN preset is
`GPU_MEM=128 GiB / STAGING=32 GiB / HOST_MEM=112 GiB` with "16 GiB died at q05/q07"
(`C/bench/gb200-8gpu/sf1000/README.md:9`, `C/bench/gb200-4gpu/SIRIUS-TUNING-RUNBOOK.md:96-101`,
`C/bench/gb200-8gpu/SIRIUS-TUNING-RUNBOOK.md:68`). `C/bench/gb200-4gpu/HARDWARE.md:99-110` also
says SF1000 cannot be pinned at 1 GPU (283 GB > 198.7 GB) and is "marginal at 2", which is why the
reference's 1-CN q05/q08/q09/q21 cells are dagger "filled, partial" (`S/demoplus/reference-pin-table-cn.md`
header).

Inference: the pinned 4-CN reference (`S/demoplus/reference-pin-table-cn.md`, config not recorded)
ran the six through the same unfused exchange path with the kit's >= 32 GiB arena and 128 GiB pool,
i.e. by sizing, not by avoiding the shuffle. That is consistent with `dp-cn4-six-stg48` passing 5/6
at 48 GiB. The finding should say so instead of claiming the pinned path skips the transfer; the
user's "pin-table-cn ran most of them" point is evidence about arena sizing on the pre-fix path,
not about a different data flow. (The q09 4-CN reference cell 2.73 is also dagger-marked.)

### 2.4 "five of six pass only with a 48 GiB arena" is unsupported

No arm between 16 and 48 GiB exists (`S/demoplus/arms/`: `dp-cn4` = 16 GiB, `dp-cn4-six-stg48` =
48 GiB, then q09 alone at 72 GiB). "Pass with a 48 GiB arena" is measured; "only" is not. The kit's
32 GiB preset passing q05 on the reference lineage (2.3) suggests the threshold is lower.

### 2.5 Omitted: at 72 GiB q09 moves from the arena to a pool OOM

`S/demoplus/arms/dp-cn4-q09-stg72/runs/q09.r0.err`: `failed to push a staged remote batch from
sender 0 into stream 4: std::bad_alloc: out_of_memory` (64 GiB pool); `dp-cn4-q09-stg72-pool100/runs/q09.r0.err`:
`failed to execute fragment: std::bad_alloc: out_of_memory` (100 GiB pool, 72 GiB arena). This
strengthens the finding's "needs bounded/streamed consumption, not a bigger arena" conclusion and
should be cited.

### 2.6 "the fused shape itself would not fit a 96 GB GPU" is inference that ignores the spill tier

The 1-CN numbers are right (1 above), but ~107 GB of the peak is the engine's host pool, i.e. q05
already spilled that much at 1 CN and passed. Whether a 96 GB card passes depends on the pool floor
and host pool size, not on GPU-peak + host-peak. The finding labels this inference; keep it labelled
and do not present it as a fit argument. It is also a side remark: the RTX run was 2 CNs, where
fusion is inert regardless.

### 2.7 RTX attribution: unresolvable here, and the box's own history cuts both ways

`S/demoplus/reference-2cn-rtxpro6000.md` distinguishes "fail (OOM)" (q05 q08 q09 q18) from "fail"
(q17 q21) with no error text. On that box the kit has recorded both `exchange staging arena
exhausted` (q08) and `OOM at operator HASH_JOIN` that a 50 % larger pool did not fix (q09)
(`C/bench/rtxpro6000-2gpu/SIRIUS-TUNING-RUNBOOK.md:287-317`), and TPCH-STATUS.md:112 notes an
earlier arena/deadlock diagnosis there was wrong. The finding correctly flags this as inference;
it should not be firmed up without the RTX `.err` files.

## 3. Net

Headline stands: fusion (Leaf and LeafAny) requires exactly one local destination, every shuffle
sender at N CNs has N destinations, so demo-plus fixes the six on 1 CN only and the N > 1 failures
are the unfused exchange path (arena, then pool). Remove the "0 skipped lines" evidence, replace
`ReceiverExpectsMany` with `NotSingleDestination`, and rewrite the pinned-path sentence: pinning
caches the scan input, the scan output is still shuffled, and the reference lineage passes the six at
4 CNs with a 32 GiB arena.
