---
name: record-improvement-rejection
description: Record or complete a human-decided AutoProgress rejection for one or more existing IMP IDs. Use only when a human explicitly asks to reject named improvements or complete their rejection records; never infer the rejection decision from a closed or unmerged review.
---

# Record Improvement Rejection

Create or complete one tracked rejection record per explicitly supplied improvement ID.

## Intake

Require the human to provide every `IMP-...` ID and a rejection reason for each. If either is missing, ask for all missing required values together. You may infer likely intent and recommend wording, but label it as a suggestion and require confirmation before writing it.

Default the remaining fields unless the human overrides them:

- rejected by: current user;
- rejected at: current date in the configured project timezone;
- exclusion pattern: infer from the confirmed reason and improvement evidence;
- scope: all code.

Read the matching improvement document, relevant review context, existing rejection record, and configured proactive rejection rules when available.

## Edit boundaries

- Create or update `<paths.rejections>/<IMP-ID>.md` from the plugin rejection-record asset.
- Fill missing fields and improve wording without changing meaning.
- Preserve human-authored text. Before broadening or narrowing an exclusion pattern or scope, show the proposed change and obtain explicit confirmation.
- Never delete a rejection record or edit the proactive rejection-rule document.
- Never decide that an improvement is rejected merely because a review was closed, not merged, or criticized.
- Do not modify functional code, improvement implementation, or unrelated policy in this invocation.

## Deliver

Use a separate lightweight worktree and configuration branch. Create one Draft review containing only the named IMP-ID rejection records. This manual administration task never claims the scheduled implementation allowance and never merges or marks the review Ready automatically.
