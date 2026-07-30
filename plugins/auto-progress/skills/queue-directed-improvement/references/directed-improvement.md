# Human-directed improvement contract

Create one tracked Markdown file per item under the configured directed-item directory.

Required fields:

- improvement ID;
- state (`pending` initially);
- priority;
- intent and reason;
- acceptance criteria;
- requested scope;
- explicit exemptions;
- creator and created-at timestamp.

Optional fields:

- deadline;
- preferred and forbidden paths;
- budget override;
- implementation notes.

The rejection list never blocks a directed item. Still show any matching rejection entries so the human understands the exception. An exemption applies only to the named automatic-task constraint. These rules are never exemptible:

- secrets and credential safety;
- no automatic merge, conflict resolution, rebase, reset, clean, stash, or force-push;
- the configured base branch is the only pull-request target;
- human review is required;
- validations and failures must be reported truthfully.

Creating the item is not authorization to implement it immediately. The daily maintenance workflow selects it according to priority and readiness.
