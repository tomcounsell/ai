"""Guards: every checkpoint that holds or redirects work, ledgered with the
incident it prevents, the mission item it serves, and a ninety-day expiry.

The four granted with the pipeline on 2026-10-01, and the web guard granted on
2026-10-16 (docs/plans/orchestration.md), and the update room granted on
2026-10-20 (docs/plans/update.md), are seeded by `db.migrate` on the
`guards` stream, once each. This module records; the expiry routine (`core/routines.py`) reads the
rows and proposes the deletion of what is due.
"""

from datetime import date, timedelta
from typing import Any

from core import machine

STREAM = "guards"
GRANTED = date(2026, 10, 1)
EXPIRES = GRANTED + timedelta(days=90)  # 2026-12-30
NOTE = (
    "Every stage is a checkpoint. The plan sets the loops (0, 1, or 2 critique rounds and 0 or 1 "
    "review round, from the stakes). Test, review, and docs run in parallel on every candidate. "
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
            "the review checkpoint: a join sends work to patch on review changes, a red test, or "
            "docs changes, within the plan's rounds; the round count is Tom's 2026-10-01 pipeline decision"
        ),
        "incident": (
            "popoto #633: the stale-cache bug was moved, not removed, and a lenient Sonnet stand-in "
            "accepted it (rebuild-baseline.md, Review rounds). Review sends work back at most once"
        ),
        "mission_items": [1],
        "source": "docs/sdlc-state-machine.md, checks.review, Why; rebuild-baseline.md, Review rounds",
    },
    {
        "guard_id": "web.agent_only",
        "name": (
            "the web agent: in a Claude Code turn, WebFetch and WebSearch run only inside the valor:web "
            "agent, a leaf any agent at any depth can start (skills/valor/hooks/web_only.py)"
        ),
        "incident": (
            "injection through inbound and retrieved content: the old system's inbound injection "
            "inspection (#3648); a page read by a working agent puts its text in that agent's context"
        ),
        "mission_items": [4],
        "source": "docs/plans/orchestration.md, Tom's grants",
        "granted_at": "2026-10-16",
        "expires": "2027-01-14",
        "note": (
            'Tom, 2026-10-16: "for web fetch, I recommend a dedicated subagent that must be used." '
            '"subagents can create subagents to unlimited degree. so web fetch is only ever another '
            'subagent away."'
        ),
        "via": "the 2026-10-16 orchestration rulings, seeded by migrate",
        # It fires inside a turn, which cannot reach the ledger.
        "firing_record": False,
    },
    {
        "guard_id": "update.room",
        "name": "/update binds only in Valor's engineering room (core/intake.py, UPDATE_CHAT)",
        "incident": "none; accepted by Tom 2026-10-20",
        "mission_items": [1],
        "source": "docs/plans/update.md, Grant",
        "granted_at": "2026-10-20",
        "expires": "2027-01-18",
        "note": (
            "Tom, 2026-10-20: the room restriction granted, any verified member of the room may /update. "
            "The risk, from the round-2 critique: a stranger restarting the kernel by DM or email and "
            "cancelling background turns."
        ),
        "via": "docs/plans/update.md, Grant",
        # The firing fold reads only pipeline verdict rows (`machine.VERDICT_ROWS`),
        # and a refused `/update` is an ordinary message: a row of its own would be
        # code for no use.
        "firing_record": False,
    },
)


def seeded_payload(guard: dict[str, Any]) -> dict[str, Any]:
    """A seeded guard's row: the pipeline's grant date, expiry, note and
    provenance unless the entry carries its own."""
    entry = {k: v for k, v in guard.items() if k != "via"}
    granted = entry.setdefault("granted_at", GRANTED.isoformat())
    entry.setdefault("expires", EXPIRES.isoformat())
    entry.setdefault("note", NOTE)
    return {
        **entry,
        "provenance": {
            "by": "tom",
            "via": guard.get("via", "the 2026-10-01 pipeline decision, seeded by migrate"),
            "role_played": False,
            "at": granted,
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
