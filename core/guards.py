"""Guards: every checkpoint that holds or redirects work, ledgered with the
incident it prevents, the mission item it serves, and a ninety-day expiry.

The four granted with the pipeline on 2026-10-01, and the web guard granted on
2026-10-16 (docs/plans/orchestration.md), the update room granted on
2026-10-20 (docs/plans/update.md), the session-only send granted on
2026-11-01 (docs/plans/one-voice.md), and the read-only mail sign-in
granted the same day (docs/plans/one-voice-mail.md), are seeded by `db.migrate` on the
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
            "fabrikam #894 (task 32f800bce8a2: two feedback rounds for three decisions one message "
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
        "guard_id": machine.GUARD_RESTATED,
        "name": "the builder's restatement: a raising restatement adds the review round",
        "incident": (
            'd0ec204a0, subject ending "(Tom, team chat 2026-10-29; prompt only)", plan 15141b3ae '
            '"(prompt only)", whose plan said "Tags are in the prompt only"; the build added an '
            "intake.root_of call in session._send_places that failed chat turns (fixed in 3cf7b882c). "
            "The extra round is Tom's grant on top of that incident"
        ),
        "mission_items": [1],
        "source": "docs/plans/restated-stakes.md, Governance",
        "granted_at": "2026-10-30",
        "expires": "2027-01-28",
        "note": (
            "Tom, 2026-10-30: \"the builder should be able to change the description from 'prompt "
            'only\' to something more accurate once the build is done." "Because it can add a review '
            "round it is governance: Tom's grant is this request.\""
        ),
        "via": "docs/plans/restated-stakes.md, Governance",
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
        "name": "/update binds only in Valor's engineering room (core/intake.py, the valor spec's update_chat)",
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
    {
        "guard_id": "send.sessions_only",
        "name": (
            "only a chat session's turn is offered or may request a send or canvas write; any other task "
            "reports to its session (core/__main__.py _performers, core/notices.py)"
        ),
        "incident": (
            "2026-10-31: the nightly Sentry routine's report reached Tom's Slack DM as kernel text no "
            "session wrote; his two questions in its thread were bound as feedback and the routine re-sent "
            "its report twice, unanswered"
        ),
        "mission_items": [6],
        "source": "docs/plans/one-voice.md, Grant",
        "granted_at": "2026-11-01",
        "expires": "2027-01-30",
        "note": (
            'Tom, 2026-11-01: "No routine or system should be impersonating you." "How can I trust '
            "anything coming from you if something else might be able to speak for you without your "
            'permission." Confirmed with a :+1: at 04:34 UTC on the design post.'
        ),
        "via": "docs/plans/one-voice.md, Grant",
        # A task's turn is never offered a send, so there is nothing to refuse and no firing.
        "firing_record": False,
    },
    {
        "guard_id": "mail.sessions_only",
        "name": (
            "Valor's Google sign-in for gws reads mail and cannot send it (gmail.readonly), and no "
            "mailbox password is in a vault a turn reads: mail as Valor leaves only as a chat session's "
            "email.send (docs/rollout.md step 6, docs/machine.md)"
        ),
        "incident": (
            "2026-10-31: the nightly Sentry routine's report reached Tom's Slack DM as kernel text no "
            "session wrote; after one-voice closed the kernel's sends, any task or routine could still "
            "email people as Valor through gws with gmail.modify or the vault's SMTP_PASSWORD"
        ),
        "mission_items": [6],
        "source": "docs/plans/one-voice-mail.md, Grant",
        "granted_at": "2026-11-01",
        "expires": "2027-01-30",
        "note": (
            'Tom, 2026-11-01: "No routine or system should be impersonating you." Confirmed with a :+1: '
            'at 04:34 UTC; "Agreed" at 05:37 UTC to closing email the same way.'
        ),
        "via": "docs/plans/one-voice-mail.md, Grant",
        # Google refuses the send; nothing in the kernel fires.
        "firing_record": False,
    },
    {
        "guard_id": "nightly.hold",
        "name": (
            "a Mac the watch finds broken, and the Mac watching it, skip the nightly Homebrew upgrade "
            "while the hold file stands (routines/watch, ~/.config/brew-nightly/hold-<host>); the "
            "Cowboy upgrades an hour after the others"
        ),
        "incident": (
            "2026-11-13: the 04:00 nightly upgraded postgresql@18 under the running server on all four "
            "Macs at once; every ledger write failed for 5 to 12 hours"
        ),
        "mission_items": [6],
        "source": "docs/plans/a4601299-db-self-recovery.md, Governance",
        "granted_at": "2026-11-13",
        "expires": "2027-02-11",
        "note": (
            'Tom, 2026-11-13, #see-issue-63: "what if the cowboy machine updated last? ... The cowboy is '
            'the one that has tailscale access to help the others"; "i\'m also trying to prevent getting '
            'me involved at all."'
        ),
        "via": "docs/plans/a4601299-db-self-recovery.md, Governance",
        # Each hold the watch writes is a `watch.held` row on its objective,
        # not a pipeline verdict row.
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
