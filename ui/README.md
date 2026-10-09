# ui

The read-only dashboard.

## Scope

- Views over `core/` read models: tasks, metered spending, the ledger, the attention log, and each routine with its last run and period spending.
- What came after a task's merges: the index's counts (merges, feedback after, used, reworked by), and on the task page an After merge table (head, time, feedback, used marks, paths, later merges sharing code or only docs, revert and on-branch) and a Used list of deliveries marked with no merge.
- `/audit`: the audit list (`core/audit_sample.py`), as `python -m core audit` prints it, and nothing else. The list is blind: no verdict, finding, forecast, merge state, or score, and no link to the task page, which shows the verdict.
- `/audit/scores`: the verifier's calibration, as `python -m core audit scores` prints it. It is its own page because a stratum's counts beside the list show how many listed candidates hold each verdict, and with one listed candidate, which verdict it holds. The scores measure the verifier's judgement of work quality; no figure there comes from governance instances, grants, or guards.
- `python -m ui` serves `127.0.0.1:8790` (`VALOR_UI_PORT`), aiohttp, GET only; `ui/app.py` holds the pages.
- Read-only. Stops and replies come by message, not here.

Governed by [docs/tech-stack.md](../docs/tech-stack.md) (Surfaces) and [docs/mission.md](../docs/mission.md) (The attention log).

## Imports

- May import: `core/` read models only.
- Imported by: nothing.

## Effect classes

Holds none. The dashboard reads; it performs no effect.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Buttons that stop or change state. Those go through a message and `core/`.
- The public site. That is `site/`.
- Governance or guard dashboards.
