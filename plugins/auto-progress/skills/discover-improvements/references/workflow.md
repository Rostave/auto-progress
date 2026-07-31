# Improvement discovery workflow

## Qualification before allowance

Run:

```text
python <plugin-root>/scripts/auto_progress.py preflight --mode discovery --repo <repo> --config <repo>/.codex/auto-progress.toml
```

Use read-only GitHub queries to find an open/Draft discovery PR. Count target inventory only from automatic-discovery documents already present on `origin/<base_branch>` with state `queued`. Do not count directed items, compile repair, stale items, or candidates from an unmerged PR.

Only after these checks create a run ID and call:

```text
python <plugin-root>/scripts/auto_progress.py claim-allowance --project-id <id> --timezone <zone> --run-id <RUN-ID> --task-type discover-improvements
```

An existing claim for the same run and task type is a safe retry. Any other claim stops the session.

## Worktree and slice

Call `prepare-run` with the registered base branch and task type `discover-improvements`. It fetches `origin`, freezes and validates the base policy, claims the allowance, and creates the temporary lightweight worktree and branch. Treat its structured result as authoritative; do not reproduce the Git transition in model reasoning.

Use branch:

```text
codex/auto-progress/run-yyyy.mm.dd-xxxxxxxx-discover-improvements
```

Base it exactly on the fetched remote base SHA. Do not copy or initialize `Library`.

Build the review slice in this order:

1. Allowed C# files changed on the remote base since the prior discovery cursor.
2. One rotating allowed module not reviewed inside `revisit_after_maintenance_days`.
3. If a valid focus was supplied, use it instead of the cursor selection.

Start with `initial_files`. If useful time remains and fewer than the allowed candidate count have qualified, add at most `expansion_files` per round. Never exceed `max_files`, `max_source_lines`, or `project.max_run_minutes`.

## Candidate document

Use a fresh stable `IMP-...` ID. Record:

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
