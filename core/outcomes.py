"""What came after a task's merges and deliveries: Tom's feedback after a
merge, later merges by other tasks that touched the same paths, whether the
merge was reverted or is still on its branch, and whether someone used the
work (`delivery.used`, written only by `python -m core used`).

What a merge landed (`landed`: the head it came after, its own commits, and
the paths they change) is recorded by the broker on the merge's
`effect.intent` at release, from the kernel mirror (`landed`), so rework is
a fold over rows (`done_merges`, `rework`). Git is read on the status
surfaces only for revert and on-branch, from the kernel's cache of the
target as last fetched (`revert`), never from a network and never from a
directory a turn writes.

Everything here is shown beside spending and attention. Nothing reads it to
decide anything: not the router, the broker's decision, the fold, or a
verdict. This module imports `ledger`, `machine`, `git`, and `workspace`,
never `tasks` or `broker`, so both can import it.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from core import git, ledger, machine, workspace

USED = "delivery.used"
NO_KERNEL_COPY = "no kernel copy"
PRIVATE_TARGET = "private target"
NO_CACHE = "no cache of the target"
NOT_FETCHED = "cache not fetched since the merge"
NOT_RECORDED = "paths not recorded"
# The line `git revert` writes in the body, and GitHub's revert button
# writes under its "Reverts owner/repo#N" title.
REVERTS = re.compile(r"This reverts commit ([0-9a-f]{40})(?![0-9a-f])")


def _at(row: dict[str, Any]) -> str | None:
    return row["at"].isoformat() if row.get("at") is not None else None


def _provenance(p: dict[str, Any]) -> dict[str, Any]:
    recorded = p.get("provenance") or {}
    return {k: recorded.get(k) for k in ("by", "via", "at", "role_played")}


def merges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One task's merges, in outcome order: each `effect.outcome` with
    `kind: done` whose effect is an `effect.held` with `action_type:
    merge`. Each carries its `effect_id`, `head_sha`, `url`, and
    `target_branch` (from the held row), `merged_at` and `event_id` (the
    outcome row), `landed` (from the intent; None for a merge recorded
    before it was), and `delivery_event_id`, the latest `task.delivered`
    before its `effect.held`. A failed merge is not one."""
    held: dict[str, dict[str, Any]] = {}
    intents: dict[str, dict[str, Any]] = {}
    delivered = None
    found = []
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "task.delivered":
            delivered = row["id"]
        elif kind == "effect.held" and p.get("action_type") == "merge":
            held[p["effect_id"]] = {"payload": p.get("payload") or {}, "delivery_event_id": delivered}
        elif kind == "effect.intent":
            intents[p.get("effect_id")] = p
        elif kind == "effect.outcome" and p.get("kind") == "done" and p.get("effect_id") in held:
            h = held[p["effect_id"]]
            found.append(
                {
                    "task_id": row["task_id"],
                    "effect_id": p["effect_id"],
                    "head_sha": h["payload"].get("head_sha"),
                    "url": h["payload"].get("url"),
                    "target_branch": h["payload"].get("target_branch"),
                    "merged_at": _at(row),
                    "event_id": row["id"],
                    "landed": (intents.get(p["effect_id"]) or {}).get("landed"),
                    "delivery_event_id": h["delivery_event_id"],
                }
            )
    return found


