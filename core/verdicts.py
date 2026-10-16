"""Writing verdict rows, and what the join does when it sends a task to merge.

Each writer takes the task's advisory lock, folds, refuses a row the fold
would ignore (a task in another state, a legacy task, a stopped one), and
appends in one transaction. A check verdict that completes a join to
`merge` writes `task.delivered` in the same transaction; `ensure_merge`
then requests the merge effect, and the router calls it again on every run
that finds the task in `merge`, so a crash between the two strands nothing.

The judge's verdict is the kernel's reading of a judgement row
(`record_judge`). Every other verdict is its runner's: a fresh session's
(`leg: session`) or the kernel's own (`leg: kernel`). A test verdict given
a breadth judgement reads that row itself: the behaviors come from the
kernel's table, never from the caller's text.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core import (
    broker,
    credentials,
    git,
    judgement,
    judgement_sites,
    judgement_tasks,
    ledger,
    machine,
    targets,
    tasks,
)
from core.machine import Check, State

# What a reviewer's session answers.
REVIEWER_VERDICTS = ("pass", "changes")


class VerdictRefused(LookupError):
    pass


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


def _session_leg(
    leg: str, turn_id: str | None, model: str | None, suites: list[int] | None = None
) -> dict[str, Any]:
    """A verdict a fresh session's turn produced names that turn and its
    model; one with neither is refused. A `kernel` verdict (the test
    runner's, which runs no model turn) names the `suite.ran` rows it read
    in place of a turn."""
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
    leg: str, model: str | None = None, usd_micros: int = 0, turn_id: str | None = None,
    plan_sha256: str | None = None,
) -> int:  # fmt: skip
    """`plan_sha256`, when given, is the plan the session read; a verdict on
    a plan that is no longer the current one is refused. A `kernel` verdict
    is the critique runner's own, when the plan commit's tree cannot be
    checked out; it names no turn."""
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
            **({} if leg == "kernel" else _session_leg(leg, turn_id, model)),
        }
        sent_back = verdict == "revise" and _would(rows, "critique.decided", payload).state is State.PLAN
        payload["guard_id"] = machine.GUARD_CRITIQUE if sent_back else None
        return await _append(conn, task_id, "critique.decided", payload)


async def record_check(
    conn, task_id: str, check: Check, verdict: str | None, *, findings: Iterable = (),
    head: str | None = None, command: str | None = None,
    failures: Iterable[str] = (), behaviors: Iterable[str] = (), leg: str,
    model: str | None = None, usd_micros: int = 0,
    breadth: str | None = None, turn_id: str | None = None,
    deleted_at_head: Iterable[str] = (), failing_at_base: Iterable[str] = (),
    suites: Iterable[int] | None = None, dropped: Iterable[Mapping[str, Any]] = (),
    predicted_failure: float | None = None, requirements: Iterable[Any] = (),
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
    delivery, never `gaps`. A test verdict must name its breadth
    judgement.
    A docs `head` is the one the docs runner kept (`fresh.kept_prefix`, the
    one place docs commits are read); `dropped` lists the rest.

    A review gives the reviewer's own `verdict`, `pass` or `changes`, with
    `predicted_failure` and `requirements`. A `kernel` review is `changes`
    only, since no reviewer ran."""
    failures, behaviors = list(failures), list(behaviors)  # once: a generator is read one time
    suites = list(suites) if suites is not None else None
    if check is Check.TEST and breadth is None:
        raise VerdictRefused("a test verdict names its breadth judgement")
    if leg == "kernel" and check is Check.REVIEW and verdict != "changes":
        raise VerdictRefused("a kernel-leg review is changes: no reviewer ran to pass it")
    if verdict is None and leg != "kernel":
        raise VerdictRefused("a session's verdict names its verdict")
    if check is Check.REVIEW and verdict not in REVIEWER_VERDICTS:
        raise VerdictRefused(f"a reviewer's verdict is one of {', '.join(REVIEWER_VERDICTS)}")
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
            **(
                {}
                if leg == "kernel" and check is not Check.TEST
                else _session_leg(leg, turn_id, model, suites)
            ),
        }
        # A task the kernel provisioned keeps its commits in the kernel
        # mirror, which no turn writes; git facts are read there.
        repo = b.mirror or b.workspace
        if breadth is not None and check is not Check.TEST:
            raise VerdictRefused("only the test branch takes a breadth judgement")
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
                payload["head"] = head
                payload["dropped"] = [dict(d) for d in dropped]
                payload["paths"] = git.diff_paths(repo, c.sha, head) if head != c.sha else []
        except git.GitError as exc:
            raise VerdictRefused(str(exc)) from None
        if check is Check.REVIEW and leg != "kernel":
            payload["predicted_failure"] = predicted_failure
            payload["requirements"] = list(requirements)
        kind = f"{check.value}.decided"
        after = _would(rows, kind, payload)
        if after.join is not None and f.join is None and after.join.row in (3, 5):
            payload["guard_id"] = machine.GUARD_REVIEW
        event_id = await _append(conn, task_id, kind, payload)
        after = machine.fold(await ledger.read(conn, task_id))
        if f.state is State.CHECKS and after.state in (State.MERGE, State.ENDED) and after.join is not None:
            await _append(conn, task_id, "task.delivered", _delivery(after, rows, event_id))
    return after


