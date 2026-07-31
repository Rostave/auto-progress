---
name: maintain-project
description: Run one guarded AutoProgress implement-batch task for a configured Unity repository. Use for an approved scheduled run, an explicitly requested early implementation run, or recovery and review feedback for an unfinished AutoProgress implementation branch or pull request.
---

# Maintain Project

Implement one exclusive recovery, compile-repair, or directed item, or a batch of up to the configured number of compatible ordinary improvements. Read [references/workflow.md](references/workflow.md) before acting.

## Qualify and claim

1. Generate a run ID and call `prepare-run` with the registered base branch and task type `implement-batch`. It loads the authoritative policy from the frozen base revision, validates adapters and environment, claims the allowance, admits the original workspace, and runs baseline validation.
2. Require scheduled approval or an explicit human early run. Honor the configured pause and deferral semantics.
3. If `prepare-run` reports `baseline_compile_repair_required`, restrict this run to the base compilation repair. If it reports `recovery_required`, call `recover-run` or stop as directed by its result.

Treat deterministic helper output as authoritative for every condition it covers. Consume successful structured results silently unless the human asks or the final artifact requires disclosure; report failures according to the workflow. Do not spend model reasoning rechecking the same facts or override a failed gate.

## Select work

Use this exclusive order:

1. Recover an existing implementation branch or handle its review feedback.
2. Repair a C# compilation error already present on the fetched remote base.
3. Execute the highest-priority pending human-directed improvement.
4. Select up to `batch.max_improvements` compatible ordinary automatic improvements already merged into the base branch.
5. Record a skip when nothing is eligible.

Recovery, compile repair, and directed work are never automatically batched. Do not discover new candidates or read candidates from an unmerged discovery PR.

Before selecting an ordinary item, compare its evidence blob SHAs with the fetched base. Boundedly recheck only recorded evidence and expected paths when they changed. Record `candidate_stale` and try the next authoritative item when the proposal is no longer valid.

## Implement and validate

- Work only in the change context admitted by `prepare-run`. Do not recreate its Git or environment checks.
- Keep each ordinary item inside its item budget and the whole batch inside its aggregate budget.
- Implement the selected items without staging or committing. Keep expected path ownership disjoint and describe it in the delivery manifest.
- On a pre-commit item failure, precisely undo only that item's uncommitted edits. On a post-commit failure before push, repair it or revert only that same-run item commit. Never reset or rewrite history.
- Submit the semantic delivery manifest to `finish-run`. It computes the actual diff and budget, runs final C# and configured Unity MCP validation, verifies the content fingerprint, renders the run record and review, commits, pushes, opens the Draft review, records material events, and restores the workspace.
- Conflicts always require human resolution. Never merge, rebase, force-push, stash, reset, clean, or auto-resolve.

## Deliver and restore

- Let `finish-run` perform delivery. On `recovery_required`, preserve the reported state and use `recover-run`; do not repeat commit, push, or review creation manually.
- Mark Ready only when the matching already-open Unity Editor was refreshed and its C# compilation passed. Otherwise prominently state **未经 Unity 编译测试**.
- Include the rejection-register notice and never merge the PR.
- Append ledger events for each item and material Git/PR transition, then restore the original branch and refresh Unity if it was open.

Do not invoke configuration, directed-item creation, or discovery skills from this task.
