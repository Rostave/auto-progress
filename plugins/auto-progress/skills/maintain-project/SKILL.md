---
name: maintain-project
description: Run one guarded AutoProgress implement-batch task for a configured Unity repository. Use for an approved scheduled run, an explicitly requested early implementation run, or recovery and review feedback for an unfinished AutoProgress implementation branch or pull request.
---

# Maintain Project

Implement one exclusive recovery, compile-repair, or directed item, or a batch of up to the configured number of compatible ordinary improvements. Read [references/workflow.md](references/workflow.md) before acting.

## Qualify and claim

1. Validate `.codex/auto-progress.toml`. Version 1 requires a human `$configure-auto-progress migrate`; stop before claiming the allowance.
2. Require scheduled approval or an explicit human early run. Honor the configured pause and deferral semantics.
3. Run the shared maintenance preflight and verify the original Unity checkout is clean and available.
4. Check the ledger. Another task type's claim stops this run; a claim for this exact run is only a safe retry.
5. Atomically claim the daily allowance with task type `implement-batch` immediately before core maintenance work.

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

- Lease the configured original Unity checkout. Fetch, create the run-based branch from `origin/<base_branch>`, checkout it, and refresh an already-open matching Unity Editor through Unity MCP.
- Run the configured structured C# validation once as the baseline. A base compile failure changes the run to exclusive compile repair.
- Keep each ordinary item inside its item budget and the whole batch inside its aggregate budget.
- Implement items sequentially and create one commit per improvement containing its code, tests, and improvement-document state change.
- On a pre-commit item failure, precisely undo only that item's uncommitted edits. On a post-commit failure before push, repair it or revert only that same-run item commit. Never reset or rewrite history.
- Run final configured C# validation for the successful subset. Stop for recovery if edits cannot be isolated or the final state cannot be validated.
- Conflicts always require human resolution. Never merge, rebase, force-push, stash, reset, clean, or auto-resolve.

## Deliver and restore

- Before push/PR, compare actual changed paths with any open discovery PR. Path overlap stops delivery for human resolution.
- Push the run branch and open one Draft implementation PR listing every improvement ID and each item's delivered, deferred, or reverted result.
- Mark Ready only when the matching already-open Unity Editor was refreshed and its C# compilation passed. Otherwise prominently state **未经 Unity 编译测试**.
- Include the rejection-register notice and never merge the PR.
- Append ledger events for each item and material Git/PR transition, then restore the original branch and refresh Unity if it was open.

Do not invoke configuration, directed-item creation, or discovery skills from this task.
