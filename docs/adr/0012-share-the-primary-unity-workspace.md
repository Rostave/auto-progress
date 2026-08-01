# Share the primary Unity workspace across task types

Discovery and implementation both lease the original Unity checkout instead of creating a second Git worktree that duplicates large tracked Assets; discovery therefore requires a clean, exclusive checkout and cannot run beside human work. During implementation delivery, deterministic code may preserve path-disjoint human changes by freezing their fingerprints, unstaging them, excluding them from commits, and proving they can survive restoration; discovery never receives this bypass because source changes invalidate its evidence.
