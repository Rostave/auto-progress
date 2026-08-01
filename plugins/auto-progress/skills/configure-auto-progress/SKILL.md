---
name: configure-auto-progress
description: Explicitly initialize, validate, inspect, pause, resume, or change AutoProgress configuration for a Unity repository. Use only when a human directly asks to configure or administer AutoProgress; never invoke it from a daily maintenance run.
---

# Configure AutoProgress

This is a manual administration entry point. Read [references/configuration.md](references/configuration.md) before making changes.

## Supported operations

- Initialize `.codex/auto-progress.toml` from the plugin template.
- Initialize the configured rejection-record directory and copy the proactive rejection-rule asset when absent; never overwrite existing human policy.
- Migrate older policies only when a human explicitly requests `migrate`. Preserve the existing staged migrations; use `migrate-config-v4 --repo <repository>` for v3-to-v4 so every configured guidance document receives its own current Git blob SHA, and add the rejection-rule path only after showing the diff.
- Set the one base branch used both as the work source and pull-request target.
- Configure structured C# validation, discovery limits, change budgets, paths, Unity MCP expectations, retry cooldown, proactive rejection rules, and repository-guidance document locations.
- At configuration time, resolve and record each configured guidance document's Git blob SHA separately. Missing documents use an empty SHA and may appear later.
- Validate configuration and run a read-only repository preflight.
- Explain status counters using their documented event semantics.
- Pause or resume future maintenance days. A pause does not interrupt a run already in progress; if today's run has not started, it cancels today.
- Export project-facing status from the append-only local ledger.
- Purge ledger data only after explicit human confirmation.

## Guardrails

- Never offer a separate pull-request target branch.
- Never edit rejection records or proactive rejection rules from this entry point.
- Never create a directed improvement from this entry point.
- Preserve unknown configuration keys when editing.
- Do not store tokens, credentials, absolute machine-specific paths, or raw build logs in tracked configuration.
- Show the proposed configuration diff before applying material policy changes.
- Never migrate configuration from a daily task or another automatic entry point.

Use `python <plugin-root>/scripts/auto_progress.py --help` for deterministic helper commands.
