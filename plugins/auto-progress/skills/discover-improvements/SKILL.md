---
name: discover-improvements
description: Manually discover and document review-ready improvement candidates for a configured AutoProgress Unity repository. Use only when a human explicitly asks to replenish the improvement backlog or invokes $discover-improvements, optionally with an allowed-path focus. This task consumes the project's daily activity allowance but never implements candidates or runs Unity.
---

# Discover Improvements

Discover a bounded C# review slice, create batch-ready candidate documents, and open one Draft documentation PR. Read [references/workflow.md](references/workflow.md) before acting.

## Eligibility

1. Require an explicit human invocation. Never invoke this skill from a schedule, `$maintain-project`, or another task type.
2. Check inventory and open discovery reviews before claiming the allowance. Then generate a run ID and call `prepare-run` with task type `discover-improvements`; it freezes the base policy, validates adapters, claims the allowance, and creates the lightweight worktree. The original Unity checkout may be dirty or open.
4. Before claiming the allowance, stop normally when the target inventory is already met or an open/Draft discovery PR exists.
5. Treat an optional focus as a repository-relative path inside `paths.allowed`. It may bypass module revisit cooldown only.

Treat deterministic helper output as authoritative for every condition it covers. Consume successful structured results silently unless the human asks or the final artifact requires disclosure; report failures according to the workflow. Do not spend model reasoning rechecking the same facts or override a failed gate.

## Claim and review

1. Use only the change context admitted by `prepare-run`; do not reproduce its fetch, policy, allowance, branch, or worktree checks.
3. Read the rejection register, authoritative improvement documents, and closed discovery candidates still in cooldown.
4. Review the adaptive slice: use configured initial and expansion rounds and obey the file, source-line, candidate, and run-time hard limits.
5. Admit only candidates with `value >= 1`, `confidence >= 2`, `risk <= 1`, concrete code evidence, an independently testable scope, and no rejection or duplicate match.

## Deliver

- Create one document per candidate from the plugin's `automatic-improvement.md` asset. Record all batch-ready metadata and evidence blob SHAs.
- Do not edit C#, tests, Unity assets, project settings, packages, or status snapshots. Do not run C# validation, Unity MCP, or Unity.
- Before committing, compare candidate document paths with the open implementation PR's changed paths. Any overlap requires human resolution.
- Submit candidate ownership and semantic summaries through the delivery manifest to `finish-run`. It deterministically renders the report, commits, pushes, opens the Draft discovery review, writes ledger events, and removes the worktree. Discovery skips C# and Unity validation by contract.
- Never merge or mark the discovery PR Ready automatically.
- Append bounded ledger events for start, reviewed file/line totals, candidate IDs, commit, push, PR, result, and duration.
- Let `finish-run` remove the temporary worktree after a normal finish and let `recover-run` reconcile interrupted cleanup. Preserve and report the worktree whenever deterministic cleanup would lose unrecovered work.

Zero candidates is a valid result after the allowance is claimed. It does not permit switching to another task type that day.