async def _append(conn, task_id: str, kind: str, payload: dict[str, Any]) -> int:
    """A verdict's row, judged where it is written: what Postgres jsonb
    refuses (findings a session wrote past what one value holds, say) is
    `VerdictRefused` with Postgres's reason, and the caller's transaction
    rolls back whole."""
    event_id, why = await ledger.try_append(conn, task_id, kind, payload)
    if event_id is None:
        raise VerdictRefused(f"the {kind} row: {ledger.UNSTORABLE}: {why}")
    return event_id


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


# A merge that failed is requested again, after a delay that doubles from
# `MERGE_BACKOFF_S` up to `MERGE_BACKOFF_MAX_S`, until `MERGE_TRIES`
# failures; then the task ends with a report. Two failures take their own
# path: a push rejected because the target moved carries the verified
# candidate onto the new tip when the target's new commits change none of
# its paths (`_carry`), else sends the work back to be rebased (spending
# no round; some merge always lands first, so this ends), and one that
# needs the push credential asks Tom for it, the one thing only he holds,
# and is tried again every `CREDENTIAL_RETRY_S` until it lands.
MERGE_TRIES = 5
MERGE_BACKOFF_S = 60.0
MERGE_BACKOFF_MAX_S = 3600.0
CREDENTIAL_RETRY_S = 600.0
# Git's words for a push the target refused because its branch moved on.
MOVED = ("[rejected]", "non-fast-forward", "fetch first")
# The credential's name, as a missing key says it (`credentials.MissingKey`),
# and the merge performer's words for a refused one (`tools/push_branch.py`,
# `ROTATE`).
CREDENTIAL = (credentials.GITHUB_KEY, "refused the credential")
# A project's schema file, by name at any depth: what its stored data is
# built from, which no carry may take a change to untested.
SCHEMA_FILES = ("schema.sql",)
# Where a carried head is kept in the repository the merge pushes from.
CARRIED_REF = "refs/valor/carried/"


def carry_blocked(path: str) -> bool:
    """A path the target's new commits may not change for a carry: a file
    setup installs from (`checks.LOCKFILES`, at the top level) or the
    project's schema (`SCHEMA_FILES`)."""
    from core import checks

    return path in checks.LOCKFILES or path.rsplit("/", 1)[-1] in SCHEMA_FILES


@dataclass(frozen=True)
class MergeTry:
    """One merge effect requested for a candidate: how it stands, why it
    did not land, and when it settled."""

    effect_id: str
    state: str  # in_flight, done, failed, refused
    reason: str
    at: datetime | None


def merge_tries(rows: list[dict[str, Any]], candidate: machine.Candidate) -> list[MergeTry]:
    """Every merge effect requested for `candidate`, in request order, each
    as its latest row leaves it."""
    named = {"sha": candidate.sha, "turn_id": candidate.turn_id}
    found: dict[str, MergeTry] = {}
    for r in rows:
        kind, p = r["type"], r["payload"]
        effect_id = p.get("effect_id")
        if effect_id not in found:
            if kind not in ("effect.held", "effect.intent", "effect.refused"):
                continue
            if p.get("action_type") != "merge" or (p.get("payload") or {}).get("candidate") != named:
                continue
        if kind == "effect.refused":
            found[effect_id] = MergeTry(effect_id, "refused", str(p.get("reason") or ""), r.get("at"))
        elif kind == "effect.outcome":
            found[effect_id] = MergeTry(effect_id, str(p.get("kind")), str(p.get("error") or ""), r.get("at"))
        elif kind in ("effect.held", "effect.intent"):
            found[effect_id] = MergeTry(effect_id, "in_flight", "", None)
    return list(found.values())


