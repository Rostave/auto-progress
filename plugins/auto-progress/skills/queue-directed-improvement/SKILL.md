---
name: queue-directed-improvement
description: Explicitly create a human-directed AutoProgress improvement item for a configured Unity repository. Use only when a human directly asks to queue or define a forced improvement; never run it automatically and never implement the improvement in the same invocation.
---

# Queue Directed Improvement

This is a manual-only intake entry point. Read [references/directed-improvement.md](references/directed-improvement.md).

## Intake

Collect and write:

- intent and reason;
- acceptance criteria;
- preferred or forbidden paths;
- priority and optional deadline;
- explicitly requested exemptions from automatic-task rules;
- optional budget overrides within the configured absolute directed-work caps.

Generate an ID shaped like `IMP-2026.07.29-a1b2c3d4`. Compare the request with the rejection list and disclose matches, but do not block it: a human-directed improvement fully bypasses that list.

## Boundaries

- Do not implement, branch, commit, push, or open a pull request for the improvement in this invocation.
- Do not infer exemptions. Anything not named remains governed by normal admission and validation rules.
- Never allow an exemption from hard safety, secret handling, human-only merge/conflict resolution, or the single configured pull-request target.
- Only a human may modify the rejection list.
- Store one item per tracked document and append a corresponding local ledger event.

If Codex believes a rejected class now has a reasonable exception, ask the human to “拓宽、修改或放行拒绝清单中的哪一项”; do not edit the list.
