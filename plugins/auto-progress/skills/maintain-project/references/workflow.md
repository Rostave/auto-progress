# AutoProgress implementation workflow

## Daily identity and allowance

Use the configured IANA timezone. IDs use local dates:

- improvement: `IMP-YYYY.MM.DD-xxxxxxxx`
- run: `RUN-YYYY.MM.DD-xxxxxxxx`

After eligibility and approval, call the shared `claim-allowance` command with `implement-batch`. A claim by another task type prevents implementation that day. A work-branch `commit_created` or `branch_pushed` completes the implementation maintenance day even when PR creation remains pending.

## Hard safety and workspace lease

- The configured base branch is the only source and PR target.
- Reuse the original Unity checkout and Library; do not create a second Unity project.
- Record the original branch and HEAD and require a clean tree with no active Git operation.
- Never merge, rebase, force-push, stash, reset, clean, rewrite history, or resolve conflicts.
- Restore the original branch at the end. If restoration fails, preserve state and block later runs for human recovery.

## Exclusive work

Pending implementation PR/recovery, remote-base compile repair, and human-directed items each occupy a run alone. Review feedback remains limited to the existing PR scope. Directed items bypass the rejection register but only their declared exemptions bypass other automatic-task rules.

## Ordinary maintenance batch

Choose only authoritative automatic-discovery documents in `queued` state on the fetched base. Never scan unrelated code for extra work.

An ordinary batch contains at most `batch.max_improvements` items and requires:

- same or adjacent module;
- no scope or behavior conflict;
- a shared validation profile;
- aggregate estimated and actual changes inside `[batch_budget]`.

If only one item qualifies, run one. Do not pad a batch.

Check freshness from `discovered_at_base_sha`, `evidence_paths`, `expected_paths`, and `evidence_blob_shas`. When a recorded blob changed, inspect only those paths. If evidence vanished, the scope materially changed, or acceptance is no longer valid, record `candidate_stale` and select the next queued item.

## Validation and partial delivery

Run configured structured C# validation directly, never through a shell expression:

1. Once on the new branch before implementation.
2. Any focused checks required by an item.
3. Once for the final successful subset.

Implement items sequentially. Every item gets an independent commit referencing both its `IMP-...` and the shared `RUN-...`.

- Failure before commit: remove only the current item's uncommitted edits; leave it queued.
- Failure after commit and before any push: try a bounded repair; otherwise `git revert` only that item's same-run commit.
- Re-run final validation and deliver successful items together.
- If isolation, revert, or validation is unsafe, stop and preserve pending recovery.

Record each item as `delivered`, `deferred`, or `reverted`. A delivered item becomes `implemented` only when the PR is merged into the base branch.

## Unity and PR

If Unity is already open for the exact project root, refresh after checkout and inspect compiler results through Unity MCP. Only a passing refresh/compile allows Ready status. Otherwise the Draft PR must say **未经 Unity 编译测试**.

Use branch:

```text
codex/auto-progress/run-yyyy.mm.dd-xxxxxxxx-implement-batch
```

Use PR title:

```text
[AutoProgress][RUN-...] Implement N improvements
```

The body lists all IDs, per-item results, aggregate and per-item budgets, validation, rollback, run record, and the rejection-register reminder. An open discovery PR may coexist, but any changed-path overlap stops PR creation for human resolution.