def merge_failure(t: MergeTry) -> str:
    """Why a merge did not land, as the kernel acts on it: `credential`
    (the push credential is missing or refused), `moved` (the target's
    branch moved on), `refused` (the broker or the predicate refused it; a
    try again gets the same answer), or `transient`."""
    if any(m in t.reason for m in CREDENTIAL):
        return "credential"
    if t.state == "failed" and any(m in t.reason for m in MOVED):
        return "moved"
    if t.state == "refused":
        return "refused"
    return "transient"


def credential_missing(performers: broker.Performers) -> str | None:
    """Why the task's merge cannot carry its credential now (the key file
    or the token in it is missing), or None. Only a merge to a remote with
    a kernel mirror carries one."""
    performer = performers.get("merge")
    keyfile, url = getattr(performer, "credential", None), getattr(performer, "url", None)
    if keyfile is None or not url or targets.local(url):
        return None
    try:
        credentials.read_key(keyfile, credentials.GITHUB_KEY, "github-key")
    except credentials.MissingKey as exc:
        return str(exc)
    return None


def _backoff(failures: int) -> float:
    return min(MERGE_BACKOFF_MAX_S, MERGE_BACKOFF_S * 2 ** max(failures - 1, 0))


async def ensure_merge(
    conn, performers: broker.Performers, task_id: str, *, now: datetime | None = None
) -> broker.Outcome | None:
    """Request the merge for a task in `merge` whose current candidate's
    delivery passed (or passed with gaps) and whose merge is not in flight
    or done. The request carries `merge:<payload digest>:<tries>` as
    its `request_id`, so the broker answers a second request for the same
    try with the first effect's standing under the task's lock, and two
    racing steps make one intent.

    A merge that did not land is acted on by why (`merge_failure`): a
    moved target first carries the verified candidate onto the new tip
    (`_carry`, `verdicts.carried`) and requests its merge at once, else
    writes `rebase.requested`; a
    credential asks Tom for it once per candidate (`notices.credential`)
    and is tried again every `CREDENTIAL_RETRY_S`; a transient failure is
    tried again after `_backoff`; a refusal or a transient failure past
    `MERGE_TRIES` ends the task
    (`task.ended`, kind `merge`). A merge whose credential is missing is
    not requested; Tom is asked for it. Before a try is due the answer is
    `unknown` with the time it is due, so the step parks."""
    from core import notices

    now = now or datetime.now(UTC)
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.legacy or f.calibration or f.state is not State.MERGE or f.candidate is None:
        return None
    named = {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}
    if (f.delivery or {}).get("candidate") != named or f.delivery.get("outcome") not in ("passed", "gaps"):
        return None
    b = await tasks.brief(conn, task_id)
    action = merge_action(f, b)
    if action is None:
        return None
    effect = f.merge_effect
    if effect and effect["state"] in ("in_flight", "done"):
        return None
    tries = [t for t in merge_tries(rows, f.candidate) if t.state in ("failed", "refused")]
    last = tries[-1] if tries and effect and effect["state"] in ("refused", "failed") else None
    if last is not None and last.reason == broker.NO_PERFORMER:
        return None  # this kernel has no merge for the task: nothing to try again or end
    if last is not None:
        why = merge_failure(last)
        if why == "moved":
            not_carried = await _carry(conn, task_id, b, f, last)
            if not_carried is None:
                return await ensure_merge(conn, performers, task_id, now=now)
            await _rebase(conn, task_id, b, f.candidate, last, not_carried)
            return None
        transient = [t for t in tries if merge_failure(t) == "transient"]
        if why == "refused" or (why == "transient" and len(transient) >= MERGE_TRIES):
            await end(conn, task_id, "merge", _first_line(last.reason), effect_id=last.effect_id, why=why)
            return None
        if why == "credential":
            await notices.credential(
                conn, task_id, f.candidate.sha, missing=credentials.GITHUB_KEY in last.reason
            )
        delay = CREDENTIAL_RETRY_S if why == "credential" else _backoff(len(transient))
        due = (last.at or now) + timedelta(seconds=delay)
        if now < due:
            return broker.Outcome(last.effect_id, "unknown", error=f"tried again at {due.isoformat()}")
    missing = credential_missing(performers)
    if missing is not None:
        await notices.credential(conn, task_id, f.candidate.sha, missing=True)
        return broker.Outcome("", "unknown", error=missing)
    request_id = f"merge:{ledger.digest(action.payload)}:{len(tries)}"
    return await broker.request(conn, performers, task_id, action, request_id=request_id)


