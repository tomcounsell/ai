"""Kernel command line: `python -m core <command>`.

migrate [--db NAME]            create the role, the database, the schema
start INSTRUCTION --budget-usd N [--ceiling C] [--workspace DIR]
      [--model M] [--harness-config FILE] [--mode bare|clarify]
                               start a task; prints its id. `clarify` has the
                               Brief ask Valor to inspect and ask before building
run TASK_ID                    run turns until a question, a delivery, the
                               budget's end, or a stop; prints one status line
answer TASK_ID TEXT [--by B] [--role-played]
                               the answer to the task's open question
feedback TASK_ID TEXT [--by B] [--role-played]
                               feedback on a delivered task; the next run
                               resumes its session with it. `--by` names who
                               wrote it (default tom); `--role-played` marks a
                               stand-in speaking for Tom
status TASK_ID                 the task as a fold over its ledger, with the
                               attention log (questions, answers, feedback)
ledger TASK_ID                 every ledger row of the task
stop TASK_ID [--reason TEXT]   stop the task now, wherever its turn runs
pending                        act-class effects held for Tom
approve EFFECT_ID --note TEXT  Tom's tap on one held effect
release EFFECT_ID              perform a held effect Tom approved
correct TEXT [--by] [--via]    record Tom's next correction (global, direct)
corrections                    every correction, in force for every turn

This module is the composition root: `run` and `release` wire the Claude
Code harness and the workspace performers into the kernel. Nothing else in
`core/` imports outside it.
"""

import argparse
import asyncio
import json
from pathlib import Path

from core import broker, corrections, db, ledger, session, tasks


def _performers(b: tasks.Brief) -> None:
    from tools.push_branch import PushBranch

    if b.workspace:
        broker.register(PushBranch(b.workspace))


def _turn_for(prompt: str, resume: str | None, b: tasks.Brief):
    from harnesses import claude_code

    return claude_code.workspace_turn(
        prompt, cwd=b.workspace, resume=resume, model=b.model, harness=b.harness
    )


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.4f}"


def _status_line(task_id: str, out: dict) -> str:
    state = out.get("state") or {}
    spent = f"spent {_usd(state.get('charged_usd_micros', 0))}, remaining {_usd(state.get('remaining_usd_micros', 0))}"
    status = out["status"]
    if status == "waiting":
        q = out["question"]
        return (
            f"QUESTION for Tom (task {task_id}, question {q['question_id']}; {spent}):\n\n{q['question']}\n\n"
            f'answer with: python -m core answer {task_id} "..."'
        )
    if status == "delivered":
        held = [e for e, s in state.get("effects", {}).items() if s == "pending"]
        lines = [f"DELIVERED (task {task_id}; {spent}):\n\n{state['delivered']}"]
        for effect_id in held:
            lines.append(
                f"\nheld for Tom: effect {effect_id}\n"
                f'  approve with: python -m core approve {effect_id} --note "..."\n'
                f"  then:         python -m core release {effect_id}"
            )
        return "\n".join(lines)
    turn = out.get("turn") or {}
    detail = {
        "stopped": "stopped",
        "budget exhausted": "the budget is spent",
        "failed": f"the turn failed: {turn.get('stderr_tail') or turn.get('result')}",
        "idle": f"{session.IDLE_TURNS} turns ended without a question or delivery",
    }[status]
    return f"{status.upper()} (task {task_id}; {spent}): {detail}"


async def _run_task(task_id: str) -> str:
    from core.gateway import Gateway

    async with await db.connect() as conn:
        b = await tasks.brief(conn, task_id)
    _performers(b)
    gateway = Gateway()
    await gateway.start()
    try:
        out = await session.run(gateway, task_id, _turn_for)
    finally:
        await gateway.close()
    return _status_line(task_id, out)


