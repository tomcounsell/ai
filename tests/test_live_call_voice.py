"""The call's one mind and voice, live, without Meet (P-042): each scenario
is a real `call.Call` on the test database, its page a fake one, its mind
and voice Gemini itself (`settings.call_mind_model`, `call_voice_model`),
given the call so far as lines and asked for a turn as the transcriber's
end of turn would ask it (`Call._answer`). What the turn said, the tools it
called and the seconds from the ask to its first sound are read back. The
scenarios are made up; what they test is from Tom's calls (each test
names it). Hearing and turn-taking are `test_live_ears.py`'s and, through
Meet, `test_live_meet.py`'s.

Runs only when `VALOR_LIVE=1`, with the Gemini key copied (`python -m core
google-key`)."""

import asyncio
import os
import re
import statistics
import time

import pytest

from core import call, credentials
from core.settings import settings
from tests import test_meet
from tests.test_call import LOGAN, TOMS, Page, a_call_of

pytestmark = [
    pytest.mark.spend(usd=0.10),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live Gemini calls: VALOR_LIVE=1"),
]
team = test_meet.team
TOM = test_meet.TOM
ANA, BEN = "devices/7", "devices/8"
DELAYS: list[float] = []  # each reply's seconds to first sound, for the report


@pytest.fixture(scope="module", autouse=True)
def report_delays():
    yield
    if DELAYS:
        d = sorted(DELAYS)
        print(f"\nmind + voice, end of turn to first sound: {len(d)} replies, median {statistics.median(d):.2f} s, "
              f"p90 {d[int(0.9 * (len(d) - 1))]:.2f} s, max {d[-1]:.2f} s")  # fmt: skip


def line(name: str, text: str, how: str = "spoke") -> str:
    return f"[00:00] {name} ({how}): {text}"


class Meeting:
    """A live call's state and lines, and one turn of the mind at a time."""

    def __init__(self, the: call.Call, page: Page):
        self.the, self.page = the, page

    async def say(self, who: str, text: str, *, how: str = "spoke") -> dict:
        """`who` said `text`; Valor's turn on it, as `_turn_end` asks it."""
        name = self.the.names[who]
        await self.the._heard(who, name, text, how)
        alone = len(self.the.present) == 1
        now = (call.NOW_TYPED if how == "typed" else call.NOW_SPOKEN if alone else call.NOW_GROUP).format(
            name=name
        )
        return await self.turn(now, None if how == "typed" else who, typed=how == "typed")

    async def turn(self, now: str, who: str | None, *, typed: bool = False) -> dict:
        tools: list[tuple[str, dict]] = []
        real = self.the._tool

        async def tool(t, name, args):
            tools.append((name, dict(args)))
            return await real(t, name, args)

        self.the._tool = tool
        first: list[float] = []
        self.page.on_stream = lambda: first.append(time.monotonic()) if not first else None
        began = time.monotonic()
        self.the._answer(now, who, typed=typed)
        t = self.the.turn
        await t.task
        while not t.done():
            await asyncio.sleep(0.05)
        self.the._tool = real
        await self.the.posts.join()
        said = t.reply.text
        if typed:
            said = (
                self.the.lines[-1].partition("(typed): ")[2] if "Valor (typed)" in self.the.lines[-1] else ""
            )
        delay = first[0] - began if first else None
        if delay is not None:
            DELAYS.append(delay)
        print(f"\n{now[:40]!r} -> said {said!r} tools {[n for n, _ in tools]} first sound {delay}")
        return {"said": said, "tools": tools, "delay": delay}


def meeting(dsn, team, *, here=(TOMS,), topics="", decided=(), told=(), lines=(), open_="", prep=""):
    """A call with `here` in it (Tom, or Tom with others), its state, kept
    notes and lines so far, and `open_` as the open decisions."""
    names = {TOMS: TOM, LOGAN: "Logan", ANA: "Ana Ruiz", BEN: "Ben Ito"}

    async def go(scenario):
        page = Page()
        key = credentials.read_key(settings.google_keyfile, call.GOOGLE_KEY, "google-key")
        _, the = await a_call_of(dsn, team, page, call.Gemini(key))
        if prep:
            the.info["prep"] = prep
        the.present, the.names = set(here), {w: names[w] for w in here}
        the.topics, the.told, the.lines = topics, list(told), list(lines)
        the.decided = [{"decision": d, "by": [TOM], "at": "00:04", "settles": []} for d in decided]
        the.instruction = call.system("acme")
        the.posts_task = asyncio.create_task(the.posts_lane())
        if open_:
            orig = call.decisions

            async def decisions(conn, project):
                return open_

            call.decisions = decisions
        try:
            return await scenario(Meeting(the, page))
        finally:
            the.posts_task.cancel()
            if open_:
                call.decisions = orig

    return go


