# ui

The read-only dashboard.

## Scope

- Views over `core/` read models: tasks grouped as Needs you, Active and Recent with children under their root, what is alive (`/live`: lock connections, open turns with their processes and subagents, recently ended turns), metered spending, the ledger, the attention log, and each routine with its last run and period spending.
- What came after a task's merges: the index's counts (merges, feedback after, used, reworked by), and on the task page an After merge table (head, time, feedback, used marks, paths, later merges sharing code or only docs, revert and on-branch) and a Used list of deliveries marked with no merge.
- The old dashboard's dark theme and header on every page: "Valor System", the page buttons, and Services (the kernel and each installed bridge, green with uptime or red when not connected; `rollout.services`).
- Children and older routine runs collapse under a ▶ count toggle (CSS only). A click on a task row opens its metadata in a modal (`/task/ID/modal`). Ids are never printed; a row carries its task as `data-task`. `static/style.css` holds the theme; `static/app.js`, the one script, opens the modal and reloads `/live` while none is open.
- `python -m ui` serves `127.0.0.1:8790` (`VALOR_UI_PORT`), aiohttp, GET only; `ui/app.py` holds the pages.
- Read-only. Stops and replies come by message, not here.

Governed by [docs/tech-stack.md](../docs/tech-stack.md) (Surfaces) and [docs/mission.md](../docs/mission.md) (The attention log).

## Imports

- May import: `core/` read models only.
- Imported by: nothing.

## Effect classes

Holds none. The dashboard reads; it performs no effect.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Buttons that stop or change state. Those go through a message and `core/`.
- The public site. That is `site/`.
- Governance or guard dashboards.
