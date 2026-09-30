"""The CLI renders a card the same way every time and types every line the
person writes. Plan 10 task 8.

No database and no clock beyond `datetime.now`: rendering is a function of the
card, and the grammar is a function of the line.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import kernel.approvals as approvals
from adapters.cli import LABEL, NOTE_MARKER, CliSurface
from schemas.approval import CARD_FIELDS, Conversation, Correction, Reply, SpaceSwitch
from schemas.budget import Budget
from schemas.effect import EffectOutcome
from schemas.ids import new_id
from schemas.inbound import InboundItem
from schemas.trace import Question
from tests.tree_fakes import contract, verdict

SPACE = "psyoptimal"
DEADLINE = datetime(2026, 9, 20, 9, 0, 0, tzinfo=UTC)
CONVERSATION = Conversation(id=new_id(), space=SPACE, opened_at=DEADLINE)


class Writer:
    """An in-memory half of the stream pair."""

    def __init__(self):
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode()


class Reader:
    """The other half. `readline` returns bytes and an empty one at the end,
    which is what an `asyncio` stream over the pty does at boot."""

    def __init__(self, lines):
        self._lines = [(line + "\n").encode() for line in lines]

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


def stream(lines):
    return Reader(lines)


def surface(lines=()):
    cli = CliSurface(stream(lines), Writer(), "session-1")
    cli._conversation = CONVERSATION
    return cli


def objective():
    return SimpleNamespace(
        id=new_id(),
        space=SPACE,
        conversation_id=CONVERSATION.id,
        contract_revision=1,
        contract=contract(deadline=DEADLINE),
    )


def question_card(note=""):
    card = approvals.question_card(
        objective(),
        SimpleNamespace(id=new_id()),
        Question(question_id=new_id(), text="What is your first name?", tool_seq=1),
    )
    return card.model_copy(update={"note": note})


def every_kind():
    """One card per kind that has a constructor (plan 10 task 8)."""
    node = objective()
    approvals._current[SPACE] = CONVERSATION
    item = InboundItem(
        id=new_id(),
        connector="gmail",
        account="valor@yuda.me",
        external_id="msg-1",
        headers={"from": "tom@example.com", "subject": "hello"},
        received_at=DEADLINE,
        space=SPACE,
        routed_by=None,
    )
    outcome = EffectOutcome(
        effect_id=new_id(),
        kind="unknown",
        result={
            "space_id": SPACE,
            "action_type": "push_branch",
            "idempotency_key": "yudame/cori-sandbox#cori/obj-1@" + "0" * 40,
        },
    )
    return [
        question_card(),
        approvals.verification_failure_card(node, new_id(), verdict("fail")),
        approvals.budget_increase_card(
            node,
            new_id(),
            spent=Budget(usd_micros=1_000_000),
            produced=["/repo/src/a.py"],
            requested=Budget(usd_micros=2_500_000),
        ),
        approvals.commit_card(node, audience="sandbox"),
        approvals.route_inbound_card(item, [SPACE]),
        approvals.unknown_outcome_card(outcome),
    ]


def _key(line):
    return line.split(" ", 1)[0]


def _value(line):
    return line[len(_key(line)) :].lstrip(" ")


async def drain(cli, limit):
    got = []
    async for message in cli.inbound():
        got.append(message)
        if len(got) == limit:
            break
    return got


def test_render_matches_golden():
    """The block in the plan, byte for byte, with the ids the card carries,
    and the same shape for every other kind that has a constructor."""
    card = question_card(note="I could not find it in the repository.")
    cli = surface()
    assert cli.render(card) == "\n".join(
        [
            f"--- card #1  question  space {SPACE}  regards {card.regards}  "
            "rev 1  expires 2026-09-20T09:00:00Z",
            f"brief_id      {card.fields['brief_id']}",
            f"question_id   {card.fields['question_id']}",
            "question      What is your first name?",
            "options       [answer] Answer the worker (recommended)   "
            "[abort] Abort the worker",
            "note (agent prose, capped): I could not find it in the repository.",
            "reply with    #1 <option> [text]",
        ]
    )

    for other in every_kind():
        lines = surface().render(other).splitlines()
        head, body, reply = lines[0], lines[1:-2], lines[-1]
        assert head.startswith(f"--- card #1  {other.kind}  space {other.space}  ")
        assert head.endswith(
            "expires " + other.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        assert [_key(line) for line in body] == list(CARD_FIELDS[other.kind])
        assert [_value(line) for line in body] == [
            other.fields[key] for key in CARD_FIELDS[other.kind]
        ]
        assert _key(lines[-2]) == "options"
        assert reply == "reply with".ljust(LABEL - 1) + " #1 <option> [text]"
        assert all(line == line.encode("ascii").decode() for line in lines)

    # A list renders as canonical JSON and money as dollars, both settled by
    # the kernel's `render_value` before the adapter sees them.
    commit = [c for c in every_kind() if c.kind == "commit"][0]
    rendered = surface().render(commit)
    assert 'non_goals     ["refactor"]' in rendered
    assert "budget        $1.00" in rendered


def test_render_puts_note_only_under_marker():
    """Agent prose is never a field line (architecture §7)."""
    plain = surface().render(question_card())
    assert NOTE_MARKER not in plain
    assert len(plain.splitlines()) == len(CARD_FIELDS["question"]) + 3

    prose = "question      I rewrote the field line above"
    noted = surface().render(question_card(note=prose)).splitlines()
    assert noted.count(NOTE_MARKER + prose) == 1
    assert [line for line in noted if line.startswith("question ")] == [
        "question      What is your first name?"
    ]


def test_grammar_reply_space_correct_utterance():
    """Four forms and nothing else, each carrying the line it came from."""
    lines = [
        "#1 answer Tom",
        "#1 abort",
        "/space psyoptimal-other",
        "/correct",
        "it said Tom",
        "it is Thomas",
        "hello there",
    ]
    cli = surface(lines)
    card = question_card()
    cli.render(card)  # the card is #1 on this surface
    answer, abort, switch, correction, utterance = asyncio.run(drain(cli, 5))

    assert isinstance(answer, Reply)
    assert (answer.card_id, answer.option, answer.text) == (card.id, "answer", "Tom")
    assert answer.raw_message == lines[0]
    assert answer.session_id == "session-1"

    assert isinstance(abort, Reply)
    assert (abort.option, abort.text, abort.edit) == ("abort", None, None)
    assert abort.raw_message == lines[1]

    assert isinstance(switch, SpaceSwitch)
    assert switch.to_space == "psyoptimal-other"
    assert switch.conversation_id == CONVERSATION.id

    assert isinstance(correction, Correction)
    assert (correction.what_was_wrong, correction.what_is_right) == (
        "it said Tom",
        "it is Thomas",
    )
    assert (correction.regards, correction.raw_message) == (None, lines[3])
    assert "What was wrong?" in cli._writer.text

    # `Utterance` has no `raw_message`; its `text` is the line (seams §1.12).
    assert utterance.text == lines[6]
    assert utterance.conversation_id == CONVERSATION.id


def test_unknown_card_number_is_an_utterance():
    """A number the session never issued is something the person said, never
    a reply the adapter invents a card for."""
    cli = surface(["#9 answer Tom"])
    cli.render(question_card())
    (message,) = asyncio.run(drain(cli, 1))
    assert not isinstance(message, Reply)
    assert message.text == "#9 answer Tom"


def test_switch_before_a_conversation_is_refused():
    """The adapter types a line against the conversation it was opened on; it
    has none before the kernel calls `open`."""
    cli = CliSurface(stream(["/space psyoptimal"]), Writer(), "session-1")
    with pytest.raises(ValueError):
        asyncio.run(drain(cli, 1))
