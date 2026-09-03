# Draft: request to a sirius-db admin (mike-wendt) to allow `stacked/**` for maintainers

Suggested channel: a comment on #1662 (the stacked-PR docs PR) or a short GitHub issue in sirius-db/sirius
titled `ci(repo): allow sirius-maintainers to create stacked/** branches`. Text:

---

Hi @mike-wendt — following CONTRIBUTING's Stacked PRs section (#1662/#1663), I tried to publish the first layer
of a stack today and the push was refused by branch protection:

```
$ gh stack init -b dev stacked/scan-byte-range-rule && gh stack submit --auto
remote: - You're not authorized to push to this branch. Visit https://docs.github.com/.../about-protected-branches ...
 ! [remote rejected]   stacked/scan-byte-range-rule -> stacked/scan-byte-range-rule (protected branch hook declined)
```

Facts checked with the API: my account (`aocsa`) has `push` on `sirius-db/sirius` (`maintain`/`admin` false) and is
a member of the `sirius-maintainers` team; no `stacked/*` branch existed at the time, so this looks like a
branch-creation restriction on the `stacked/**` pattern (or on all non-`dev` branches) whose allow/bypass list
only contains admins — your `stacked/docs-contributing-gh-stacks` push worked because you are one.

Could you add the `sirius-maintainers` team to the allow-list for creating and pushing `stacked/**` branches
(Settings → Branches / Rulesets: "Restrict creations" and "Restrict pushes" bypass or allow-list), or tell me
which team/role the rule expects? CONTRIBUTING says "Stacked PRs require push permissions … restricted to
Maintainers", so the rule and the doc should agree.

Context: this is the first of six stacks that carve the multi-CN work (`feat/pin-table-cn`, draft #1686) into
one-sitting PRs; two self-contained fork PRs from the same batch are already open as Drafts (#1693, #1694). Three
stack bottoms are carved, built and tested locally and only wait on this permission:
`stacked/scan-byte-range-rule`, `stacked/ffi-transaction-scope`, `stacked/pipeline-stall-watchdog`.

Thanks!

---

Verification once granted (from the stacking clone, gh env sourced):

```bash
source /home/prestouser/aocsa/gh-activate.sh && cd /home/prestouser/aocsa/sirius-stacks && git checkout stacked/scan-byte-range-rule && gh stack submit --auto
```
