"""Writing verdict rows, and what the join does when it sends a task to merge.

Each writer takes the task's advisory lock, folds, refuses a row the fold
would ignore (a task in another state, a legacy task, a stopped one), and
appends in one transaction. A check verdict that completes a join to
`merge` writes `task.delivered` in the same transaction; `ensure_merge`
then requests the merge effect, and the router calls it again on every run
that finds the task in `merge`, so a crash between the two strands nothing.

The judge's verdict is the kernel's reading of a judgement row
(`record_judge`); nobody records it by hand. Until the critique and check
runners (1.4) exist, a person records those verdicts by hand (`python -m
core verdict`), each row `leg: manual` with provenance. `manual_allowed`
refuses a stage that has a runner, so the stand-in closes as runners
arrive. A test verdict given a breadth judgement, and a review or docs
verdict given governance judgements, read those rows themselves: the
behaviors and instances come from the kernel's tables, never from the
caller's text.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from core import broker, git, judgement, judgement_sites, judgement_tasks, ledger, machine, tasks
from core.machine import Check, State

MANUAL_STAGES: dict[str, State | Check] = {"review": Check.REVIEW}


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
    if f.calibration:
        raise VerdictRefused(f"task {task_id} is a calibration task")
    if f.state is State.STOPPED:
        raise VerdictRefused(f"task {task_id} is stopped")
    if f.state not in stages:
        raise VerdictRefused(f"task {task_id} is in {f.state}, not {' or '.join(stages)}")
    return rows, f


def _manual(leg: str, by: str, via: str, role_played: bool) -> dict[str, Any]:
    return {"provenance": ledger.provenance(by, via, role_played)} if leg == "manual" else {}


def _session_leg(
    leg: str, turn_id: str | None, model: str | None, suites: list[int] | None = None
) -> dict[str, Any]:
    """A verdict a fresh session's turn produced names that turn and its
    model; one with neither is refused. A `kernel` verdict (the test
    runner's, which runs no model turn) names the `suite.ran` rows it read
    in place of a turn."""
    if leg == "manual":
        return {}
    if leg == "kernel":
        if not suites:
            raise VerdictRefused("a kernel verdict names the suite.ran rows it read")
        return {"suites": list(suites)}
    if not turn_id or not model:
        raise VerdictRefused(f"a {leg} verdict names the turn that produced it and its model")
    return {"turn_id": turn_id}


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


async def record_judge(conn, task_id: str, judgement_id: str) -> int:
    """The judge's verdict, read by the kernel from one `intake.underspecified`
    judgement row on this task (`judgement_sites.judge_verdict`): proceed is
    `precise`; caution, an abstain, or a failure of both legs is `thin`."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        rows, _ = await _fold(conn, task_id, State.JUDGE)
        row = judgement.find(rows, judgement_id)
        if row is None or row["payload"].get("site") != machine.GUARD_JUDGE:
            raise VerdictRefused(f"{judgement_id} is not a request judgement on task {task_id}")
        p = row["payload"]
        verdict = judgement_sites.judge_verdict(row)
        answer = (p.get("answers") or {}).get("request") or {}
        return await ledger.append(
            conn,
            task_id,
            "judge.decided",
            {
                "verdict": verdict,
                "leg": "judgement",
                "judgement_id": judgement_id,
                "answered": row["type"] == "judgement.answered",
                "p_precise": answer.get("p_proceed"),
                "label": answer.get("label"),
                "abstained": bool(p.get("abstained")),
                "model": p.get("model"),
                "usd_micros": p.get("usd_micros"),
                "guard_id": machine.GUARD_JUDGE if verdict == "thin" else None,
            },
        )


async def record_critique(
    conn, task_id: str, verdict: str, *, findings: Iterable = (), raised: Mapping[str, int] | None = None,
    leg: str = "manual", model: str | None = None, usd_micros: int = 0, by: str = "tom",
    via: str = "the command line", role_played: bool = False, turn_id: str | None = None,
    plan_sha256: str | None = None,
) -> int:  # fmt: skip
    """`plan_sha256`, when given, is the plan the session read; a verdict on
    a plan that is no longer the current one is refused."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        rows, f = await _fold(conn, task_id, State.CRITIQUE)
        if plan_sha256 is not None and plan_sha256 != f.plan["sha256"]:
            raise VerdictRefused("the plan changed since this critique read it")
        payload = {
            "plan_sha256": f.plan["sha256"],
            "verdict": verdict,
            "findings": _findings(findings),
            "raised": dict(raised or {}),
            "leg": leg,
            "model": model,
            "usd_micros": usd_micros,
            **_session_leg(leg, turn_id, model),
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
    conn, task_id: str, check: Check, verdict: str | None, *, findings: Iterable = (),
    governance: Iterable[InstanceSpec] = (), head: str | None = None, command: str | None = None,
    failures: Iterable[str] = (), behaviors: Iterable[str] = (), leg: str = "manual",
    model: str | None = None, usd_micros: int = 0, by: str = "tom", via: str = "the command line",
    role_played: bool = False, breadth: str | None = None, governance_from: Iterable[str] | None = None,
    notes: Mapping[str, Mapping[str, Any]] | None = None, turn_id: str | None = None,
    deleted_at_head: Iterable[str] = (), failing_at_base: Iterable[str] = (),
    suites: Iterable[int] | None = None, dropped: Iterable[Mapping[str, Any]] = (),
) -> machine.Fold:  # fmt: skip
    """Record one branch's verdict on the current candidate. Returns the
    fold after it; when it completes a join to `merge`, `task.delivered` is
    written with it.

    `breadth` (test only) names the candidate's breadth judgement: the
    listed behaviors come from it and the verdict is computed (any failure
    `red`, else any behavior `gaps`, else `pass`); a `verdict` that
    disagrees is refused; a `kernel` verdict (the test runner's) may pass
    `verdict` None and have it computed. While breadth has no calibration
    record (`judgement_tasks.BREADTH.calibrated` is None) its behaviors are
    information only: listed under `breadth.information` and in the
    delivery, never `gaps`. A test, review, or docs verdict not recorded by
    hand must name its judgements (`breadth`, `governance_from`).
    A docs head not recorded by hand must already sit in the mirror under
    a `refs/valor/docs/` ref (the docs runner fetched it and cut it to the
    commits it keeps; `dropped` lists the rest); nothing is fetched here.
    `governance_from` (review and docs) names one
    governance judgement per hunk of the check's diff: the instances are the
    hunks the kernel's table makes instances, together with any `governance`
    the reviewer named (a reviewer adds caution, never removes it), and
    `notes` attach a summary, incident, and mission item to an instance by
    its id. A judgement both legs failed, with reruns left, refuses the
    verdict as unanswered, so the branch has none and the next run asks
    again."""
    failures, behaviors = list(failures), list(behaviors)  # once: a generator is read one time
    suites = list(suites) if suites is not None else None
    if leg != "manual" and check is Check.TEST and breadth is None:
        raise VerdictRefused("a test verdict not recorded by hand names its breadth judgement")
    if leg != "manual" and check in (Check.REVIEW, Check.DOCS) and governance_from is None:
        raise VerdictRefused(f"a {check.value} verdict not recorded by hand names its governance judgements")
    if leg == "kernel" and check is not Check.TEST:
        raise VerdictRefused("only the test branch has a kernel leg")
    if verdict is None and leg != "kernel":
        raise VerdictRefused("a verdict recorded by hand or by a session names its verdict")
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
            **_session_leg(leg, turn_id, model, suites),
            **_manual(leg, by, via, role_played),
        }
        specs = list(governance)
        # A task the kernel provisioned keeps its commits in the kernel
        # mirror, which no turn writes; git facts are read there.
        repo = b.mirror or b.workspace
        if breadth is not None and check is not Check.TEST:
            raise VerdictRefused("only the test branch takes a breadth judgement")
        if governance_from is not None and check is Check.TEST:
            raise VerdictRefused("only review and docs answer the governance boolean")
        if check is Check.TEST and breadth is not None:
            try:
                found = judgement_sites.breadth_outcome(rows, breadth, c)
            except (judgement_sites.Unusable, judgement_sites.Unanswered) as exc:
                raise VerdictRefused(str(exc)) from None
            if behaviors:
                raise VerdictRefused(
                    "with a breadth judgement the behaviors come from it, not from the caller"
                )
            calibrated = judgement_tasks.BREADTH.calibrated is not None
            behaviors = found["behaviors"] if calibrated else []
            computed = "red" if failures else ("gaps" if behaviors else "pass")
            if verdict is None:
                verdict = payload["verdict"] = computed
            if verdict != computed:
                raise VerdictRefused(
                    f"with these failures and this breadth judgement the verdict is {computed}"
                )
            payload["breadth"] = {k: found[k] for k in ("judgement_id", "actions", "abstained", "model",
                                                         "usd_micros", "guard_id")}  # fmt: skip
            if not calibrated:
                # Breadth has no calibration record yet: what it lists is shown, never acted on.
                payload["breadth"]["information"] = found["behaviors"]
                payload["breadth"]["guard_id"] = None
        if check is Check.TEST:
            payload["command"] = command
            payload["failures"] = failures
            payload["deleted_at_head"] = list(deleted_at_head)
            payload["failing_at_base"] = list(failing_at_base)
            payload["behaviors"] = behaviors
            payload["findings"] += [{"kind": "test failure", "text": t} for t in failures]
            payload["findings"] += [{"kind": "untested behavior", "text": t} for t in behaviors]
            payload["guard_id"] = machine.GUARD_BREADTH if behaviors else None
        try:
            if check is Check.DOCS:
                head = head or c.sha
                if head != c.sha:
                    # The docs runner fetched and cut the head; it must sit under a docs ref.
                    if b.mirror and (
                        not re_sha(head)
                        or not git.trusted(repo, "for-each-ref", "--points-at", head, "refs/valor/docs/")
                    ):
                        raise VerdictRefused(f"{head} is not a docs head the kernel kept in the mirror")
                    if not git.is_ancestor(repo, c.sha, head):
                        raise VerdictRefused(f"{head} does not descend from the candidate {c.sha}")
                    if git.merges_between(repo, c.sha, head):
                        raise VerdictRefused("the docs commits hold a merge commit")
                payload["head"] = head
                payload["dropped"] = [dict(d) for d in dropped]
                payload["paths"] = git.diff_paths(repo, c.sha, head) if head != c.sha else []
            judged: dict[str, Any] | None = None
            if check in (Check.REVIEW, Check.DOCS):
                older, newer = (b.base_sha, c.sha) if check is Check.REVIEW else (c.sha, payload["head"])
                if governance_from is not None:
                    ids = list(governance_from)
                    try:
                        judged = judgement_sites.governance_outcome(
                            rows, ids, judgement_sites.diff_hunks(repo, older, newer)
                        )
                    except (judgement_sites.Unusable, judgement_sites.Unanswered) as exc:
                        raise VerdictRefused(str(exc)) from None
                    specs = [InstanceSpec(h.path, h.start) for h in judged["instances"]] + specs
                instances = _union(_instances(repo, older, newer, specs) if specs else [])
                if judged is not None and judged["unjudged"]:
                    instances.append(judgement_sites.unjudged_instance(judged["unjudged"]))
                instances = _annotate(instances, notes or {})
        except git.GitError as exc:
            raise VerdictRefused(str(exc)) from None
        if notes and check is Check.TEST:
            raise VerdictRefused("only review and docs carry governance notes")
        if check in (Check.REVIEW, Check.DOCS):
            payload["governance"] = {"adds": bool(instances), "instances": instances}
            if judged is not None:
                payload["governance"].update(
                    {
                        "judgements": ids,
                        "abstain_instances": judged["abstain_instances"],
                        "unjudged_hunks": [h.id for h, _ in judged["unjudged"]],
                    }
                )
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


def re_sha(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)


def _union(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Instances once per id. The kernel's come first; a reviewer naming a
    line inside a hunk the kernel already made an instance adds nothing
    twice, and the summary, incident, and mission item it gave fill the
    kernel's entry where that has none."""
    seen: dict[str, dict[str, Any]] = {}
    for i in instances:
        if i["id"] not in seen:
            seen[i["id"]] = dict(i)
            continue
        kept = seen[i["id"]]
        for k in ("summary", "incident", "mission_item"):
            if not kept.get(k) and i.get(k):
                kept[k] = i[k]
    return list(seen.values())


def _annotate(
    instances: list[dict[str, Any]], notes: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """A reviewer's summary, incident, and mission item on an instance, by
    its id. A note for an id that is not an instance is refused."""
    ids = {i["id"] for i in instances}
    unknown = sorted(set(notes) - ids)
    if unknown:
        raise VerdictRefused(f"notes for ids that are not governance instances here: {unknown}")
    out = []
    for i in instances:
        note = notes.get(i["id"]) or {}
        out.append({**i, **{k: note[k] for k in ("summary", "incident", "mission_item") if note.get(k)}})
    return out


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
        "information": ((test.payload.get("breadth") or {}).get("information") if test else None) or [],
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
    if f.legacy or f.calibration or f.state is not State.MERGE or f.candidate is None:
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
