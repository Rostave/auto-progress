# Commit implemented state with each improvement

Each successful improvement keeps an independent rollback commit containing its code, tests, and deterministic `queued`-to-`implemented` document transition. `implemented` means final validation passed and that item commit exists, independently of push or PR delivery, so AutoProgress no longer creates a separate post-PR batch status commit.
