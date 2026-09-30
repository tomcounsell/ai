"""Release lifecycle for qualified improvement candidates (#3218, lane 6).

An ``accept`` verdict from ``tools/improvement_eval`` becomes an
``ImprovementRelease`` here: proposed with its surfaces and plans, drilled for
rollback, approved by a human, exposed through the ordinary SDLC pipeline,
observed for a stated window, and rolled back or accepted on the record.

Automated promotion is disabled; ``promotion.py`` names the two preconditions
and refuses every call. ``denylist.py`` keeps the charter, the charter model,
the identity file, this package, the secrets files, and the git hooks off any
candidate's surface list.

Modules import lazily by design; importing this package loads nothing.
"""
