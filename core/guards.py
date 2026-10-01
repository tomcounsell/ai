"""Guards: every checkpoint that holds or redirects work, ledgered with the
incident it prevents, the mission item it serves, and a ninety-day expiry.

The four granted with the pipeline on 2026-10-01 are seeded by `db.migrate`
on the `guards` stream, once each. A governance instance Tom grants on a
task (one hunk that adds a check, gate, hook, round, or review step) is a
`guard.granted` row on that task, bound to the instance's id: an unchanged
hunk stays granted across patches, a changed or moved hunk is a new
instance. This module records; deleting an expired guard is a routine for
milestone 4.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from core import ledger, machine

STREAM = "guards"
GRANTED = date(2026, 10, 1)
EXPIRES = GRANTED + timedelta(days=90)  # 2026-12-30
NOTE = (
    "Every stage is a checkpoint. The plan sets the loops (0, 1, or 2 critique and review rounds, "
    "from the stakes). Test, review, and docs run in parallel on every candidate. "
    "(Tom's pipeline decision of 2026-10-01, docs/sdlc-state-machine.md, The pipeline Tom set.)"
)

SEEDED: tuple[dict[str, Any], ...] = (
    {
        "guard_id": machine.GUARD_JUDGE,
        "name": "the request judge: a request judged thin goes to clarify",
        "incident": (
            "psyoptimal #894 (task 32f800bce8a2: two feedback rounds for three decisions one message "
            "would have settled); popoto #191 bare (fidelity 1, 3 of 11 hidden tests); popoto #188 bare "
            "(built a feature Tom did not want)"
        ),
        "mission_items": [3, 6],
        "source": "docs/sdlc-state-machine.md, judge, Guard record; docs/judgement-layer.md, The guard entry",
    },
    {
        "guard_id": machine.GUARD_BREADTH,
        "name": "the test-breadth check: a green suite with untested behaviors is gaps",
        "incident": (
            "every replay wrote fewer tests than its reference: #872 missed the archived-team guards, "
            "#191 the list key name and hash exclusion, the demonstration 10 tests against the "
            "reference's 35; on popoto #633 the clarify arm broke a bound in an existing test and was "
            "accepted"
        ),
        "mission_items": [1],
        "source": "docs/sdlc-state-machine.md, checks.test, Why and Guard record for breadth",
    },
    {
        "guard_id": machine.GUARD_CRITIQUE,
        "name": "the critique loop: a revise sends the plan back to plan",
        "incident": (
            "popoto #633: the clarify arm built on a wrong premise nothing read before code existed, "
            "and correctness fell from 5 to 2"
        ),
        "mission_items": [1],
        "source": "docs/sdlc-state-machine.md, critique, Why",
    },
    {
        "guard_id": machine.GUARD_REVIEW,
        "name": (
            "the review checkpoint: a join sends work to patch on review changes, or once in the "
            "repair round; the round count is Tom's 2026-10-01 pipeline decision"
        ),
        "incident": (
            "popoto #633: the stale-cache bug was moved, not removed, and a lenient Sonnet stand-in "
            "accepted it (rebuild-baseline.md, Review rounds). The second round has no incident of its "
            "own and falls to expiry on 2026-12-30 unless one occurs"
        ),
        "mission_items": [1],
        "source": "docs/sdlc-state-machine.md, checks.review, Why; rebuild-baseline.md, Review rounds",
    },
)


def seeded_payload(guard: dict[str, Any]) -> dict[str, Any]:
    return {
        **guard,
        "granted_at": GRANTED.isoformat(),
        "expires": EXPIRES.isoformat(),
        "note": NOTE,
        "provenance": {
            "by": "tom",
            "via": "the 2026-10-01 pipeline decision, seeded by migrate",
            "role_played": False,
            "at": GRANTED.isoformat(),
        },
    }


def seed(conn) -> int:
    """Seed the granted guards that the ledger does not hold yet, as the
    owner, synchronously. Returns how many were written."""
    from psycopg.types.json import Jsonb

    written = 0
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (STREAM,))
        for guard in SEEDED:
            found = conn.execute(
                "SELECT 1 FROM events WHERE type = 'guard.granted' AND payload->>'guard_id' = %s",
                (guard["guard_id"],),
            ).fetchone()
            if found is None:
                conn.execute(
                    "INSERT INTO events (task_id, type, payload) VALUES (%s, 'guard.granted', %s)",
                    (STREAM, Jsonb(seeded_payload(guard))),
                )
                written += 1
    return written


class GrantRefused(LookupError):
    pass


async def grant(
    conn,
    task_id: str,
    instance_id: str,
    *,
    note: str,
    incident: str | None = None,
    mission_item: str | None = None,
    via: str = "the command line",
) -> str:
    """Tom's tap on one governance instance of the task's current candidate.
    It is Tom's: written `by: tom`, never role-played. Incident and mission
    item default to what the verdict naming the instance gave; a grant
    missing either is refused (the governance paragraph: "missing either, it
    is not added"). Returns the guard id."""
    note = note.strip()
    if not note:
        raise GrantRefused("a grant carries Tom's message")
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        f = machine.fold(await ledger.read(conn, task_id))
        if f.legacy:
            raise GrantRefused(f"task {task_id} predates the state machine")
        if f.calibration:
            raise GrantRefused(f"task {task_id} is a calibration task")
        if f.state is not machine.State.MERGE:
            raise GrantRefused(f"task {task_id} is in {f.state}; a grant is given in merge")
        instance = next((i for i in f.instances() if i.id == instance_id), None)
        if instance is None:
            raise GrantRefused(f"the current candidate names no governance instance {instance_id}")
        if instance_id in f.granted:
            raise GrantRefused(f"instance {instance_id} is already granted")
        incident = incident or instance.incident
        mission_item = mission_item or instance.mission_item
        if not incident or not mission_item:
            raise GrantRefused(
                "a grant names the incident and the mission item; missing either, it is not added"
            )
        now = datetime.now(UTC)
        guard_id = f"grant-{ledger.new_id()}"
        await ledger.append(
            conn,
            task_id,
            "guard.granted",
            {
                "guard_id": guard_id,
                "name": instance.summary or f"governance in {instance.path}",
                "instance_id": instance_id,
                "path": instance.path,
                "candidate": {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}
                if f.candidate
                else None,
                "incident": incident,
                "mission_items": [mission_item],
                "granted_at": now.date().isoformat(),
                "expires": (now.date() + timedelta(days=90)).isoformat(),
                "note": note,
                "provenance": ledger.provenance("tom", via, False),
            },
        )
    return guard_id