def asked_about(said: str, pattern: str) -> list[str]:
    return [
        x
        for x in re.split(r"(?<=[.?!])\s+", said)
        if x.endswith("?") and re.search(pattern, x, re.IGNORECASE)
    ]


# P-041's acceptance, on the one mind. A made-up project.
TOPICS = (
    "- Support rota: open; who covers launch week.\n- Pricing page copy: open; the draft says launch Monday.\n"
    "- Launch date: open; recommend Monday the 1st."
)
DECIDE = "Valor, on the launch date: we go Thursday the 4th, not Monday. That's decided."
ASK = "Valor, what did we decide on the launch date, and does it change anything else?"


def test_topics_out_of_order_a_decision_is_recorded_before_the_reply_kept_and_changes_what_it_touches(
    dsn, team
):
    """P-041's acceptance (Tom, 2026-11-25: "I'd rather the interaction be
    slow than have to repeat myself"; the client call of that day asked a
    decided item three times): three topics raised out of order; a decision
    on one that changes another, recorded with `settle` before Valor speaks
    of it; then a state written before the decision arrives (the stale
    topics under the decision). Asked what is next and what was decided,
    Valor never asks the decided topic again, states it, and says the
    topic it touches changed."""

    async def scenario(m):
        out = [await m.say(TOMS, "Let's take the support rota first.")]
        out.append(await m.say(TOMS, "Hold that. Where is the pricing page copy?"))
        out.append(await m.say(TOMS, DECIDE))
        m.the.topics = TOPICS  # the worker's update, written before the decision, lands now
        out.append(await m.say(TOMS, "Okay. What's next?"))
        out.append(await m.say(TOMS, ASK))
        return m.the, out

    for _ in range(3):
        the, (_, _, decided, nexts, asked) = asyncio.run(meeting(dsn, team, topics=TOPICS)(scenario))
        settles = [a for n, a in decided["tools"] if n == "settle"]
        assert settles and re.search(r"thursday", settles[0]["readback"], re.IGNORECASE), decided
        assert [d["decision"] for d in the.decided] and re.search(
            r"thursday", the.decided[0]["decision"], re.IGNORECASE
        )
        for seen in (nexts, asked):
            assert not asked_about(seen["said"], r"launch|monday|thursday"), seen
            again = [a["readback"] for n, a in seen["tools"] if n == "settle"]  # not settled again
            assert not [r for r in again if not re.search(r"pricing|copy", r, re.IGNORECASE)], seen
        assert re.search(r"thursday", asked["said"], re.IGNORECASE) and re.search(
            r"pricing|copy", asked["said"], re.IGNORECASE
        ), asked


def test_a_fact_the_mind_does_not_hold_goes_to_the_worker_on_the_first_turn(dsn, team):
    """The 2026-11-16 call: asked what an error meant, Valor explained it
    five times in other words before asking the worker; the 2026-11-13 call:
    asked for the criteria behind an order, it restated the order."""
    prep = ("Next on the backlog: a runtime error in Sentry, raised when the system applies a difference while "
            "no difference is active. Medium value, small appetite.")  # fmt: skip

    async def scenario(m):
        return await m.say(TOMS, "Tell me more about that error. Is it a real failure, or a no-op?")

    for _ in range(3):
        seen = asyncio.run(meeting(dsn, team, prep=prep)(scenario))
        assert "ask" in [n for n, _ in seen["tools"]], seen


