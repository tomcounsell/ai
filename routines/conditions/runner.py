"""The conditions routine: each committed decision's watched conditions,
checked against the business's own data (docs/routines.md, Watched
conditions; docs/business.md, item 7).

For every project spec, the runner reads `decisions/conditions.toml` from
the business's repository on its committed branch (`core.conditions`). The
run is a node under the routine's objective, and each condition checked is a
row on it: `condition.read` (a numeric condition: the figure, where, when),
`condition.observed` (an observation: what Valor saw, where, when), or
`condition.unread` (no adapter for its source is granted, or the adapter
failed). A met condition puts the decision's original reasoning, the
condition, and what changed on the next strategy meeting's agenda
(`core.agenda`), once. It sends Tom nothing.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from core import agenda, conditions, ledger, tasks, workspace
from core.routines import Context, Ran
from core.settings import resolve_model

Adapter = conditions.Source | conditions.Observer


async def _check(
    business: str, c: conditions.Condition, adapters: Mapping[str, Any], at: datetime
) -> tuple[str, dict[str, Any], bool]:
    """The row a condition's check writes and whether it was met."""
    adapter = adapters.get(c.source)
    base = {"business": business, "condition": c.id, "text": c.text, "source": c.source}
    if c.numeric:
        if adapter is None or not hasattr(adapter, "read"):
            return "condition.unread", {**base, "why": f"no access to {c.source} is granted"}, False
        try:
            got = await adapter.read(business, c.metric, at)
        except Exception as exc:  # noqa: BLE001  an adapter's failure is a row on the run, never fatal
            return "condition.unread", {**base, "why": f"{c.source}: {exc}"}, False
        met = c.met_by(got.value)
        row = {**base, "metric": c.metric, "value": got.value, "where": got.where, "at": got.at.isoformat()}
        row |= {"above": c.above} if c.above is not None else {"below": c.below}
        return "condition.read", {**row, "met": met}, met
    if adapter is None or not hasattr(adapter, "observe"):
        return "condition.unread", {**base, "why": f"no access to {c.source} is granted"}, False
    try:
        seen = await adapter.observe(business, c, at)
    except Exception as exc:  # noqa: BLE001  as above
        return "condition.unread", {**base, "why": f"{c.source}: {exc}"}, False
    row = {**base, "saw": seen.saw, "where": seen.where, "at": seen.at.isoformat(), "met": seen.met}
    return "condition.observed", row, seen.met


def runner(
    adapters: Mapping[str, Adapter],
    *,
    projects: Callable[[], list[workspace.Spec]] = workspace.Spec.all,
    work: Callable[[], Path] = workspace.work_dir,
):
    """The `conditions` runner over the granted `adapters`, by source name."""

    async def run(ctx: Context) -> Ran:
        read: list[conditions.Committed] = []
        unread: list[str] = []
        for spec in projects():
            try:
                found = conditions.committed(spec, work())
            except (workspace.Refused, conditions.Malformed) as exc:
                unread.append(f"{spec.name}: {exc}")
                continue
            if found is not None and any(d.conditions for d in found.decisions):
                read.append(found)
        not_read = "".join(f"; not read, {u}" for u in unread)
        if not read:
            return Ran(None, "nothing_due", f"no committed decision carries conditions{not_read}")
        run_id = await tasks.start_child(
            ctx.conn,
            ctx.objective,
            ceiling=ctx.routine.ceiling,
            marker={"objective": ctx.routine.name},
            by="routine",
            via=f"python -m core routine {ctx.routine.name}",
            instruction=f"routine {ctx.routine.name}: conditions of {ctx.now.date()}",
            model=resolve_model(ctx.routine.model),
            routine=ctx.routine.name,
        )
        checked = met = put = 0
        for found in read:
            for d in found.decisions:
                for c in d.conditions:
                    kind, row, is_met = await _check(found.business, c, adapters, ctx.now)
                    row |= {"decision": d.id, "commit": found.commit}
                    checked += 1
                    if is_met:
                        met += 1
                        k = conditions.key(found.business, d.id, c)
                        item_id = await agenda.put(
                            ctx.conn,
                            {
                                "key": k,
                                "business": found.business,
                                "decision": d.id,
                                "decided": d.decided.isoformat(),
                                "commit": found.commit,
                                "reasoning": d.reasoning,
                                "reasoning_text": found.reasoning.get(d.id),
                                "condition": {
                                    "id": c.id,
                                    "text": c.text,
                                    "source": c.source,
                                    **({"metric": c.metric} if c.metric else {}),
                                    **({"above": c.above} if c.above is not None else {}),
                                    **({"below": c.below} if c.below is not None else {}),
                                },
                                "changed": {x: row[x] for x in ("value", "saw", "where", "at") if x in row},
                                "run": run_id,
                            },
                        )
                        put += item_id is not None
                        row["agenda_item"] = (
                            item_id if item_id is not None else await agenda.find(ctx.conn, k)
                        )
                    await ledger.append(ctx.conn, run_id, kind, row)
        summary = f"{checked} conditions checked, {met} met, {put} put on the agenda{not_read}"
        return Ran(run_id, "finished", summary)

    return run
