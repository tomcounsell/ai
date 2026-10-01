"""Writing verdict rows, and what the join does when it sends a task to merge.

Each writer takes the task's advisory lock, folds, refuses a row the fold
would ignore (a task in another state, a legacy task, a stopped one), and
appends in one transaction. A check verdict that completes a join to
`merge` writes `task.delivered` in the same transaction; `ensure_merge`
then requests the merge effect, and the router calls it again on every run
that finds the task in `merge`, so a crash between the two strands nothing.

Until the judge (1.3) and the critique and check runners (1.4) exist, a
person records those verdicts by hand (`python -m core verdict`), each row
`leg: manual` with provenance. `manual_allowed` refuses a stage that has a
runner, so the stand-in closes as runners arrive.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from core import broker, git, ledger, machine, tasks
from core.machine import Check, State

MANUAL_STAGES: dict[str, State | Check] = {
    "judge": State.JUDGE,
    "critique": State.CRITIQUE,
    "test": Check.TEST,
    "review": Check.REVIEW,
    "docs": Check.DOCS,
}


class VerdictRefused(LookupError):
    pass


def manual_allowed(stage: State | Check, runners: Mapping) -> None:
    if stage in runners:
        raise VerdictRefused(f"{stage} has a runner; its verdict is the runner's to record")


@dataclass(frozen=True)
class InstanceSpec:
    """A governance instance as a reviewer names it: a path and a line
    inside the hunk, in the candidate's diff. The kernel reads the hunk
    itself."""

    path: str
    line: int
    summary: str = ""
    incident: str | None = None
    mission_item: str | None = None


async def _fold(conn, task_id: str, *stages: State) -> tuple[list[dict], machine.Fold]:
    rows = await ledger.read(conn, task_id)
    if not rows:
        raise VerdictRefused(f"no task {task_id}")
    f = machine.fold(rows)
    if f.legacy:
        raise VerdictRefused(f"task {task_id} predates the state machine")
    if f.state is State.STOPPED:
        raise VerdictRefused(f"task {task_id} is stopped")
    if f.state not in stages:
        raise VerdictRefused(f"task {task_id} is in {f.state}, not {' or '.join(stages)}")
    return rows, f


def _manual(leg: str, by: str, via: str, role_played: bool) -> dict[str, Any]:
    return {"provenance": ledger.provenance(by, via, role_played)} if leg == "manual" else {}


def _would(rows: list[dict], kind: str, payload: dict[str, Any]) -> machine.Fold:
    return machine.fold([*rows, {"id": None, "type": kind, "payload": payload}])


def _findings(items: Iterable) -> list[dict[str, str]]:
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append({"kind": str(item.get("kind", "finding")), "text": str(item["text"])})
        elif ":" in str(item):
            kind, text = str(item).split(":", 1)
            out.append({"kind": kind.strip(), "text": text.strip()})
        else:
            out.append({"kind": "finding", "text": str(item)})
    return out


async def record_judge(
    conn, task_id: str, verdict: str, *, leg: str = "manual", by: str = "tom", via: str = "the command line",
    role_played: bool = False,
) -> int:  # fmt: skip
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        await _fold(conn, task_id, State.JUDGE)
        return await ledger.append(
            conn,
            task_id,
            "judge.decided",
            {
                "verdict": verdict,
                "leg": leg,
                "judgement_id": None,
                "guard_id": machine.GUARD_JUDGE if verdict == "thin" else None,
                **_manual(leg, by, via, role_played),
            },
        )


async def record_critique(
    conn, task_id: str, verdict: str, *, findings: Iterable = (), raised: Mapping[str, int] | None = None,
    leg: str = "manual", model: str | None = None, usd_micros: int = 0, by: str = "tom",
    via: str = "the command line", role_played: bool = False,
) -> int:  # fmt: skip
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        rows, f = await _fold(conn, task_id, State.CRITIQUE)
        payload = {
            "plan_sha256": f.plan["sha256"],
            "verdict": verdict,
            "findings": _findings(findings),
            "raised": dict(raised or {}),
            "leg": leg,
            "model": model,
            "usd_micros": usd_micros,
            **_manual(leg, by, via, role_played),
        }
        sent_back = verdict == "revise" and _would(rows, "critique.decided", payload).state is State.PLAN
        payload["guard_id"] = machine.GUARD_CRITIQUE if sent_back else None
        return await ledger.append(conn, task_id, "critique.decided", payload)


def _instances(workspace: str, older: str, newer: str, specs: Iterable[InstanceSpec]) -> list[dict[str, Any]]:
    """Each named instance, its id computed from the real diff, never from
    text a reviewer supplied. An instance not found in the diff is refused."""
    out = []
    for spec in specs:
        hunk = git.hunk_at(workspace, older, newer, spec.path, spec.line)
        if hunk is None:
            raise VerdictRefused(
                f"no added lines at {spec.path}:{spec.line} in the diff {older[:12]}..{newer[:12]}"
            )
        out.append(
            {
                "id": hunk.id(),
                "path": spec.path,
                "line": spec.line,
                "context": hunk.context,
                "summary": spec.summary,
                "incident": spec.incident,
                "mission_item": spec.mission_item,
            }
        )
    return out


async def record_check(
    conn, task_id: str, check: Check, verdict: str, *, findings: Iterable = (),
    governance: Iterable[InstanceSpec] = (), head: str | None = None, command: str | None = None,
    failures: Iterable[str] = (), behaviors: Iterable[str] = (), leg: str = "manual",
    model: str | None = None, usd_micros: int = 0, by: str = "tom", via: str = "the command line",
    role_played: bool = False,
) -> machine.Fold:  # fmt: skip
    """Record one branch's verdict on the current candidate. Returns the
    fold after it; when it completes a join to `merge`, `task.delivered` is
    written with it."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        rows, f = await _fold(conn, task_id, State.CHECKS)
        b = await tasks.brief(conn, task_id)
        c = f.candidate
        payload: dict[str, Any] = {
            "candidate": {"sha": c.sha, "turn_id": c.turn_id},
            "verdict": verdict,
            "findings": _findings(findings),
            "leg": leg,
            "model": model,
            "usd_micros": usd_micros,
            **_manual(leg, by, via, role_played),
        }
        specs = list(governance)
        if check is Check.TEST:
            failures, behaviors = list(failures), list(behaviors)
            payload["command"] = command
            payload["failures"] = failures
            payload["behaviors"] = behaviors
            payload["findings"] += [{"kind": "test failure", "text": t} for t in failures]
            payload["findings"] += [{"kind": "untested behavior", "text": t} for t in behaviors]
            payload["guard_id"] = machine.GUARD_BREADTH if behaviors else None
        if check is Check.DOCS:
            head = head or c.sha
            if head != c.sha:
                if not git.is_ancestor(b.workspace, c.sha, head):
                    raise VerdictRefused(f"{head} does not descend from the candidate {c.sha}")
                if git.merges_between(b.workspace, c.sha, head):
                    raise VerdictRefused("the docs commits hold a merge commit")
            payload["head"] = head
            payload["paths"] = git.diff_paths(b.workspace, c.sha, head) if head != c.sha else []
        if check in (Check.REVIEW, Check.DOCS):
            older, newer = (b.base_sha, c.sha) if check is Check.REVIEW else (c.sha, payload["head"])
            instances = _instances(b.workspace, older, newer, specs) if specs else []
            payload["governance"] = {"adds": bool(instances), "instances": instances}
            ungranted = [i for i in instances if i["id"] not in f.granted]
            if check is Check.REVIEW:
                if verdict == "pass" and ungranted:
                    raise VerdictRefused(
                        "a review naming an ungranted governance instance is governance_refused"
                    )
                if verdict == "governance_refused" and not ungranted:
                    raise VerdictRefused(
                        "governance_refused names at least one instance not yet granted; "
                        "with every instance granted, such a review is pass"
                    )
        elif specs:
            raise VerdictRefused("only review and docs answer the governance boolean")
        kind = f"{check.value}.decided"
        after = _would(rows, kind, payload)
        if after.join is not None and f.join is None and after.join.row in (3, 5):
            payload["guard_id"] = machine.GUARD_REVIEW
        event_id = await ledger.append(conn, task_id, kind, payload)
        after = machine.fold(await ledger.read(conn, task_id))
        if f.state is State.CHECKS and after.state is State.MERGE and after.join is not None:
            await ledger.append(conn, task_id, "task.delivered", _delivery(after, rows, event_id))
    return after