def test_asked_again_after_a_vague_answer_the_mind_asks_the_worker(dsn, team):
    lines = [line(TOM, "Tell me about the import error."),
             line("Valor", "It's an error in the logic that applies changes when the state isn't what the system "
                           "expects.")]  # fmt: skip

    async def scenario(m):
        return await m.say(TOMS, "I'm so confused. What does that error actually mean?")

    for _ in range(3):
        assert "ask" in [n for n, _ in asyncio.run(meeting(dsn, team, lines=lines)(scenario))["tools"]]


ITEMS = (
    "1. The import, Monday: QA passed 212 of 214 cases on 2026-11-16; the two failures are dates written "
    "day-first, fixed in the parser, retested 2026-11-17. Follow-up, a level down: day-first dates fail "
    "because the parser guessed month-first when both numbers are 12 or less; it now reads the account's "
    "locale. Follow-up, a level further: 31 accounts use a day-first locale, 4 of them with over 10,000 rows.\n"
    "2. Onboarding emails: open; from the product address, 38 percent opened in the September trial; from "
    "Tom's, 61 percent. Recommendation: Tom's address for the first email only."
)
ITEM_ONE = [line(TOM, "Let's do the import first. Where is it?"),
            line("Valor", "The import is ready to ship Monday: QA passed, and the two failures are fixed. I "
                          "recommend we ship it Monday.")]  # fmt: skip


@pytest.mark.parametrize("confirm", ["Yes, that's settled. Ship it Monday.", "Okay, sounds good."])
def test_in_a_one_to_one_a_settled_item_opens_the_next(dsn, team, confirm):
    """Tom, 2026-11-17: in a 1:1 he had to ask "what's next?" once an item
    was settled (2026-11-16). Settled by saying so or by agreeing, Valor's
    reply opens item two from what only the state holds (the open rates),
    and never hands the pick back."""

    async def scenario(m):
        return await m.say(TOMS, confirm)

    for _ in range(2):
        said = asyncio.run(meeting(dsn, team, topics=ITEMS, lines=ITEM_ONE)(scenario))["said"].lower()
        assert "email" in said or "onboarding" in said, said
        assert not re.search(r"(which|what) (item|priority|one)[^.?]* next", said), said


def test_in_a_group_call_a_settled_item_waits_to_be_asked(dsn, team):
    async def scenario(m):
        return await m.say(TOMS, "Valor, yes, that's settled. Ship it Monday.")

    for _ in range(3):
        seen = asyncio.run(meeting(dsn, team, here=(TOMS, ANA, BEN), topics=ITEMS, lines=ITEM_ONE)(scenario))
        assert "email" not in seen["said"].lower() and "onboarding" not in seen["said"].lower(), seen


def test_asked_for_more_twice_each_answer_adds_what_the_last_lacked(dsn, team):
    """Tom, 2026-11-17: "I ask for more details and you simply repeat
    yourself"."""
    words = lambda text: {w for w in re.findall(r"[a-z0-9][a-z0-9'-]{4,}", text.lower())}

    async def scenario(m):
        return await m.say(TOMS, "Tell me more about that."), await m.say(TOMS, "Can you go deeper on that?")

    first, second = asyncio.run(meeting(dsn, team, topics=ITEMS, lines=ITEM_ONE)(scenario))
    opening = words(ITEM_ONE[1])
    one, two = words(first["said"]), words(second["said"])
    assert len(one - opening) >= 3 and len(two - opening - one) >= 3, (one, two)
    assert len(two & one) / max(1, len(two)) < 0.6, (one, two)


def test_in_a_group_talk_between_the_others_gets_no_word(dsn, team):
    """2026-11-05: side talk was answered. Put to the mind as a named turn
    would be, talk between the others is left alone."""

    async def scenario(m):
        await m.the._heard(ANA, "Ana Ruiz", "Ben, did you see the landing page numbers?", "spoke")
        return await m.say(BEN, "Yes, better than October. Valor mentioned the import too, right Ana?")

    for _ in range(3):
        seen = asyncio.run(meeting(dsn, team, here=(TOMS, ANA, BEN))(scenario))
        assert not seen["said"].strip() and not seen["tools"], seen