def deliveries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each `task.delivered` row: its event id, candidate, outcome, summary,
    and time."""
    return [
        {
            "event_id": row["id"],
            "candidate": row["payload"].get("candidate"),
            "outcome": row["payload"].get("outcome"),
            "summary": row["payload"].get("summary"),
            "delivered_at": _at(row),
        }
        for row in rows
        if row["type"] == "task.delivered"
    ]


def _marks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "used_id": row["payload"].get("used_id"),
            "delivery_event_id": row["payload"].get("delivery_event_id"),
            "effect_id": row["payload"].get("effect_id"),
            "head_sha": row["payload"].get("head_sha"),
            "note": row["payload"].get("note"),
            "provenance": _provenance(row["payload"]),
        }
        for row in rows
        if row["type"] == USED
    ]


def used_count(marks: list[dict[str, Any]]) -> int:
    """The marks that are not role-played: a stand-in using a feature is not
    real use."""
    return sum(1 for m in marks if m["provenance"]["role_played"] is not True)


def _feedback(rows: list[dict[str, Any]], merge: dict[str, Any]) -> list[dict[str, Any]]:
    """Every `feedback.given` row after the merge's outcome and before the
    task's next delivery: feedback on the merged work. Feedback given in
    `merge` follows a newer delivery, so it belongs to no merge."""
    found = []
    for row in rows:
        if row["id"] <= merge["event_id"]:
            continue
        if row["type"] == "task.delivered":
            break
        if row["type"] == "feedback.given":
            p = row["payload"]
            found.append(
                {"feedback_id": p.get("feedback_id"), "text": p.get("text"), "provenance": _provenance(p)}
            )
    return found


# -- every task's merges ---------------------------------------------------------------


async def done_merges(conn, url: str | None = None, branch: str | None = None) -> list[dict[str, Any]]:
    """Every task's done merges, in outcome order, from one query: each
    `effect.held` merge joined to its `effect.outcome` with `kind: done` and
    its `effect.intent`, optionally only those to `url` and `branch`. Each
    as `task_id`, `effect_id`, `head_sha`, `url`, `target_branch`,
    `merged_at`, `event_id` (the outcome row), and `landed`."""
    where = ""
    args: tuple[Any, ...] = ()
    if url is not None:
        where += " AND h.payload->'payload'->>'url' = %s"
        args += (url,)
    if branch is not None:
        where += " AND h.payload->'payload'->>'target_branch' = %s"
        args += (branch,)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT h.task_id, h.payload->>'effect_id' AS effect_id, "
            "h.payload->'payload'->>'head_sha' AS head_sha, h.payload->'payload'->>'url' AS url, "
            "h.payload->'payload'->>'target_branch' AS target_branch, o.at, o.id AS event_id, "
            "i.payload->'landed' AS landed "
            "FROM events h JOIN events o ON o.task_id = h.task_id AND o.type = 'effect.outcome' "
            "AND o.payload->>'effect_id' = h.payload->>'effect_id' AND o.payload->>'kind' = 'done' "
            "LEFT JOIN events i ON i.task_id = h.task_id AND i.type = 'effect.intent' "
            "AND i.payload->>'effect_id' = h.payload->>'effect_id' "
            "WHERE h.type = 'effect.held' AND h.payload->>'action_type' = 'merge'" + where + " ORDER BY o.id",
            args,
        )
        found = await cur.fetchall()
    return [{**{k: v for k, v in r.items() if k != "at"}, "merged_at": _at(r)} for r in found]


def rework(merge: dict[str, Any], done: list[dict[str, Any]]) -> dict[str, Any]:
    """Other tasks' merges to the same url and branch whose outcome came
    after this one's, by the paths both recorded, split with
    `machine.is_doc_path`: `later` shares a code path, `later_docs` only
    doc paths; `later_unknown` names those that recorded no paths, so an
    unknown never reads as no rework. With no paths of its own, `later` and
    `later_docs` are None and `rework_why` says so."""
    after = [
        d
        for d in done
        if d["task_id"] != merge["task_id"]
        and d["url"] == merge["url"]
        and d["target_branch"] == merge["target_branch"]
        and d["event_id"] > merge["event_id"]
    ]
    unknown = [d["task_id"] for d in after if (d.get("landed") or {}).get("paths") is None]
    own = (merge.get("landed") or {}).get("paths")
    if own is None:
        return {"later": None, "later_docs": None, "later_unknown": unknown, "rework_why": NOT_RECORDED}
    later, later_docs = [], []
    merged = datetime.fromisoformat(merge["merged_at"]) if merge.get("merged_at") else None
    for d in after:
        paths = (d.get("landed") or {}).get("paths")
        if paths is None:
            continue
        shared = sorted(set(own) & set(paths))
        if not shared:
            continue
        entry = {
            "task_id": d["task_id"],
            "effect_id": d["effect_id"],
            "merged_at": d["merged_at"],
            "days_after": round((datetime.fromisoformat(d["merged_at"]) - merged).total_seconds() / 86400, 1)
            if merged and d.get("merged_at")
            else None,
            "shared_code": [p for p in shared if not machine.is_doc_path(p)],
            "shared_docs": [p for p in shared if machine.is_doc_path(p)],
        }
        (later if entry["shared_code"] else later_docs).append(entry)
    return {"later": later, "later_docs": later_docs, "later_unknown": unknown, "rework_why": None}


# -- what a merge landed, recorded at release ------------------------------------------


def _target_cache(brief) -> Path | None:
    """The kernel's cache of the task's merge target, when the task has a
    kernel mirror (its work directory is the mirror's grandparent)."""
    if not brief.mirror or not brief.origin_url:
        return None
    return workspace.cache_path(brief.origin_url, Path(brief.mirror).parent.parent)


def own_changes(
    mirror: str | Path, cache: Path | None, before: str | None, head: str, earlier: list[str], branch: str
) -> tuple[list[str], list[str]]:
    """The merge's own commits (oldest first) and the paths they change,
    read in the kernel mirror with the target cache's objects borrowed:
    from `before` to `head`, leaving out every earlier done merge head and
    the cache's tip of `branch`, so other tasks' work and hand-landed
    commits the candidate took in are not its own. A `before` or `head`
    the repository lacks raises `GitError`, so the merge records nulls
    rather than an empty list; an excluded head it lacks is skipped
    (`--ignore-missing`), as nothing reachable here can be under it. Both paths of a rename are listed. Names are read as bytes and
    decoded with `errors="replace"`."""
    borrow: dict[str, str] = {}
    tip: list[str] = []
    if cache is not None and cache.is_dir():
        borrow = {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(cache / "objects")}
        if git.has_commit(cache, f"refs/heads/{branch}"):
            tip = [git.trusted(cache, "rev-parse", f"refs/heads/{branch}^{{commit}}")]
    for rev in (before, head):
        if rev:
            git.trusted(mirror, "cat-file", "-e", f"{rev}^{{commit}}", extra_env=borrow)
    out = git.trusted(
        mirror,
        "log", "--no-merges", "--ignore-missing", "-z", "--format=%x00%H", "--name-only", "--no-renames",
        f"{before}..{head}" if before else head, "--not", *earlier, *tip,
        extra_env=borrow, strip=False, text=False,
    )  # fmt: skip
    commits: list[str] = []
    paths: set[str] = set()
    sha_next, first = False, False
    for token in out.split(b"\0"):
        if not token:
            sha_next = True
        elif sha_next:
            commits.append(token.decode())
            sha_next, first = False, True
        else:
            if first and token.startswith(b"\n"):
                token = token[1:]
            first = False
            paths.add(token.decode(errors="replace"))
    return commits[::-1], sorted(paths)


async def landed(conn, brief, rows: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """What a merge about to be performed lands, for its `effect.intent`:
    `before` (the task's previous done merge's head, or the Brief's base),
    `commits`, and `paths` (`own_changes`). Read only from the kernel
    mirror: a task without one records null commits and paths, "no kernel
    copy", and no git runs. Any failure records nulls with the exception's
    type under `why`; nothing here stops a merge."""
    before = None
    try:
        own = merges(rows)
        before = own[-1]["head_sha"] if own else brief.base_sha
        if not brief.mirror:
            return {"before": before, "commits": None, "paths": None, "why": NO_KERNEL_COPY}
        async with conn.transaction():  # a savepoint: a failed read leaves the release's transaction usable
            earlier = [
                d["head_sha"] for d in await done_merges(conn, payload["url"], payload["target_branch"])
            ]
        commits, paths = await git.threaded(
            own_changes,
            brief.mirror,
            _target_cache(brief),
            before,
            payload["head_sha"],
            [e for e in earlier if e],
            payload["target_branch"],
        )
        return {"before": before, "commits": commits, "paths": paths, "why": None}
    except Exception as exc:  # noqa: BLE001  recording what landed never stops the merge
        return {"before": before, "commits": None, "paths": None, "why": type(exc).__name__}


# -- revert and on-branch, read from the kernel's cache --------------------------------


def revert(brief, merge: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Whether the merge's head is still on its branch and which later
    commits revert one of its commits, read from the kernel's cache of the
    target as last fetched (`as_of`, its `FETCH_HEAD` time). Returns the
    reading and None, or None and why there is none: a task on its own
    origin (`PRIVATE_TARGET`), a `--workspace` task (`NO_KERNEL_COPY` of
    the target), no cache of the target, a head the cache lacks, or git's
    error. A revert is a commit whose body holds `This reverts commit
    <40 hex>` naming one of the merge's recorded commits; with no recorded
    commits, `reverted_by` is None."""
    if brief.push_url and brief.origin_url == brief.push_url:
        return None, PRIVATE_TARGET
    if not brief.mirror:
        return None, f"{NO_KERNEL_COPY} of the target"
    cache = _target_cache(brief)
    if cache is None or not cache.is_dir():
        return None, NO_CACHE
    head, branch = merge["head_sha"], merge["target_branch"]
    try:
        if not head or not git.has_commit(cache, head):
            return None, NOT_FETCHED
        on_branch, why = git.ancestry(cache, head, f"refs/heads/{branch}")
        if on_branch is None:
            return None, f"git: {why}"
        commits = set((merge.get("landed") or {}).get("commits") or ())
        reverted_by = None
        if (merge.get("landed") or {}).get("commits") is not None:
            out = git.trusted(
                cache, "log", "--format=%H%x00%B%x1e", f"{head}..refs/heads/{branch}", strip=False, text=False
            )
            reverted_by = []
            for record in out.decode(errors="replace").split("\x1e"):
                sha, _, body = record.strip("\n").partition("\x00")
                named = [c for c in REVERTS.findall(body) if c in commits]
                if sha and named:
                    reverted_by.append({"sha": sha, "reverts": named})
    except git.GitError as exc:
        return None, f"git: {exc}"
    fetched = cache / "FETCH_HEAD"
    as_of = datetime.fromtimestamp(fetched.stat().st_mtime, UTC).isoformat() if fetched.exists() else None
    return {"on_branch": on_branch, "reverted_by": reverted_by, "as_of": as_of, "source": "cache"}, None


# -- the status surfaces ----------------------------------------------------------------


async def after_merge(conn, brief, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`after_merge`, each of the task's merges with the feedback after it,
    its used marks and `used_count`, its rework (`rework`; None with
    `PRIVATE_TARGET` on a task's own origin), and its revert reading; and
    `deliveries_used`, each delivery with marks that name no merge."""
    marks = _marks(rows)
    private = bool(brief.push_url and brief.origin_url == brief.push_url)
    done: dict[tuple[str, str], list[dict[str, Any]]] = {}
    found = []
    for m in merges(rows):
        used = [u for u in marks if u["effect_id"] == m["effect_id"]]
        if private:
            later = {"later": None, "later_docs": None, "later_unknown": None, "rework_why": PRIVATE_TARGET}
        else:
            pair = (m["url"], m["target_branch"])
            if pair not in done:
                done[pair] = await done_merges(conn, *pair)
            later = rework(m, done[pair])
        reading, why = await git.threaded(revert, brief, m)
        found.append(
            {
                **m,
                "paths": (m["landed"] or {}).get("paths"),
                "feedback": _feedback(rows, m),
                "used": used,
                "used_count": used_count(used),
                **later,
                "revert": reading,
                "revert_why": why,
            }
        )
    used_deliveries = []
    for d in deliveries(rows):
        used = [u for u in marks if u["effect_id"] is None and u["delivery_event_id"] == d["event_id"]]
        if used:
            used_deliveries.append({**d, "used": used, "used_count": used_count(used)})
    return {"after_merge": found, "deliveries_used": used_deliveries}


def summary(rows: list[dict[str, Any]], done: list[dict[str, Any]]) -> dict[str, int]:
    """A task's counts for the index, from its rows and every task's done
    merges, with no git: `merges`, `feedback_after` (feedback after a
    merge), `used` (marks not role-played), and `reworked` (other tasks'
    later merges sharing a code path with one of its merges)."""
    own = merges(rows)
    reworked = set()
    for m in own:
        for d in rework(m, done)["later"] or ():
            reworked.add(d["effect_id"])
    return {
        "merges": len(own),
        "feedback_after": sum(len(_feedback(rows, m)) for m in own),
        "used": used_count(_marks(rows)),
        "reworked": len(reworked),
    }


# -- the used mark ----------------------------------------------------------------------


async def mark_used(
    conn,
    task_id: str,
    *,
    by: str,
    via: str = "the command line",
    role_played: bool = False,
    note: str | None = None,
    delivery: int | None = None,
) -> str:
    """Append one `delivery.used` row on the task, under its lock, and
    return its `used_id`. The row names a delivery: `delivery` when given;
    else the one the task's latest done merge carried; else the latest.
    `effect_id` and `head_sha` are those of the done merge that carried it,
    or None. `by` names who used the work. Raises `LookupError` for an
    unknown task, one with no delivery, or a `delivery` not the task's.
    The fold ignores the row, so the task's state does not move."""
    async with conn.transaction():
        known = await (
            await conn.execute("SELECT 1 FROM documents WHERE kind = 'task' AND id = %s", (task_id,))
        ).fetchone()
        if known is None:
            raise LookupError(f"no task {task_id}")
        await ledger.lock(conn, f"task:{task_id}")
        rows = await ledger.read(conn, task_id)
        found = {d["event_id"]: d for d in deliveries(rows)}
        if not found:
            raise LookupError(f"task {task_id} has no delivery to mark used")
        own = merges(rows)
        if delivery is not None:
            if delivery not in found:
                raise LookupError(f"task {task_id} has no delivery {delivery}")
            named = found[delivery]
        elif own and own[-1]["delivery_event_id"] in found:
            named = found[own[-1]["delivery_event_id"]]
        else:
            named = found[max(found)]
        carried = [m for m in own if m["delivery_event_id"] == named["event_id"]]
        merge = carried[-1] if carried else None
        used_id = ledger.new_id()
        await ledger.append(
            conn,
            task_id,
            USED,
            {
                "used_id": used_id,
                "delivery_event_id": named["event_id"],
                "candidate": named["candidate"],
                "effect_id": merge["effect_id"] if merge else None,
                "head_sha": merge["head_sha"] if merge else None,
                "note": note,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return used_id
