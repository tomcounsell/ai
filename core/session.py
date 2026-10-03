"""The working session: the task's one harness session, in which clarify,
plan, build, and patch run, each turn resuming the one before.

Serves Mission item 1 (one working context from inspection to delivery) and
Mission item 6 (Tom's answer lands in the context that asked). `run` runs
one state's turns until the task leaves that state, or until there is
something for Tom: two idle turns, a failed turn, a stop.
The router (`core/router.py`) decides what runs next.

A turn's prompt is data from the row that moved the task into its state
(`Fold.entry`): the instruction, Tom's answer, a critique's findings, every
finding of the join, or Tom's feedback, under a short label; `Continue.`
once a turn in the state has finished. What the turn should do with it is
the stage file its Brief carries (`skills/sdlc/<state>.md`). An answer,
findings, or feedback is spent only by a turn that finishes: after a turn
that fails or is stopped, the next one opens with it again. Each prompt is
followed by what became of the effects the previous turn requested.

What a turn left under `.valor/` (see `core.signals`) is one
`turn.collected` row carrying the state it ran in and its verdict, with a
`question.asked` or `plan.written` beside it when the verdict calls for
one. A signal that means nothing in the state, a plan not committed, or a
candidate on a tree with uncommitted changes goes to `errors`, and the next
prompt says so. The merge is the kernel's to request: a turn's request for
one never reaches the broker. An `email.send` naming `reply_to` is made
the reply to all of that received email before it is requested, so Tom's
approval covers its final recipients.

Every question is a `question.asked` row and its answer a
`question.answered` row with Tom's provenance, and his feedback a
`feedback.given` row, so the attention a task cost is a fold over its
ledger (`tasks.status`).
"""

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core import broker, db, git, ledger, machine, mail, runs, signals, tasks, workspace
from core.gateway import Gateway
from core.machine import State
from core.settings import settings

# Builds one turn's command: (prompt, session to resume or None, Brief).
TurnFor = Callable[[str, str | None, tasks.Brief], Callable[[str, str], runs.TurnCommand]]
Alive = Callable[[], Awaitable[bool]]


async def _always() -> bool:
    return True


async def run(
    gateway: Gateway, task_id: str, turn_for: TurnFor, dsn: str | None = None, alive: Alive = _always
) -> dict[str, Any]:
    """Run turns in the task's current working state until it leaves it.
    Returns `status`: `moved` (the fold left the state), `failed`, `idle`,
    `stopped`, or `lock lost` (the router's run lock died), with the task's
    `state` from `tasks.status`."""
    dsn = dsn or gateway.dsn
    idle = 0
    async with await db.connect(dsn) as conn:
        state = machine.fold(await ledger.read(conn, task_id)).state
    if state not in machine.WORKING:
        return {"status": "moved"}
    while True:
        if not await alive():
            return {"status": "lock lost"}
        async with await db.connect(dsn) as conn:
            now = await tasks.status(conn, task_id)
            if now["state"] != state.value:
                return {"status": "moved", "state": now}
            b = await tasks.brief(conn, task_id)
            prompt, resume = await next_prompt(conn, task_id)
        try:
            ended = await runs.run_turn(gateway, task_id, turn_for(prompt, resume, b), dsn=dsn, state=state)
        except tasks.TaskStopped:
            return {"status": "stopped"}
        found = signals.collect(b.workspace, ended["turn_id"]) if b.workspace else signals.Signals()
        if not await alive():
            return {"status": "lock lost", "turn": ended}
        ok = ended["outcome"] == "done" and not ended["result"].get("is_error")
        async with await db.connect(dsn) as conn:
            await record(
                conn,
                task_id,
                ended["turn_id"],
                found,
                state=state,
                workspace=b.workspace,
                finished=ok,
                brief=b,
            )
            now = await tasks.status(conn, task_id)
        if now["state"] == State.STOPPED.value:
            return {"status": "stopped", "state": now, "turn": ended}
        if now["state"] != state.value:
            return {"status": "moved", "state": now, "turn": ended}
        if not ok:
            return {"status": "failed", "state": now, "turn": ended}
        idle += 1
        if idle >= settings.idle_turns:
            return {"status": "idle", "state": now, "turn": ended}


