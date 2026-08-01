# Improvement discovery workflow

## Qualification before allowance

Run:

```text
python <plugin-root>/scripts/auto_progress.py preflight --mode discovery --repo <repo> --config <repo>/.codex/auto-progress.toml
```

Use read-only GitHub queries to find an open/Draft discovery PR. Count target inventory only from automatic-discovery documents already present on `origin/<base_branch>` with state `queued`. Do not count directed items, compile repair, stale items, or candidates from an unmerged PR.

Discovery is human-only and never claims the scheduled implementation allowance.

## Workspace lease and slice

Call `prepare-run` with the registered base branch, task type `discover-improvements`, and trigger source `manual`. It requires and exclusively leases the clean original Unity checkout, fetches `origin`, freezes and validates the base policy, and switches the discovery branch in that checkout. Treat its structured result as authoritative; do not reproduce the Git transition in model reasoning.

Use branch:

```text
codex/auto-progress/run-yyyy.mm.dd-xxxxxxxx-discover-improvements
```

Base it exactly on the fetched remote base SHA. Do not create a second worktree or copy tracked Assets or `Library`. Do not allow human or automatic edits during the discovery lease; discovery has no non-target-change bypass.

Build the review slice in this order:

1. Allowed C# files changed on the remote base since the prior discovery cursor.
2. One rotating allowed module not reviewed inside `revisit_after_maintenance_days`.
3. If a valid focus was supplied, use it instead of the cursor selection.

Start with `initial_files`. If useful time remains and fewer than the allowed candidate count have qualified, add at most `expansion_files` per round. Never exceed `max_files`, `max_source_lines`, or `project.max_run_minutes`.

## Candidate document

Use a fresh stable `IMP-...` ID and filename `IMP-...--queued.md`. Record:

- value, confidence, risk, and estimated size;
- module, evidence paths, expected paths, validation profile, and batch affinity;
- remote base commit and evidence-path blob SHAs;
- motivation, exact evidence, smallest scope, acceptance criteria, and known batch conflicts.

The evidence must establish a real maintainability, diagnostics, test, correctness, or reliability improvement. Do not record speculative cleanup or lower the gate to fill inventory.

Check duplicates against queued and implemented documents, the rejection register, and closed discovery PR candidates in cooldown. After cooldown, reuse the prior ID only when evidence or the proposal changed materially; cite the old PR and describe the change.

## PR and ledger

The discovery commit and Draft PR contain candidate documents only. Use:

```text
[AutoProgress][Discovery][RUN-...] Add N candidates
```

The PR must state that no C# or Unity validation ran and that candidates are non-authoritative until merge. It must also contain the rejection-register reminder.

Record `discovery_started`, `discovery_completed`, `commit_created`, `branch_pushed`, `discovery_pr_opened`, and a terminal result as applicable. The completion event should include `reviewed_files`, `reviewed_source_lines`, `candidate_count`, and `improvement_ids`, but never code or raw logs.
