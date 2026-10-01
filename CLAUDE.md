# CLAUDE.md

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

This branch is a rebuild of Valor in its setup phase.

- The previous system's code and docs live unchanged on the `main` branch.
  Read them with `git show main:<path>`; never import from them.
- The cori copy is `tomcounsell/cori` at commit
  0336d10642534f344e002d9a7b7abb22eb40a8a1.
- The plan governing this phase is `docs/plans/valor-cori-rebuild-setup.md`.
  Follow it step by step and do not run steps out of order.