def _plan(workspace: str | None, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A `plan.written` payload from a turn's `plan.json`, or why not."""
    try:
        path = str(raw["path"])
        counts = {k: raw[k] for k in ("critique_rounds", "review_rounds")}
    except KeyError as exc:
        return None, f"plan.json lacks {exc.args[0]!r}"
    for k, v in counts.items():
        if not isinstance(v, int) or isinstance(v, bool) or v not in machine.ROUNDS:
            return None, f"plan.json {k} is {v!r}; each count is 0, 1, or 2"
    try:
        if not git.is_repo(workspace):
            return None, "the workspace is not a git repository, so the plan cannot be committed"
        head = git.head(workspace)
        body = git.show(workspace, "HEAD", path) if head else None
    except git.GitError as exc:
        return None, str(exc)
    if body is None:
        return None, f"{path} is not committed at HEAD"
    local = Path(workspace) / path
    if not local.is_file() or local.read_bytes() != body:
        return None, f"{path} has changes not committed"
    return {
        "path": path,
        "commit": head,
        "sha256": hashlib.sha256(body).hexdigest(),
        "stakes": str(raw.get("stakes") or ""),
        **counts,
        "scope": list(raw.get("scope") or []),
    }, None


def _candidate(workspace: str | None, turn_id: str) -> tuple[dict[str, str] | None, str | None]:
    try:
        if not git.is_repo(workspace):
            return None, "the workspace is not a git repository, so there is no commit to check"
        left = git.dirty(workspace)
        head = git.head(workspace)
    except git.GitError as exc:
        return None, str(exc)
    if left:
        return None, "done.md with uncommitted changes; commit everything first:\n" + "\n".join(left[:20])
    if head is None:
        return None, "the workspace has no commit"
    return {"sha": head, "turn_id": turn_id}, None


def _keep(brief: tasks.Brief | None, sha: str, ref: str, turn_id: str) -> str | None:
    """Fetch a plan commit or a candidate into the kernel mirror of a task
    the kernel provisioned; why not, or None. A task without a mirror keeps
    nothing."""
    if brief is None or not brief.mirror:
        return None
    try:
        if workspace.tree_has_valor(brief.workspace, sha, trusted=False):
            return (
                f"{sha[:12]} commits a .valor entry; .valor is the kernel's signal channel and never "
                "part of a plan or candidate (git rm -r --cached .valor, then commit)"
            )
        workspace.fetch_into_mirror(
            brief.mirror, brief.workspace, sha, ref, brief.harness["sandbox_profile"], f"mirror-{turn_id}"
        )
    except (workspace.FetchRefused, git.GitError) as exc:
        return str(exc)
    return None


def _verdict(
    state: State,
    found: signals.Signals,
    workspace: str | None,
    turn_id: str,
    finished: bool,
    brief: tasks.Brief | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    """The turn's verdict in its state, what goes with it, and the signals
    that did not count."""
    errors: list[str] = []
    extra: dict[str, Any] = {}
    meant = {
        State.CLARIFY: ("question", "no_question"),
        State.PLAN: ("question", "plan"),
        State.BUILD: ("question", "done"),
        State.PATCH: ("question", "done"),
    }[state]
    for name in ("no_question", "plan", "done"):
        if name not in meant and getattr(found, name) is not None:
            errors.append(f"{name} means nothing in {state}; ignored")
    if found.plan_error:
        errors.append(found.plan_error)
    if found.question is not None:
        return "asked", extra, errors
    if state is State.CLARIFY and found.no_question is not None:
        return "no_material_question", extra, errors
    if state is State.PLAN and found.plan is not None:
        plan, why = _plan(workspace, found.plan)
        if plan is not None:
            why = _keep(brief, plan["commit"], f"refs/valor/plans/{turn_id}", turn_id)
            plan = None if why else plan
        if plan is None:
            errors.append(why)
        else:
            extra["plan"] = plan
            return "planned", extra, errors
    if state in (State.BUILD, State.PATCH) and found.done is not None:
        candidate, why = _candidate(workspace, turn_id)
        if candidate is not None:
            why = _keep(brief, candidate["sha"], f"refs/valor/candidates/{turn_id}", turn_id)
            candidate = None if why else candidate
        if candidate is None:
            errors.append(why)
        else:
            extra["candidate"] = candidate
            return "candidate", extra, errors
    return ("idle" if finished else "failed"), extra, errors


async def _reply_all(conn, action: broker.Action) -> tuple[broker.Action, str | None]:
    """An `email.send` naming `reply_to` (a received email's `received_id`
    or `message_id`) as the reply to all of it: recipients, subject, and
    threading from `mail.reply_all`, the turn's `body` and `files` kept.
    Returns the action, or an error when no email was received under that
    id."""
    ref = str(action.payload["reply_to"])
    row = await (
        await conn.execute(
            "SELECT payload FROM events WHERE type = 'message.received' AND task_id = 'email' "
            "AND (payload->>'received_id' = %s OR payload->>'message_id' = %s) ORDER BY id LIMIT 1",
            (ref, ref),
        )
    ).fetchone()
    if row is None:
        return action, f"reply_to {ref} names no received email"
    payload = {
        **mail.reply_all(row[0], settings.email_address),
        "body": action.payload.get("body") or "",
        "files": action.payload.get("files") or [],
    }
    return broker.Action("email.send", ",".join(sorted(payload["to"])), payload), None


async def record(
    conn,
    task_id: str,
    turn_id: str,
    found: signals.Signals,
    *,
    state: State,
    workspace: str | None,
    finished: bool = True,
    brief: tasks.Brief | None = None,
) -> str:
    """Ledger what a turn left, sending each effect request but a merge to
    the broker. Returns the verdict. For a task with a kernel mirror, a plan
    commit or a candidate counts only once it is fetched into the mirror."""
    verdict, extra, errors = _verdict(state, found, workspace, turn_id, finished, brief)
    effects = []
    for entry in found.effects:
        if "request" in entry and entry["request"]["action_type"] == "merge":
            entry = {**entry, "error": "the merge is the kernel's to request"}
        elif "request" in entry:
            r = entry["request"]
            action = broker.Action(r["action_type"], r["target"], r["payload"])
            if action.action_type == "email.send" and "reply_to" in action.payload:
                action, said = await _reply_all(conn, action)
                if said:
                    effects.append({**entry, "error": said})
                    continue
            outcome = await broker.request(conn, task_id, action, request_id=f"{turn_id}/{entry['file']}")
            entry = {**entry, "effect_id": outcome.effect_id, "kind": outcome.kind, "error": outcome.error}
        effects.append(entry)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        current = machine.fold(await ledger.read(conn, task_id)).state
        await ledger.append(
            conn,
            task_id,
            "turn.collected",
            {
                "turn_id": turn_id,
                "state": state.value,
                "verdict": verdict,
                "question": found.question,
                "no_question": found.no_question,
                "done": found.done,
                "plan": found.plan,
                "candidate": extra.get("candidate"),
                "errors": errors,
                "effects": effects,
            },
        )
        if current is state and verdict == "asked":
            await ledger.append(
                conn,
                task_id,
                "question.asked",
                {
                    "question_id": ledger.new_id(),
                    "turn_id": turn_id,
                    "text": found.question,
                    "state": state.value,
                },
            )
        elif current is state and verdict == "planned":
            await ledger.append(conn, task_id, "plan.written", {"turn_id": turn_id, **extra["plan"]})
    return verdict


async def answer(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Record the answer to the task's open question. `by` names who wrote
    it and `role_played` says whether they stood in for Tom. Returns the
    question's id."""
    text = text.strip()
    if not text:
        raise ValueError("an answer has text")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy:
            raise LookupError(f"task {task_id} predates the state machine")
        if f.calibration:
            raise LookupError(f"task {task_id} is a calibration task")
        if f.state is not State.WAITING:
            raise LookupError(f"task {task_id} has no open question (it is {f.state})")
        await ledger.append(
            conn,
            task_id,
            "question.answered",
            {
                "question_id": f.open_question,
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return f.open_question


async def feedback(
    conn,
    task_id: str,
    text: str,
    *,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Record Tom's feedback on the task's delivery, in `merge` or `merged`,
    which sends the work to `patch` and opens a new loop window. Refused
    while the merge's intent has no outcome. Returns the feedback's id."""
    text = text.strip()
    if not text:
        raise ValueError("feedback has text")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy:
            raise LookupError(f"task {task_id} predates the state machine")
        if f.calibration:
            raise LookupError(f"task {task_id} is a calibration task")
        if f.state is State.STOPPED:
            raise LookupError(f"task {task_id} is stopped; a stopped task takes no feedback")
        if f.state is State.WAITING:
            raise LookupError(f"task {task_id} has an open question; answer it with `answer`")
        if f.state not in (State.MERGE, State.MERGED):
            raise LookupError(f"task {task_id} is in {f.state}; feedback is on a delivery")
        if f.merge_effect and f.merge_effect["state"] == "in_flight":
            raise LookupError(f"task {task_id}'s merge is in flight; wait for its outcome")
        feedback_id = ledger.new_id()
        await ledger.append(
            conn,
            task_id,
            "feedback.given",
            {
                "feedback_id": feedback_id,
                "on_delivery": (f.delivery or {}).get("summary"),
                "candidate": {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}
                if f.candidate
                else None,
                "text": text,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return feedback_id


async def open_question(conn, task_id: str) -> dict[str, Any] | None:
    state = await tasks.status(conn, task_id)
    unanswered = [q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None]
    return unanswered[-1] if unanswered else None


def _entry_prompt(entry: dict[str, Any] | None, rows: list[dict]) -> str | None:
    if not entry:
        return None
    kind, p = entry["type"], entry["payload"]
    if kind == "question.answered":
        return f"# Tom's answer\n\n{p['text']}"
    if kind == "feedback.given":
        return f"# Tom's feedback on the delivery\n\n{p['text']}"
    if kind == "critique.decided":
        lines = [f"- [{x.get('kind', 'finding')}] {x.get('text', '')}" for x in p.get("findings") or []]
        return f"# Critique: {p['verdict']}\n\n" + ("\n".join(lines) or "No findings.")
    if kind == "join":
        found = machine.fold(rows).join
        lines = [f"- [{x.source}, {x.kind}] {x.text}" for x in (found.findings if found else ())]
        return "# Findings from the checks\n\n" + ("\n".join(lines) or "No findings were written.")
    if kind == "turn.collected" and p.get("verdict") == "no_material_question":
        return f"# No material question; on to the plan\n\n{p.get('no_question') or ''}".strip()
    return None


async def next_prompt(conn, task_id: str) -> tuple[str, str | None]:
    """The next turn's prompt and the harness session it resumes."""
    rows = await ledger.read(conn, task_id)
    f = machine.fold(rows)
    if f.session is None:
        prompt = (await tasks.brief(conn, task_id)).instruction
    else:
        prompt = (None if f.entry_finished else _entry_prompt(f.entry, rows)) or "Continue."
    steering = _steering(f.steering)
    notes = _errors_report(f.last_collected)
    report = _effects_report(f.last_collected, (await tasks.status(conn, task_id))["effects"])
    return "\n\n".join(x for x in (prompt, steering, notes, report) if x), f.session


def _steering(steered: list[dict[str, Any]]) -> str:
    """What Tom wrote while the last working turn ran (or since it
    finished), for the next working turn."""
    if not steered:
        return ""
    lines = ["Tom wrote while you worked:"]
    for p in steered:
        paths = [a["path"] for a in p.get("attachments") or [] if a.get("path")]
        lines.append(f"- {p.get('text', '')}" + (f" (attachments: {', '.join(paths)})" if paths else ""))
    return "\n".join(lines)


def _errors_report(collected: dict[str, Any] | None) -> str:
    if not collected or not collected.get("errors"):
        return ""
    return "What did not count from your last turn:\n" + "\n".join(f"- {e}" for e in collected["errors"])


def _effects_report(collected: dict[str, Any] | None, now: dict[str, str]) -> str:
    """What became of the last turn's effect requests, as the ledger has
    them now (a held push Tom has since released reads as done)."""
    if not collected or not collected.get("effects"):
        return ""
    lines = ["What became of the effects you requested last turn:"]
    for e in collected["effects"]:
        if "error" in e and "effect_id" not in e:
            lines.append(f"- {e['file']}: {e['error']}")
            continue
        r = e["request"]
        kind = now.get(e["effect_id"], e["kind"])
        said = {
            "pending": "held for Tom's approval",
            "done": "done",
            "refused": f"refused: {e['error']}",
            "failed": f"failed: {e['error']}",
        }.get(kind, kind)
        lines.append(f"- {r['action_type']} -> {r['target']} (effect {e['effect_id']}): {said}")
    return "\n".join(lines)