def test_an_answer_asked_aloud_opens_the_next_reply(dsn, team):
    """The answer to a lookup asked aloud starts no turn of its own
    (2026-11-19: its own turn spoke over Tom); the next reply gives it, so
    it is not said late or never (2026-11-18: 11 minutes late)."""
    lines = [line(TOM, "How many signups did we have last week? Look it up."),
             line("Valor", "I'll check that with my worker. Meanwhile, the launch is Monday.")]  # fmt: skip

    async def scenario(m):
        m.the.say_next = [("How many signups last week?", "There were 212 signups last week.")]
        return await m.say(TOMS, "Okay. Anything else?")

    for _ in range(3):
        said = asyncio.run(meeting(dsn, team, lines=lines)(scenario))["said"]
        assert "212" in said or "two hundred" in said.lower(), said


RENAME = (
    "Tom approved the rename on 2026-11-17: the Customer model becomes Account, with the names Account and "
    "account_id, everywhere. Four steps are built. Steps 1 to 3 carry that out as approved: step 1 renames the "
    "model, step 2 renames the API fields to account_id, step 3 renames the admin labels. Step 4 departs from "
    "it: to skip a slow migration, the builder kept the old database table name, customers, under the renamed "
    "model; that name is not on Tom's approved list, and keeping it means two names for one thing. "
    "Recommendation: keep the old table name for now."
)
RENAME_OPEN = "Open for Tom:\n- t7/q7: Keep the old table name, customers, under the renamed Account model?"
TABLE = re.compile(r"table|customers|old name", re.IGNORECASE)


def test_work_that_carries_out_a_decision_is_status_and_only_the_departure_is_asked_after_it_is_explained(
    dsn, team
):
    """P-040: the 2026-11-25 call asked a yes for steps that only carried
    out an approved plan, and asked before explaining."""

    async def scenario(m):
        return await m.say(TOMS, "Okay Valor, where are we on the rename? What do you need from me?")

    for _ in range(3):
        said = asyncio.run(meeting(dsn, team, topics=RENAME, open_=RENAME_OPEN)(scenario))["said"]
        assert TABLE.search(said), said
        sentences = [x.strip() for x in re.split(r"(?<=[.?!])\s+", said) if x.strip()]
        for i, x in enumerate(sentences):
            if x.endswith("?"):  # every question is about the departure, after it was explained
                assert TABLE.search(x) or any(TABLE.search(y) for y in sentences[max(0, i - 2) : i]), said
                assert any(TABLE.search(y) and not y.endswith("?") for y in sentences[:i]), said


def test_told_no_valor_changes_course_records_it_and_never_asks_it_again(dsn, team):
    """The 2026-11-25 call asked a rejected item three more times. Told no
    by Tom, Valor records it and says the new course; asked what is next,
    with the worker's topics still recommending the old name, it does not
    ask about it again."""
    lines = [line(TOM, "Where are we on the rename?"),
             line("Valor", "Steps 1 to 3 follow the rename you approved on 17 November. One thing departs from it: "
                           "step 4 kept the old table name, customers, under the Account model, so one thing would "
                           "have two names. I'd keep it for now. Do you want the old table name kept?")]  # fmt: skip

    async def scenario(m):
        no = await m.say(TOMS, "No. Rename the table too. I don't want two names for one thing.")
        nexts = await m.say(TOMS, "Okay. What else is there?")
        return m.the, no, nexts

    for _ in range(3):
        the, no, nexts = asyncio.run(
            meeting(dsn, team, topics=RENAME, lines=lines, open_=RENAME_OPEN)(scenario)
        )
        assert [n for n, _ in no["tools"] if n == "settle"] and the.decided, no
        assert not asked_about(no["said"], TABLE.pattern), no
        assert not asked_about(nexts["said"], TABLE.pattern), nexts
        assert not re.search(r"keep(ing)? (the )?(old )?(table )?name", nexts["said"], re.IGNORECASE), nexts


def test_a_typed_line_is_answered_in_writing(dsn, team):
    async def scenario(m):
        return await m.say(TOMS, "Valor, what's the launch date again?", how="typed")

    seen = asyncio.run(meeting(dsn, team, decided=["Launch on Thursday the 4th"])(scenario))
    assert seen["delay"] is None and re.search(r"thursday", seen["said"], re.IGNORECASE), seen