def _first_line(text: str, limit: int = 300) -> str:
    """A reason as a report carries it: its first line with words, cut."""
    line = next((s.strip() for s in (text or "").splitlines() if s.strip()), "")
    return line if len(line) <= limit else line[: limit - 3] + "..."


async def _tip(repo: str | None) -> str | None:
    """The target's new tip, which the merge's lookup fetched into the
    repository the merge pushes from (`git.LOOKUP_REF`), or None."""
    if not repo:
        return None
    try:
        return await git.threaded(git.trusted, repo, "rev-parse", "--verify", "-q", git.LOOKUP_REF) or None
    except git.GitError:
        return None


async def _carry(conn, task_id: str, b: tasks.Brief, f: machine.Fold, failed: MergeTry) -> str | None:
    """Carry the verified candidate onto the moved target's new tip, with no
    turn: the kernel replays the candidate and its docs head onto the tip
    (`git.replay`), which applies only when the target's new commits change
    no path the candidate changes and no `carry_blocked` file, and leaves
    the patch identical, hunk for hunk. Then `verdicts.carried` moves the
    candidate's test, review, and docs verdicts and its delivery to the
    replayed sha; the fold reads them there, so the merge predicate holds
    for it. It spends no rebase. Returns None when carried, else why not."""
    repo = b.mirror or b.workspace
    docs, c = f.checks.get(Check.DOCS), f.candidate
    tip = await _tip(repo)
    if not repo or docs is None or c is None or tip is None:
        return "no fetched tip of the target to carry the candidate onto"
    head = docs.payload.get("head") or c.sha
    try:
        done, why = await git.threaded(
            git.replay, repo, c.sha, head, tip, carry_blocked, kernel=bool(b.mirror)
        )
        if done is not None:
            await git.threaded(git.trusted if b.mirror else git.out, repo, "update-ref",
                               CARRIED_REF + c.turn_id, done.head)  # fmt: skip
    except git.GitError as exc:
        return f"the replay failed: {_first_line(str(exc))}"
    if done is None:
        return why
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        now = machine.fold(await ledger.read(conn, task_id))
        if now.carried and now.carried[-1]["from"]["sha"] == c.sha:
            return None  # a racing step carried it first
        if now.state is not State.MERGE or now.candidate != c or now.merge_effect != f.merge_effect:
            return "the task moved on while the candidate was replayed"
        await ledger.append(
            conn,
            task_id,
            "verdicts.carried",
            {
                "effect_id": failed.effect_id,
                "target_branch": b.target_branch,
                "tip": tip,
                "base": done.base,
                "from": {"sha": c.sha, "turn_id": c.turn_id, "head": head},
                "to": {"sha": done.sha, "turn_id": c.turn_id, "head": done.head},
                "verdicts": {ch.value: v.event_id for ch, v in f.checks.items()},
                "reason": _first_line(failed.reason),
            },
        )
    return None


async def _rebase(
    conn, task_id: str, b: tasks.Brief, c: machine.Candidate, failed: MergeTry, not_carried: str
) -> None:
    """Send the work on candidate `c` back to rebase onto the target's new
    tip, which the merge's lookup fetched into the kernel mirror
    (`git.LOOKUP_REF`), naming why the kernel could not carry `c` itself;
    nothing once the task left `merge` or a racing step carried `c`."""
    tip = await _tip(b.mirror or b.workspace)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        now = machine.fold(await ledger.read(conn, task_id))
        if now.state is not State.MERGE or now.candidate != c:
            return
        await ledger.append(
            conn,
            task_id,
            "rebase.requested",
            {
                "effect_id": failed.effect_id,
                "target_branch": b.target_branch,
                "tip": tip,
                "reason": _first_line(failed.reason),
                "not_carried": not_carried,
            },
        )


async def end(conn, task_id: str, kind: str, reason: str, **more: Any) -> bool:
    """Write `task.ended` from the kernel: Valor stops the work and Tom gets
    a report (`notices.owe`). `kind` names what ended it: `step`,
    `provision`, or `merge`. False when the task is already merged, ended,
    or stopped."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy or f.calibration or f.state in (State.MERGED, State.ENDED, State.STOPPED):
            return False
        await ledger.append(
            conn, task_id, "task.ended", {"kind": kind, "by": "kernel", "reason": reason, **more}
        )
    return True
