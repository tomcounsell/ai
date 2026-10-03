"""Fresh sessions: one turn that never resumes and never reads the working
session. Critique runs here (1.4a); review and docs join it in 1.4b and 1.4c.

A fresh session gets:

- a checkout of its own under `<task>/checks/<stage>-<key>/repo`, made from
  the kernel mirror: for critique (and review) a *blind* checkout holding
  only two commits the kernel made, the base's tree and the plan's (or the
  candidate's) tree, so no builder commit message or intermediate commit
  exists in it;
- inputs as files under `.valor/inputs/`, written by the kernel from ledger
  rows: the request verbatim, Tom's answers and feedback with provenance,
  the diff against the base, and the stage's own. A plan or candidate whose
  tree holds `.valor` is refused, `.valor` must not exist before the kernel
  makes it, and every input is written relative to a descriptor with no
  link followed and no file overwritten, so nothing committed can redirect
  a write or plant a verdict;
- its own sandbox profile, `TMPDIR`, and Claude Code config directory, with
  the whole work directory, `/private/tmp`, `/private/var/tmp`,
  `/private/var/folders`, and the user's Claude Code state denied, so it
  reads nothing the builder wrote in the paths the kernel names for the
  builder (its clone, caches, `TMPDIR`, and Claude Code state); what the
  builder writes elsewhere in the user's home is outside this (harnesses.md,
  Known openings). Critique gets no database credential and no service
  port;
- a Brief carrying the stage file and the verdict channel
  (`skills/sdlc/verdict.md`) instead of the working session's: no question,
  no effect;
- one turn, recorded with `fresh: true`, whose session is never resumed.

Its verdict is `.valor/verdict.json`, read without following links or
blocking (`workspace.read_verdict`); the kernel validates it and writes the
verdict row. A turn that fails, is stopped, or leaves no valid verdict
writes no verdict: the runner returns `failed` (or `stopped`), and the next
run starts the stage again.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import db, git, ledger, machine, runs, tasks, verdicts, workspace
from core.machine import State
from core.settings import resolve_seat

# Builds one fresh turn: (prompt, checkout, model, harness settings, harness
# name) -> builder.
FreshFor = Callable[[str, str, str, dict[str, Any], str], Callable[[str, str, str], runs.TurnCommand]]

SEATS = {"critique": "frontier", "docs": "frontier", "review": "reviewer"}


def _answers(rows: list[dict]) -> str:
    """Every question, Tom's answer, and his feedback, in ledger order, with
    who wrote each."""
    out: list[str] = []
    questions: dict[str, str] = {}
    for r in rows:
        p = r["payload"]
        if r["type"] == "question.asked":
            questions[p["question_id"]] = p.get("text") or ""
        elif r["type"] == "question.answered":
            who = p.get("provenance") or {}
            out.append(
                f"## Question\n\n{questions.get(p.get('question_id'), '')}\n\n"
                f"## Answer (by {who.get('by')}, role played: {who.get('role_played')})\n\n{p.get('text')}"
            )
        elif r["type"] == "feedback.given":
            who = p.get("provenance") or {}
            out.append(
                f"## Feedback (by {who.get('by')}, role played: {who.get('role_played')})\n\n{p.get('text')}"
            )
    return "\n\n".join(out) or "No questions were asked and no feedback was given."


def _quoted(value: Any) -> str:
    """A value a turn chose, as JSON: quoted, newlines escaped."""
    return json.dumps(value, ensure_ascii=False)


def critique_inputs(
    checkout: Path, rows: list[dict], f: machine.Fold, b: tasks.Brief, diff: str
) -> list[str]:
    plan = f.plan or {}
    critiques = [
        r["payload"] for r in rows if r["type"] == "critique.decided"
    ]  # earlier rounds, so a second round can see whether the first round's findings were met
    files = {
        "request.md": b.instruction,
        "answers.md": _answers(rows),
        "diff.patch": diff,
        "plan.md": (
            f"The plan file: {_quoted(plan.get('path'))}\n"
            f"Its stakes: {_quoted(plan.get('stakes'))}\n"
            f"Critique rounds: {plan.get('critique_rounds')}; review rounds: {plan.get('review_rounds')}\n"
            f"Scope additions: {_quoted(plan.get('scope') or [])}\n"
        ),
        "critiques.md": "\n\n".join(
            f"## Critique {i + 1}: {c.get('verdict')}\n\n"
            + "\n".join(f"- [{x.get('kind')}] {x.get('text')}" for x in c.get("findings") or [])
            for i, c in enumerate(critiques)
        )
        or "No earlier critique.",
    }
    workspace.write_inputs(checkout, files)
    return list(files)


def prompt(files: list[str]) -> str:
    return "# Inputs\n\n" + "\n".join(f"- `.valor/inputs/{n}`" for n in files)


class Malformed(ValueError):
    """A verdict file the kernel will not record."""


def _verdict_fields(data: dict[str, Any]) -> tuple[str, list, dict[str, int]]:
    verdict = data.get("verdict")
    findings = data.get("findings") or []
    raised = data.get("raise") or {}
    if not isinstance(verdict, str):
        raise Malformed("verdict.json names no verdict")
    if not isinstance(findings, list) or not all(isinstance(x, (dict, str)) for x in findings):
        raise Malformed("findings is not a list of findings")
    for x in findings:
        if isinstance(x, dict) and not isinstance(x.get("text"), str):
            raise Malformed("a finding has no text")
    if verdict not in machine.VERDICTS[State.CRITIQUE]:
        raise Malformed(f"verdict {verdict!r} is not one of {sorted(machine.VERDICTS[State.CRITIQUE])}")
    if not isinstance(raised, dict):
        raise Malformed("raise is not an object")
    for k, v in raised.items():
        if (
            k not in ("critique_rounds", "review_rounds")
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v not in machine.ROUNDS
        ):
            raise Malformed(f"raise {k}={v!r}: each count is critique_rounds or review_rounds, 0 to 2")
    return verdict, findings, raised


def critique_runner(fresh_for: FreshFor, model: str | None = None, seat: str | None = None):
    """The runner for `State.CRITIQUE`, at `seat` (default: critique's own,
    which names a harness and a model). `model` overrides the seat's pinned
    model (the live test runs a light model to keep its spend small)."""

    model_ = model

    async def run(ctx) -> dict[str, Any]:
        async with await db.connect(ctx.dsn) as conn:
            rows = await ledger.read(conn, ctx.task_id)
            f = machine.fold(rows)
            if f.state is State.STOPPED:
                return {"status": "stopped", "state": await tasks.status(conn, ctx.task_id)}
            if f.state is not State.CRITIQUE:
                return {"status": "moved"}
            b = await tasks.brief(conn, ctx.task_id)
            state = await tasks.status(conn, ctx.task_id)
        if not b.mirror:
            return {
                "status": "failed",
                "state": state,
                "turn": {"result": "a fresh session runs only in a workspace the kernel provisioned"},
            }
        lay = workspace.Layout(Path(b.mirror).parent)
        plan_sha = f.plan["sha256"]
        check_dir = workspace.fresh_dir(lay.checks / f"critique-{plan_sha[:12]}")
        checkout = check_dir / "repo"
        try:
            made = workspace.blind_checkout(b.mirror, b.base_sha, f.plan["commit"], checkout)
            diff = git.trusted(checkout, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                               made["base"], made["candidate"])  # fmt: skip
        except git.GitError as exc:
            return {"status": "failed", "state": state, "turn": {"result": f"critique checkout: {exc}"}}
        try:
            files = critique_inputs(checkout, rows, f, b, diff)
        except (OSError, ValueError) as exc:
            return {"status": "failed", "state": state, "turn": {"result": f"critique inputs: {exc}"}}
        harness = workspace.check_harness(lay, check_dir, [], b.harness.get("env", {}), services=False)
        harness_name, seat_model = resolve_seat(seat or SEATS["critique"])
        model = model_ or seat_model
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            ended = await runs.run_turn(
                ctx.gateway,
                ctx.task_id,
                fresh_for(prompt(files), str(checkout), model, harness, harness_name),
                dsn=ctx.dsn,
                state=State.CRITIQUE.value,
                fresh="critique",
            )
        except tasks.TaskStopped:
            return {"status": "stopped"}
        async with await db.connect(ctx.dsn) as conn:
            now = await tasks.status(conn, ctx.task_id)
        if ended["outcome"] == "stopped":
            return {"status": "stopped", "state": now, "turn": ended}
        if ended["outcome"] != "done" or ended["result"].get("is_error"):
            return {"status": "failed", "state": now, "turn": ended}
        data, why = workspace.read_verdict(checkout, ended["turn_id"])
        if data is None:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"no verdict: {why}"}}
        if not await ctx.alive():
            return {"status": "lock lost"}
        try:
            verdict, findings, raised = _verdict_fields(data)
            async with await db.connect(ctx.dsn) as conn:
                await verdicts.record_critique(
                    conn,
                    ctx.task_id,
                    verdict,
                    findings=findings,
                    raised=raised,
                    leg="session",
                    model=model,
                    usd_micros=int(ended.get("metered_usd_micros") or 0),
                    turn_id=ended["turn_id"],
                    plan_sha256=plan_sha,
                )
        except (ValueError, verdicts.VerdictRefused) as exc:
            return {"status": "failed", "state": now, "turn": {**ended, "result": f"verdict refused: {exc}"}}
        return {"status": "moved"}

    return run
