#!/usr/bin/env python3
"""PM Job tools: create Jobs, author goals, add/remove promises (#2494).

Room scope is enforced **at the tool layer**, not in the system prompt
(prompt-level constraints drift; teammate permissions already live in three
drifting sources and once falsely blocked ``/do-issue``). Every Job lookup
is scoped to the calling session's own Room — a Job in another Room is
structurally unreachable through this tool, and the refusal is loud
(:class:`JobToolError`), never silent.

The PM's contract (durability plan #2494, Milestone 3):

* **Goal authoring** is the PM's mandated first step on any Job whose goal
  is still the router's mechanical mint placeholder (``author-goal``).
* **Promises are PM-authored and PM-discharged**: standing by an outbound
  deferral means ``promise-add`` (a new goal version); delivering means
  ``promise-remove``. No mechanical trigger ever writes a promise.

Usage (CLI; session resolved from ``VALOR_SESSION_ID``):
    python -m tools.job_tool create --goal "Ship the fix end to end"
    python -m tools.job_tool author-goal --job-id ID --text "Real goal v1"
    python -m tools.job_tool promise-add --job-id ID --text "I'll report back after CI"
    python -m tools.job_tool promise-remove --job-id ID --promise-id PID
    python -m tools.job_tool show --job-id ID
    python -m tools.job_tool list
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Script-mode bootstrap (same as tools/send_message.py): keep repo root on
# sys.path so `python tools/job_tool.py` resolves imports like `-m` mode.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path[0] = _REPO_ROOT
elif _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logger = logging.getLogger(__name__)


class JobToolError(Exception):
    """Loud refusal: unknown session, unresolvable Room, or out-of-Room Job."""


def _session_room_id(session_id: str) -> str:
    """Resolve the calling session's Room id, or refuse."""
    from models.agent_session import AgentSession
    from models.room import room_id_for_session

    sessions = list(AgentSession.query.filter(session_id=session_id))
    if not sessions:
        raise JobToolError(f"unknown session {session_id!r} — cannot resolve a Room")
    rid = room_id_for_session(sessions[0])
    if not rid:
        raise JobToolError(f"session {session_id!r} has no project_key — cannot resolve a Room")
    return rid


def _own_room_job(session_id: str, job_id: str):
    """Fetch a Job scoped to the session's own Room. Cross-Room ids miss.

    The Room-scope enforcement: the lookup filters on the session's own
    ``room_id``, so a Job living in any other Room is structurally not
    found — there is no cross-Room code path to guard.
    """
    from models.job import Job

    rid = _session_room_id(session_id)
    jobs = list(Job.query.filter(room_id=rid, id=job_id))
    if not jobs:
        raise JobToolError(
            f"job {job_id!r} not found in your Room ({rid}) — "
            "Jobs in other Rooms are not addressable from this session"
        )
    return jobs[0]


def create_job(session_id: str, goal: str):
    """Create a PM-authored Job in the session's own Room (goal v1)."""
    from models.job import Job

    if not goal or not goal.strip():
        raise JobToolError("a Job requires a non-empty goal")
    rid = _session_room_id(session_id)
    job = Job.mint(rid, goal)
    # The PM authored this goal directly — replace the mechanical mint
    # placeholder with a pm-authored v1 so goal_is_placeholder() is False
    # from birth. (mint() is reused for its id/recency/status stamping.)
    job._write_goal_data(
        {
            "versions": [
                {
                    "ts": job.goal_versions()[0]["ts"],
                    "author": "pm",
                    "text": goal.strip(),
                }
            ],
            "promises": [],
        }
    )
    logger.info("[job-tool] session %s created job %s in %s", session_id, job.job_id, rid)
    return job


def author_goal(session_id: str, job_id: str, text: str):
    """Append a PM-authored goal version (the mandated first step on a
    placeholder-goal Job)."""
    if not text or not text.strip():
        raise JobToolError("goal text must be non-empty")
    job = _own_room_job(session_id, job_id)
    job.append_goal_version(text.strip(), author="pm")
    return job


def add_promise(session_id: str, job_id: str, text: str) -> str:
    """Stand by an outbound promise: append it to the Job's goal record."""
    if not text or not text.strip():
        raise JobToolError("promise text must be non-empty")
    job = _own_room_job(session_id, job_id)
    promise_id = job.add_promise(text.strip())
    logger.info(
        "[job-tool] session %s recorded promise %s on job %s", session_id, promise_id, job_id
    )
    return promise_id


def remove_promise(session_id: str, job_id: str, promise_id: str) -> bool:
    """Discharge a delivered promise (append-only removal)."""
    job = _own_room_job(session_id, job_id)
    removed = job.remove_promise(promise_id)
    if removed:
        logger.info(
            "[job-tool] session %s discharged promise %s on job %s", session_id, promise_id, job_id
        )
    return removed


def _job_summary(job) -> dict:
    return {
        "job_id": job.job_id,
        "room_id": job.room_id,
        "status": job.status,
        "goal": job.current_goal(),
        "goal_is_placeholder": job.goal_is_placeholder(),
        "goal_versions": len(job.goal_versions()),
        "open_promises": job.open_promises(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PM Job tools (Room-scoped)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a Job in your own Room")
    p_create.add_argument("--goal", required=True)

    p_goal = sub.add_parser("author-goal", help="Append a PM-authored goal version")
    p_goal.add_argument("--job-id", required=True)
    p_goal.add_argument("--text", required=True)

    p_padd = sub.add_parser("promise-add", help="Record a promise you are standing by")
    p_padd.add_argument("--job-id", required=True)
    p_padd.add_argument("--text", required=True)

    p_prem = sub.add_parser("promise-remove", help="Discharge a delivered promise")
    p_prem.add_argument("--job-id", required=True)
    p_prem.add_argument("--promise-id", required=True)

    p_show = sub.add_parser("show", help="Show one Job in your Room")
    p_show.add_argument("--job-id", required=True)

    sub.add_parser("list", help="List recent Jobs in your Room")

    args = parser.parse_args()

    session_id = os.environ.get("VALOR_SESSION_ID")
    if not session_id:
        print("Error: VALOR_SESSION_ID not set.", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "create":
            job = create_job(session_id, args.goal)
            print(json.dumps(_job_summary(job), indent=2))
        elif args.command == "author-goal":
            job = author_goal(session_id, args.job_id, args.text)
            print(json.dumps(_job_summary(job), indent=2))
        elif args.command == "promise-add":
            promise_id = add_promise(session_id, args.job_id, args.text)
            print(json.dumps({"promise_id": promise_id, "job_id": args.job_id}, indent=2))
        elif args.command == "promise-remove":
            removed = remove_promise(session_id, args.job_id, args.promise_id)
            if not removed:
                print(
                    f"Error: no open promise {args.promise_id!r} on job {args.job_id!r}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(json.dumps({"removed": True, "promise_id": args.promise_id}, indent=2))
        elif args.command == "show":
            job = _own_room_job(session_id, args.job_id)
            print(json.dumps(_job_summary(job), indent=2))
        elif args.command == "list":
            from models.job import Job

            rid = _session_room_id(session_id)
            jobs = Job.recent_for_room(rid, limit=10)
            print(json.dumps([_job_summary(j) for j in jobs], indent=2))
    except JobToolError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
