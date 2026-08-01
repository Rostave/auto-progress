# AutoProgress configuration

The tracked project contract is `.codex/auto-progress.toml`. Keep local execution history outside the repository under the Codex state directory.

Automatic tasks load this contract from the immutable base SHA recorded by `fetch-base`, including during admission of a checkout currently on another branch. A current checkout's older or broader configuration never expands automatic-task authority.

## Required model

- The implemented schema version is `4`. Older schemas remain readable only for their documented migration and compatibility paths.
- Version 1 must be migrated through a human-invoked `$configure-auto-progress migrate`; automatic tasks stop before claiming the daily allowance when they encounter it.
- `[project].base_branch` is the sole work source and pull-request target.
- `[project].timezone` is an IANA zone, normally `Asia/Shanghai`.
- New configurations explicitly set `[tools].source_control = "git"` and `[tools].review_host = "github"`. Existing schema-version-2 configurations without `[tools]` deterministically use those defaults. Never auto-detect or silently fall back from a configured adapter.
- `[[validation.steps]]` is structured as a program plus argument array. Shell snippets are invalid.
- Budget values are positive and satisfy suggested ≤ hard ≤ directed absolute.
- Paths are repository-relative and may not escape the repository.
- `[workspace].additional_ignore_patterns` is an optional empty-by-default list of repository-relative Git-ignore-style patterns. It filters only otherwise-untracked paths during workspace admission; absolute paths, `..`, repository escape, and `!` negation are invalid, and it never exempts tracked or staged state.
- `[[repository_guidance.documents]]` stores one `agent`, repository-relative `path`, and initial `blob_sha` per guidance document. Configure the default Codex, Claude, and Copilot paths separately. Implementation compares each Git blob independently and rereads only documents whose SHA changed; current cache state is stored outside the repository.
- `[paths].rejection_rules` points to the single human-authored proactive rejection-rule document. `[paths].rejections` remains the directory of one record per rejected `IMP-ID`.
- The implemented Unity MCP contract uses `[unity_mcp].mode = "disabled" | "optional" | "required"`, a trusted `adapter` ID, `transport = "streamable_http"`, a complete loopback `url`, expected project root, and bounded connect/operation timeouts. The deterministic entry point—not the model—calls MCP, validates initialize/tool/resource schemas through the registered adapter, and verifies project identity and content fingerprint.
- In `optional` mode, an open Editor with no usable MCP connection falls back to structured C# validation and does not block workspace admission; record `unity.verified = false` with a reason and keep the review Draft. In `required` mode the same condition blocks delivery.

## Version 1 to version 2 migration

Migration is a manual administration action:

1. Read the existing version 1 file and the plugin's current `assets/auto-progress.toml`.
2. Preserve the configured base branch, timezone, schedule, validation steps, Unity settings, allowed/excluded paths, and any unknown keys.
3. Prepare a version 2 candidate without modifying the tracked file.
4. Add `[batch]`, `[batch_budget]`, and the new adaptive discovery and cooldown keys. Remove obsolete `discovery.max_minutes`; discovery now uses `project.max_run_minutes`.
5. Preserve existing configured document paths unless the human separately asks to move tracked documents.
6. Show a complete no-index diff and obtain explicit confirmation.
7. After confirmation, update only `.codex/auto-progress.toml`, validate it, and leave the change uncommitted for human review.

Never invoke migration from a scheduled run, implementation run, or discovery run.

## Version 2 to version 3 Unity MCP migration

Preview with `python <plugin-root>/scripts/auto_progress.py migrate-config-v3 --config .codex/auto-progress.toml` for a disabled legacy configuration. For an enabled legacy configuration, also provide `--mode optional|required --url <complete-loopback-url>` and confirm the adapter and timeout options. After human approval, repeat the exact command with `--write`, then run `validate-config`.

- New version-3 configurations use `mode`, `transport`, a complete loopback `url`, expected project root, and bounded timeouts.
- Legacy `enabled = false` maps deterministically to `mode = "disabled"`.
- Legacy `enabled = true` requires a human `$configure-auto-progress migrate` action to choose `optional` or `required`, confirm the suggested registered adapter ID, enter the complete endpoint URL, and confirm root/timeouts. Never guess a port or silently disable validation.
- Legacy `provider` may suggest a known adapter mapping but cannot confirm it automatically and is not a network endpoint.
- Automatic tasks encountering an enabled legacy configuration stop before the daily allowance with `unity_mcp_migration_required`.

## Version 3 to version 4 repository-policy migration

Preview with `python <plugin-root>/scripts/auto_progress.py migrate-config-v4 --config .codex/auto-progress.toml --repo <repository-root>`. The migration preserves existing policy, adds `[paths].rejection_rules`, and creates or preserves `[[repository_guidance.documents]]` entries while resolving each configured path to its own committed Git blob SHA. A missing document receives an empty SHA. After human approval, repeat the exact command with `--write`, initialize the rejection-rule document from the plugin asset if it is absent, then run `validate-config`.

Do not bulk-rename legacy `IMP-ID.md` files. They remain readable from frontmatter and migrate to a state-suffixed filename only when a normal lifecycle transition touches that specific improvement.

## Pause semantics

`paused = true` prevents future runs. It does not interrupt an already-started run. If the maintenance day has not started, pausing cancels that day. Resuming does not backfill missed days unless a human explicitly requests an early run.

## Status semantics

Status is derived from ledger events, not inferred from GitHub's contribution graph.

- `completed_days`: distinct maintenance days with `commit_created` or `branch_pushed`.
- `pushed_days`: distinct days with `branch_pushed`.
- `pr_opened`: distinct pull requests recorded as opened.
- `skipped_days`: distinct days ending in `run_skipped` with no completion event.
- `failed_days`: distinct days ending in `run_failed` with no later completion event.
- `attention`: committed/pushed work without a recorded pull request, a recoverable run, or another condition needing human attention.
- `directed_pending`: queued directed items without a terminal item event.
- `allowance_days`: distinct maintenance days claimed by scheduled `implement-batch`; manual tasks are exempt.
- `allowance_days_by_task_type`: scheduled allowance days grouped by task type; currently only `implement-batch` is eligible.
- `legacy_allowance_days`: pre-v4 allowance events retained without rewriting history.
- `implementation`: implementation runs, batches, successfully `implemented` items, and skipped outcomes grouped by reason.
- `discovery`: discovery sessions, reviewed files/lines, candidates proposed, zero-candidate sessions, and discovery PRs.
- `task_types`: per-type result counts and average recorded duration.

A day belongs to exactly one terminal run category in exported summaries; completion wins over failure or skip.
`implemented` means final validation passed and the deterministic item commit exists; push, Draft review, merge, and release are separate facts. Discovery candidates remain non-authoritative until their discovery PR is merged.

## Ledger retention

Append to one JSONL file per project and calendar month. Retain indefinitely by default. Only an explicit human-confirmed purge may delete records. Never store credentials, full build logs, source contents, or machine-specific absolute paths.