def _delivery(f: machine.Fold, rows: list[dict], event_id: int) -> dict[str, Any]:
    c = f.candidate
    done = next(
        (
            r["payload"].get("done")
            for r in rows
            if r["type"] == "turn.collected" and r["payload"].get("turn_id") == c.turn_id
        ),
        None,
    )
    test = f.checks.get(Check.TEST)
    return {
        "candidate": {"sha": c.sha, "turn_id": c.turn_id},
        "outcome": f.join.outcome,
        "join_row": f.join.row,
        "summary": done,
        "verdicts": {ch.value: {"verdict": v.verdict, "event_id": v.event_id} for ch, v in f.checks.items()},
        "findings": [{"source": x.source, "kind": x.kind, "text": x.text} for x in f.join.findings],
        "gaps": (test.payload.get("behaviors") if test else None) or [],
        "scope": (f.plan or {}).get("scope") or [],
        "awaiting_grant": [{"id": i.id, "path": i.path, "summary": i.summary} for i in f.ungranted()],
    }


def merge_action(f: machine.Fold, b: tasks.Brief) -> broker.Action | None:
    docs = f.checks.get(Check.DOCS)
    if f.candidate is None or docs is None or not b.origin_url or not b.target_branch:
        return None
    return broker.Action(
        "merge",
        b.target_branch,
        {
            "url": b.origin_url,
            "target_branch": b.target_branch,
            "head_sha": docs.payload.get("head") or f.candidate.sha,
            "candidate": {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id},
        },
    )


async def ensure_merge(conn, task_id: str) -> broker.Outcome | None:
    """Request the merge for a task in `merge` whose current candidate's
    delivery passed (or passed with gaps) and has no merge effect yet.
    Idempotent: the broker returns a held effect for the same payload. It
    requests nothing while a governance instance awaits Tom's tap, nor again
    after a refusal of the same payload unless Tom has granted something
    since, so runs never pile refusals up."""
    f = machine.fold(await ledger.read(conn, task_id))
    if f.legacy or f.state is not State.MERGE or f.candidate is None:
        return None
    named = {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}
    if (f.delivery or {}).get("candidate") != named or f.delivery.get("outcome") not in ("passed", "gaps"):
        return None
    if f.ungranted():
        return None
    action = merge_action(f, await tasks.brief(conn, task_id))
    if action is None:
        return None
    effect = f.merge_effect
    if effect and effect["state"] in ("held", "in_flight", "done"):
        return None
    if (
        effect
        and effect["state"] == "refused"
        and effect["payload_sha256"] == ledger.digest(action.payload)
        and effect["grants"] == len(f.granted)
    ):
        return None
    return await broker.request(conn, task_id, action)
