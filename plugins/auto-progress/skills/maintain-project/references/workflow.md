# AutoProgress implementation workflow

## Daily identity and allowance

Use the configured IANA timezone. IDs use local dates:

- improvement: `IMP-YYYY.MM.DD-xxxxxxxx`
- run: `RUN-YYYY.MM.DD-xxxxxxxx`

Pass `trigger_source=scheduled` for an approved automation and `trigger_source=manual` for an explicit human call. Only scheduled `implement-batch` claims the daily allowance. Manual calls never read, claim, or complete that allowance, although workspace, recovery, and open-review gates still apply.

## Hard safety and workspace lease

- For every gate or Git transition covered by the shared script, call that deterministic entry point and treat its structured result as authoritative. Consume successful stages silently unless requested or required in the final artifact; report failures without reproducing checks in prose or bypassing them with model judgment.
- The configured base branch is the only source and PR target.
- Reuse the original Unity checkout and Library; do not create a second Unity project.
- Record the original branch and HEAD and require a clean tree with no active Git operation.
- If path-disjoint human edits appear after implementation but before `finish-run`, let the deterministic entry point freeze their paths and fingerprints, unstage them, prove restoration safety, and exclude them from delivery. Target overlap or any later change stops delivery.
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

Implement items without staging or committing, then submit the final successful subset together for one batch validation. After validation, deterministic delivery creates one independent commit per item referencing both its `IMP-...` and the shared `RUN-...`.

- Failure before commit: remove only the current item's uncommitted edits; leave it queued.
- Failure after commit and before any push: try a bounded repair; otherwise `git revert` only that item's same-run commit.
- Run final validation once for the successful subset, create its item commits, then push the batch once.
- If isolation, revert, or validation is unsafe, stop and preserve pending recovery.

Keep unsuccessful items `queued` and record their reason only in the ledger. Before each item commit, deterministic delivery renames that item's document to `--implemented.md` and includes the transition with its code and tests. `implemented` means final validation passed and the item commit exists; push, Draft review, merge, and release remain separate facts.

## Unity and PR

If Unity is already open for the exact project root and the configured MCP adapter is available, refresh after checkout and inspect compiler results through Unity MCP. Optional MCP absence or failure does not block workspace admission; record `unity.verified = false` and its reason. Only a passing refresh/compile allows Ready status. Otherwise the Draft PR must say **未经 Unity 编译测试**.

Use branch:

```text
codex/auto-progress/run-yyyy.mm.dd-xxxxxxxx-implement-batch
```

Use PR title:

```text
[AutoProgress][RUN-...] Implement N improvements
```

The body lists all IDs, per-item results, aggregate and per-item budgets, validation, rollback, run record, and the rejection-register reminder. An open discovery PR may coexist, but any changed-path overlap stops PR creation for human resolution.
