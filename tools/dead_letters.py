"""Operator break-glass for the dead-letter queue.

    python -m tools.dead_letters list
    python -m tools.dead_letters replay --stage extraction

A thin wrapper over ``bridge.dead_letters``. Deliberately not exposed as an
MCP tool: re-sending messages that already failed is a rare,
destructive-adjacent action a human takes after reading the dashboard tile,
and registering it would spend context budget in every session for something
used a handful of times a year. The tile is the agent-visible surface.

``telegram_send`` replay needs a live Telethon client, which this CLI does not
have — the bridge replays that stage on every connect.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _list(args) -> int:
    from bridge import dead_letters

    counts = dead_letters.counts_by_stage(args.project_key)
    width = max(len(stage) for stage in dead_letters.STAGES)
    for stage in sorted(dead_letters.STAGES):
        print(f"{stage:<{width}}  {counts.get(stage, 0)}")
    print(f"\ntotal: {sum(counts.values())} (per-stage cap {dead_letters.DEAD_LETTER_STAGE_CAP})")
    return 0


def _replay(args) -> int:
    from bridge import dead_letters

    if args.stage not in dead_letters.STAGES:
        print(f"unknown stage {args.stage!r}; see `list` for the stage names", file=sys.stderr)
        return 2
    if args.stage == "telegram_send":
        print(
            "telegram_send replay needs a live Telethon client; the bridge runs it "
            "on every connect. Restart the bridge instead.",
            file=sys.stderr,
        )
        return 2

    replayed = asyncio.run(dead_letters.replay_stage(args.stage))
    print(f"replayed {replayed} row(s) from stage {args.stage}")
    return 0


def _evict(args) -> int:
    from bridge import dead_letters

    deleted = dead_letters.evict_overflow(args.project_key)
    print(f"evicted {deleted} overflow row(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.dead_letters",
        description="Inspect and replay the pipeline's dead-letter rows.",
    )
    parser.add_argument("--project-key", default=None, help="Project namespace (default: valor)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Row counts per stage").set_defaults(func=_list)

    replay = sub.add_parser("replay", help="Replay every replayable row of one stage")
    replay.add_argument("--stage", required=True, help="Stage name (see `list`)")
    replay.set_defaults(func=_replay)

    sub.add_parser("evict", help="Trim each stage back to its cap").set_defaults(func=_evict)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
