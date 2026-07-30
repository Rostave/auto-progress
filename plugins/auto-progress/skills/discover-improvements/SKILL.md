---
name: discover-improvements
description: Manually discover and document review-ready improvement candidates for a configured AutoProgress Unity repository. Use only when a human explicitly asks to replenish the improvement backlog or invokes $discover-improvements, optionally with an allowed-path focus. This task consumes the project's daily activity allowance but never implements candidates or runs Unity.
---

# Discover Improvements

Discover a bounded C# review slice, create batch-ready candidate documents, and open one Draft documentation PR. Read [references/workflow.md](references/workflow.md) before acting.

## Eligibility

1. Require an explicit human invocation. Never invoke this skill from a schedule, `$maintain-project`, or another task type.
2. Read and validate `.codex/auto-progress.toml`. Version 1 requires `$configure-auto-progress migrate`; stop before claiming the allowance.
3. Run the shared preflight in `discovery` mode. The original Unity checkout may be dirty or open because discovery uses a lightweight worktree.
4. Before claiming the allowance, stop normally when the target inventory is already met or an open/Draft discovery PR exists.
5. Treat an optional focus as a repository-relative path inside `paths.allowed`. It may bypass module revisit cooldown only.

## Claim and review

1. Create one `RUN-...` ID, then atomically claim the allowance with task type `discover-improvements`.
2. Fetch and create a temporary worktree from the latest `origin/<base_branch>` using the run-based discovery branch name.
3. Read the rejection register, authoritative improvement documents, and closed discovery candidates still in cooldown.
4. Review the adaptive slice: use configured initial and expansion rounds and obey the file, source-line, candidate, and run-time hard limits.
5. Admit only candidates with `value >= 1`, `confidence >= 2`, `risk <= 1`, concrete code evidence, an independently testable scope, and no rejection or duplicate match.

## Deliver

- Create one document per candidate from the plugin's `automatic-improvement.md` asset. Record all batch-ready metadata and evidence blob SHAs.
- Do not edit C#, tests, Unity assets, project settings, packages, or status snapshots. Do not run C# validation, Unity MCP, or Unity.
- Before committing, compare candidate document paths with the open implementation PR's changed paths. Any overlap requires human resolution.
- Commit the candidate documents, push the run branch, and open one Draft discovery PR against the configured base branch using `discovery-pr-body.md`.
- Never merge or mark the discovery PR Ready automatically.
- Append bounded ledger events for start, reviewed file/line totals, candidate IDs, commit, push, PR, result, and duration.
- Always remove the temporary worktree after a normal finish. Preserve and report it only when cleanup would lose unrecovered work.

Zero candidates is a valid result after the allowance is claimed. It does not permit switching to another task type that day.