async def _run(args) -> None:
    if args.command == "run":
        print(await _run_task(args.task_id))
        return
    async with await db.connect() as conn:
        if args.command == "start":
            harness = json.loads(Path(args.harness_config).read_text()) if args.harness_config else {}
            brief = tasks.Brief(
                instruction=args.instruction,
                budget_usd_micros=round(args.budget_usd * 1_000_000),
                max_effect_class=args.ceiling,
                workspace=str(Path(args.workspace).resolve()) if args.workspace else None,
                model=args.model,
                harness=harness,
                mode=args.mode,
            )
            print(await tasks.start(conn, brief))
        elif args.command == "answer":
            question_id = await session.answer(
                conn, args.task_id, args.text, by=args.by, role_played=args.role_played
            )
            print(f"answered question {question_id}; continue with: python -m core run {args.task_id}")
        elif args.command == "feedback":
            try:
                feedback_id = await session.feedback(
                    conn, args.task_id, args.text, by=args.by, role_played=args.role_played
                )
            except LookupError as exc:
                raise SystemExit(str(exc.args[0])) from None
            print(f"feedback {feedback_id} recorded; continue with: python -m core run {args.task_id}")
        elif args.command == "status":
            print(json.dumps(await tasks.status(conn, args.task_id), indent=2))
        elif args.command == "ledger":
            print(ledger.render(await ledger.read(conn, args.task_id)))
        elif args.command == "stop":
            fresh = await tasks.stop(conn, args.task_id, reason=args.reason)
            print("stopped" if fresh else "already stopped")
        elif args.command == "pending":
            for effect in await broker.pending(conn):
                print(
                    f"{effect['effect_id']}  {effect['task_id']}  {effect['action_type']} -> {effect['target']}"
                    f"  {json.dumps(effect['payload'], sort_keys=True)}"
                )
        elif args.command == "approve":
            print(await broker.approve(conn, args.effect_id, note=args.note))
        elif args.command == "release":
            row = await (
                await conn.execute(
                    "SELECT task_id FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                    (args.effect_id,),
                )
            ).fetchone()
            if row is None:
                raise SystemExit(f"no held effect {args.effect_id}")
            _performers(await tasks.brief(conn, row[0]))
            outcome = await broker.release(conn, args.effect_id)
            print(
                f"{outcome.kind} {json.dumps(outcome.result, sort_keys=True)}"
                + (f" {outcome.error}" if outcome.error else "")
            )
        elif args.command == "correct":
            c = await corrections.record(conn, args.text, by=args.by, via=args.via)
            print(f"correction {c['number']} recorded, ledger row {c['event_id']}")
        elif args.command == "corrections":
            print(corrections.render(await corrections.in_force(conn)))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m core", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate").add_argument("--db")
    start = sub.add_parser("start")
    start.add_argument("instruction")
    start.add_argument("--budget-usd", type=float, required=True)
    start.add_argument("--ceiling", default="propose", choices=list(tasks.EFFECT_RANK))
    start.add_argument("--workspace")
    start.add_argument("--model", default="haiku")
    start.add_argument("--harness-config")
    start.add_argument("--mode", default="bare", choices=list(tasks.MODES))
    sub.add_parser("run").add_argument("task_id")
    for name in ("answer", "feedback"):
        reply = sub.add_parser(name)
        reply.add_argument("task_id")
        reply.add_argument("text")
        reply.add_argument("--by", default="tom")
        reply.add_argument("--role-played", action="store_true")
    sub.add_parser("status").add_argument("task_id")
    sub.add_parser("ledger").add_argument("task_id")
    stop = sub.add_parser("stop")
    stop.add_argument("task_id")
    stop.add_argument("--reason", default="stopped from the command line")
    sub.add_parser("pending")
    approve = sub.add_parser("approve")
    approve.add_argument("effect_id")
    approve.add_argument("--note", required=True)
    sub.add_parser("release").add_argument("effect_id")
    correct = sub.add_parser("correct")
    correct.add_argument("text")
    correct.add_argument("--by", default="tom")
    correct.add_argument("--via", default="the command line")
    sub.add_parser("corrections")
    args = parser.parse_args()
    if args.command == "migrate":
        print(db.migrate(args.db))
    else:
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
