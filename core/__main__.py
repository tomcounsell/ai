"""Kernel command line: `python -m core <command>`.

migrate [--db NAME]            create the role, the database, the schema
status TASK_ID                 the task as a fold over its ledger
ledger TASK_ID                 every ledger row of the task
stop TASK_ID [--reason TEXT]   stop the task now, wherever its turn runs
pending                        act-class effects held for Tom
approve EFFECT_ID --note TEXT  Tom's tap on one held effect
correct TEXT [--by] [--via]    record Tom's next correction (global, direct)
corrections                    every correction, in force for every turn
"""

import argparse
import asyncio
import json

from core import broker, corrections, db, ledger, tasks


async def _run(args) -> None:
    async with await db.connect() as conn:
        if args.command == "status":
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
                )
        elif args.command == "approve":
            print(await broker.approve(conn, args.effect_id, note=args.note))
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
    sub.add_parser("status").add_argument("task_id")
    sub.add_parser("ledger").add_argument("task_id")
    stop = sub.add_parser("stop")
    stop.add_argument("task_id")
    stop.add_argument("--reason", default="stopped from the command line")
    sub.add_parser("pending")
    approve = sub.add_parser("approve")
    approve.add_argument("effect_id")
    approve.add_argument("--note", required=True)
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
