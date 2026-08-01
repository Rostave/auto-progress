# Reuse discovery workspace and cache repository guidance by document

Discovery reuses one repository-external worktree parked at a detached base revision, while implementation continues in the primary Unity workspace to preserve its `Library`. Repository guidance is configured and cached per document by Git blob SHA so unchanged instructions are not repeatedly read and a change to one agent's document does not invalidate unrelated guidance.
