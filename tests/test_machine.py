"""The state machine as a pure fold: the happy path, a question and its
answer, the join table row by row, the loops, a stale candidate, stop,
merge, the merge predicate, and, by property, that every prefix of any
ledger folds to exactly one state.

No database, no model call. Live spend: none.
"""

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from core import machine
from core.machine import Check, State

pytestmark = pytest.mark.spend(usd=0)


class Ledger:
    """A task's rows under construction, in the shapes the kernel writes."""

    def __init__(self, critique_rounds: int = 0, review_rounds: int = 1, judge: str | None = "precise"):
        self.rows: list[dict] = []
        self.turns = 0
        self.critique_rounds, self.review_rounds = critique_rounds, review_rounds
        self.plan_digest = None
        self.candidate = None
        self.add("task.started", {"sdlc": 1, "instruction": "x"})
        if judge:
            self.add("judge.decided", {"verdict": judge, "leg": "manual"})

    def add(self, type_: str, payload: dict) -> Ledger:
        self.rows.append({"id": len(self.rows) + 1, "type": type_, "payload": payload})
        return self

    @property
    def state(self) -> State:
        return machine.fold(self.rows).state

    def fold(self) -> machine.Fold:
        return machine.fold(self.rows)

    def turn(self, verdict: str, **extra) -> Ledger:
        self.turns += 1
        tid = f"t{self.turns}"
        state = self.state.value
        self.add("turn.started", {"turn_id": tid, "state": state})
        self.add("turn.ended", {"turn_id": tid, "outcome": "done", "result": {"session_id": "s"}})
        self.add("turn.collected", {"turn_id": tid, "state": state, "verdict": verdict, **extra})
        return self

    def ask(self) -> Ledger:
        state = self.state.value
        self.turn("asked")
        return self.add("question.asked", {"question_id": f"q{self.turns}", "turn_id": f"t{self.turns}",
                                           "text": "?", "state": state})  # fmt: skip

    def answer(self) -> Ledger:
        return self.add("question.answered", {"question_id": self.fold().open_question, "text": "a"})

    def plan(self, critique_rounds: int | None = None, review_rounds: int | None = None, **extra) -> Ledger:
        self.turn("planned")
        self.plan_digest = f"plan{self.turns}"
        return self.add(
            "plan.written",
            {
                "turn_id": f"t{self.turns}",
                "path": "p.md",
                "commit": "c",
                "sha256": self.plan_digest,
                "critique_rounds": self.critique_rounds if critique_rounds is None else critique_rounds,
                "review_rounds": self.review_rounds if review_rounds is None else review_rounds,
                **extra,
            },
        )

    def critique(self, verdict: str, digest: str | None = None, **raised) -> Ledger:
        return self.add(
            "critique.decided",
            {"plan_sha256": digest or self.plan_digest, "verdict": verdict, "raised": raised,
             "findings": [{"kind": "premise", "text": f"critique {verdict}"}]},
        )  # fmt: skip

    def build(self, sha: str | None = None) -> Ledger:
        self.turn(
            "candidate", candidate={"sha": sha or f"sha{self.turns + 1}", "turn_id": f"t{self.turns + 1}"}
        )
        self.candidate = self.rows[-1]["payload"]["candidate"]
        return self

    def check(self, check: str, verdict: str, candidate: dict | None = None, **extra) -> Ledger:
        payload = {"candidate": candidate or self.candidate, "verdict": verdict,
                   "findings": [{"kind": check, "text": f"{check} said {verdict}"}], **extra}  # fmt: skip
        return self.add(f"{check}.decided", payload)

    def checks(self, test: str, review: str, docs: str) -> Ledger:
        self.check("test", test).check("review", review)
        return self.check("docs", docs, head=self.candidate["sha"])

    def to_checks(self) -> Ledger:
        return self.plan().critique("sound").build()


def test_the_happy_path_from_judge_to_merged():
    led = Ledger()
    assert led.state is State.PLAN
    led.plan()
    assert led.state is State.CRITIQUE
    led.critique("sound")
    assert led.state is State.BUILD
    led.build()
    assert led.state is State.CHECKS
    led.checks("pass", "pass", "no_change")
    f = led.fold()
    assert f.state is State.MERGE and f.join.row == 1 and f.join.outcome == "passed"
    led.add(
        "effect.held", {"effect_id": "e1", "action_type": "merge", "payload": {"candidate": led.candidate}}
    )
    led.add("effect.intent", {"effect_id": "e1"})
    assert led.fold().merge_effect["state"] == "in_flight"
    led.add("effect.outcome", {"effect_id": "e1", "kind": "done"})
    assert led.state is State.MERGED


def test_a_thin_request_clarifies_and_an_answer_returns_to_the_state_that_asked():
    led = Ledger(judge="thin")
    assert led.state is State.CLARIFY
    led.ask()
    f = led.fold()
    assert f.state is State.WAITING and f.return_to is State.CLARIFY
    led.answer()
    assert led.state is State.CLARIFY
    led.turn("no_material_question")
    assert led.state is State.PLAN


@pytest.mark.parametrize("where", ["plan", "build", "patch"])
def test_answered_returns_to_plan_build_and_patch_too(where):
    led = Ledger()
    if where in ("build", "patch"):
        led.plan().critique("sound")
    if where == "patch":
        led.build().checks("pass", "changes", "no_change")
    assert led.state.value == where
    led.ask().answer()
    f = led.fold()
    assert f.state.value == where and f.entry["type"] == "question.answered"


# -- the join table ----------------------------------------------------------------

# (review rounds in the plan, what is already spent, test, review, docs) -> row, goes to, outcome
JOIN_ROWS = [
    (1, None, "pass", "pass", "updated", 1, State.MERGE, "passed"),
    (1, None, "pass", "pass", "no_change", 1, State.MERGE, "passed"),
    (1, None, "red", "changes", "changes", 3, State.PATCH, None),
    (1, None, "red", "pass", "no_change", 3, State.PATCH, None),
    (1, None, "gaps", "pass", "no_change", 3, State.PATCH, None),
    (1, None, "pass", "pass", "changes", 3, State.PATCH, None),
    (1, "rounds", "gaps", "pass", "updated", 4, State.MERGE, "gaps"),
    (0, None, "gaps", "pass", "no_change", 4, State.MERGE, "gaps"),
    (0, None, "pass", "changes", "no_change", 5, State.PATCH, None),
    (1, "rounds", "red", "pass", "no_change", 5, State.PATCH, None),
    (1, "rounds", "gaps", "pass", "changes", 5, State.PATCH, None),
    (0, "last", "pass", "changes", "no_change", 6, State.ENDED, "did_not_pass"),
    (0, "last", "red", "pass", "no_change", 6, State.ENDED, "did_not_pass"),
    (1, "last", "pass", "pass", "changes", 6, State.ENDED, "did_not_pass"),
]


@pytest.mark.parametrize(("rounds", "spent", "test", "review", "docs", "row", "goes", "outcome"), JOIN_ROWS)
def test_every_row_of_the_join_table(rounds, spent, test, review, docs, row, goes, outcome):
    led = Ledger(review_rounds=rounds).to_checks()
    if spent:
        for _ in range(rounds):
            led.checks("red", "pass", "no_change").build()
        assert led.fold().counts["review_rounds"] == rounds
    if spent == "last":
        led.checks("red", "pass", "no_change")
        assert led.fold().join.spends == "last"
        led.build()
    led.checks(test, review, docs)
    f = led.fold()
    assert (f.join.row, f.state, f.join.outcome) == (row, goes, outcome)
    assert len(f.join.findings) == 3  # every finding of the three branches together


# -- loops ---------------------------------------------------------------------------


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_critique_rounds_bound_the_returns_to_plan_and_the_last_findings_ride_into_build(rounds):
    led = Ledger(critique_rounds=rounds).plan()
    returns = 0
    for _ in range(4):
        led.critique("revise")
        if led.state is State.PLAN:
            returns += 1
            led.plan()
        else:
            break
    f = led.fold()
    assert returns == rounds and f.state is State.BUILD
    assert f.entry["type"] == "critique.decided" and f.entry["payload"]["verdict"] == "revise"


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_the_plans_rounds_then_one_last_round_bound_the_patches_and_the_task_ends(rounds):
    led = Ledger(review_rounds=rounds).to_checks()
    patches = 0
    while True:
        led.checks("pass", "changes", "no_change")
        if led.state is State.PATCH:
            patches += 1
            led.build()
        else:
            break
    f = led.fold()
    # A plan's 2 is read as 1: review sends work back at most once, then
    # the one last round.
    assert patches == min(rounds, machine.REVIEW_MAX) + machine.LAST_ROUNDS
    assert f.state is State.ENDED and f.join.row == 6 and f.join.outcome == "did_not_pass"


def test_valor_stopping_in_a_patch_ends_the_task_and_task_ended_never_ends_a_merged_one():
    led = Ledger(review_rounds=0).to_checks()
    led.checks("pass", "changes", "no_change")
    led.turn("ended")
    assert led.state is State.PATCH  # the verdict moves on its own row
    led.add("task.ended", {"kind": "decided", "by": "valor", "text": "not worth it"})
    f = led.fold()
    assert f.state is State.ENDED and f.entry["type"] == "task.ended"
    merged = Ledger().to_checks().checks("pass", "pass", "no_change")
    merged.add("effect.intent", {"effect_id": "e", "action_type": "merge",
                                 "payload": {"candidate": merged.candidate}})  # fmt: skip
    merged.add("effect.outcome", {"effect_id": "e", "kind": "done"})
    merged.add("task.ended", {"kind": "step", "by": "kernel"})
    assert merged.state is State.MERGED and "task ended in merged" in merged.fold().ignored[-1]["why"]


# -- candidates and staleness -------------------------------------------------------------


def test_a_stale_candidate_verdict_is_ignored():
    led = Ledger(review_rounds=1).to_checks()
    first = led.candidate
    led.checks("pass", "changes", "no_change").build()
    led.check("review", "pass", candidate=first)
    f = led.fold()
    assert Check.REVIEW not in f.checks and "stale" in f.ignored[-1]["why"]


# -- stop -----------------------------------------------------------------------------


def _reach(state: State) -> Ledger:
    led = Ledger(judge=None if state is State.JUDGE else ("thin" if state is State.CLARIFY else "precise"))
    if state in (State.JUDGE, State.CLARIFY, State.PLAN):
        return led
    if state is State.WAITING:
        return led.ask()
    led.plan()
    if state is State.CRITIQUE:
        return led
    led.critique("sound")
    if state is State.BUILD:
        return led
    led.build()
    if state is State.CHECKS:
        return led
    if state is State.PATCH:
        return led.checks("pass", "changes", "no_change")
    led.checks("pass", "pass", "no_change")
    if state is State.MERGED:
        led.add(
            "effect.held", {"effect_id": "e", "action_type": "merge", "payload": {"candidate": led.candidate}}
        )
        led.add("effect.outcome", {"effect_id": "e", "kind": "done"})
    if state is State.ENDED:
        led.add("task.ended", {"kind": "merge", "by": "kernel", "reason": "x"})
    if state is State.STOPPED:
        led.add("task.stopped", {"reason": "x"})
    return led


@pytest.mark.parametrize("state", list(State))
def test_every_state_is_reachable_and_a_stop_in_it_is_final(state):
    led = _reach(state)
    assert led.state is state
    led.add("task.stopped", {"reason": "stop"})
    led.add("feedback.given", {"feedback_id": "f", "text": "after the stop"})
    led.add("question.answered", {"question_id": "q1", "text": "after the stop"})
    assert led.state is State.STOPPED


# -- totality, by property ----------------------------------------------------------------

CANDIDATES = [{"sha": "a", "turn_id": "t1"}, {"sha": "a", "turn_id": "t2"}, {"sha": "b", "turn_id": "t3"}]
STATES = [s.value for s in State] + ["nonsense", None]
VERDICT_VALUES = sorted({v for vs in machine.VERDICTS.values() for v in vs}) + ["bogus", None]

payloads = st.fixed_dictionaries(
    {},
    optional={
        "verdict": st.sampled_from(VERDICT_VALUES),
        "state": st.sampled_from(STATES),
        "candidate": st.sampled_from([*CANDIDATES, None, "x", {}]),
        "turn_id": st.sampled_from(["t1", "t2", "t3"]),
        "question_id": st.sampled_from(["q1", "q2", None]),
        "plan_sha256": st.sampled_from(["p1", "p2", None]),
        "sha256": st.sampled_from(["p1", "p2"]),
        "critique_rounds": st.sampled_from([0, 1, 2, 3, "x"]),
        "review_rounds": st.sampled_from([0, 1, 2, 3, None]),
        "raised": st.sampled_from([{}, {"critique_rounds": 2}, {"review_rounds": "x"}, None]),
        "instance_id": st.sampled_from(["i1", "i2"]),
        "from": st.sampled_from([{**c, "head": c["sha"]} for c in CANDIDATES] + ["x", {}]),
        "to": st.sampled_from([{**c, "head": "h"} for c in CANDIDATES] + [{"sha": "s"}]),
        "governance": st.sampled_from(
            [None, {"adds": True, "instances": [{"id": "i1"}]}, {"instances": "x"}]
        ),
        "effect_id": st.sampled_from(["e1", "e2"]),
        "action_type": st.sampled_from(["merge", "push_branch"]),
        "kind": st.sampled_from(["done", "failed"]),
        "outcome": st.sampled_from(["done", "failed", "stopped"]),
        "payload": st.sampled_from([{"candidate": c} for c in CANDIDATES] + [{}]),
        "result": st.sampled_from([{}, {"session_id": "s"}, None]),
        "sdlc": st.sampled_from([1, None]),
    },
)
TYPES = [
    "task.started", "task.stopped", "judge.decided", "turn.started", "turn.ended", "turn.collected",
    "question.asked", "question.answered", "plan.written", "critique.decided", "test.decided",
    "review.decided", "docs.decided", "task.delivered", "feedback.given", "guard.granted",
    "task.ended", "rebase.requested", "verdicts.carried", "effect.held", "effect.intent", "effect.outcome", "effect.refused", "something.else",
]  # fmt: skip
MISSING = object()  # a row with no payload key at all
RAISED = st.sampled_from(
    [
        {},
        None,
        "x",
        {"critique_rounds": 2},
        {"review_rounds": "x"},
        {"critique_rounds": 2, "review_rounds": "x"},
        {"critique_rounds": 1, "review_rounds": 3},
        {"review_rounds": True},
    ]
)
payloads = payloads.flatmap(lambda p: st.builds(lambda r: {**p, "raised": r} if "raised" in p else p, RAISED))
rows_strategy = st.lists(
    st.tuples(st.sampled_from(TYPES), st.one_of(payloads, st.sampled_from([None, "text", 3, MISSING]))),
    max_size=40,
)
STARTS = st.sampled_from(
    [
        {"sdlc": 1, "instruction": "x"},
        {"instruction": "x"},
        None,
        MISSING,
        "text",
        {"sdlc": "1"},
        {"calibration": "intake.underspecified"},
        {"calibration": "intake.underspecified", "sdlc": 1},
    ]
)


def _row(i: int, kind: str, payload) -> dict:
    row = {"id": i, "type": kind}
    if payload is not MISSING:
        row["payload"] = payload
    return row


def _material(f: machine.Fold) -> tuple:
    return (
        f.state, f.return_to, dict(f.raised), dict(f.counts), f.candidate, tuple(sorted(f.checks)),
        frozenset(f.granted), f.plan and f.plan.get("sha256"), f.join and f.join.row,
    )  # fmt: skip


@settings(max_examples=500, deadline=None)
@given(STARTS, rows_strategy)
# A question.asked with no question_id, in a working state: ignored, it
# changes nothing.
@example(
    {"sdlc": 1, "instruction": "x"},
    [("judge.decided", {"verdict": "precise", "leg": "manual"}), ("question.asked", {})],
)
def test_every_prefix_folds_to_exactly_one_state(start, generated):
    rows = [_row(1, "task.started", start)]
    rows += [_row(i + 2, t, p) for i, (t, p) in enumerate(generated)]
    stopped = False
    previous = None
    for n in range(len(rows) + 1):
        f = machine.fold(rows[:n])
        assert isinstance(f.state, State)
        if n >= 1 and isinstance(start, dict) and start.get("calibration"):
            # a calibration task: no row applies, whatever follows its start
            assert f.calibration and not f.legacy and f.state is State.JUDGE and f.candidate is None
            continue
        if stopped:
            assert f.state is State.STOPPED  # absorbing
        stopped = stopped or f.state is State.STOPPED
        if f.candidate is not None:
            assert all(v.candidate == f.candidate for v in f.checks.values())
        assert f.counts["review_rounds"] <= f.loops.review_rounds
        assert f.counts["last_rounds"] <= machine.LAST_ROUNDS
        # A row the fold ignored changed nothing that decides anything.
        if previous is not None and not f.legacy and f.ignored and f.ignored[-1]["id"] == n:
            assert _material(f) == _material(previous)
        previous = f


def test_a_failed_merge_outcome_stays_in_merge_and_a_merge_for_another_candidate_is_ignored():
    led = Ledger().to_checks().checks("pass", "pass", "no_change")
    led.add(
        "effect.held", {"effect_id": "e1", "action_type": "merge", "payload": {"candidate": led.candidate}}
    )
    led.add("effect.intent", {"effect_id": "e1"})
    led.add("effect.outcome", {"effect_id": "e1", "kind": "failed"})
    f = led.fold()
    assert f.state is State.MERGE and f.merge_effect["state"] == "failed"
    stale = {"sha": "old", "turn_id": "t0"}
    led.add("effect.held", {"effect_id": "e2", "action_type": "merge", "payload": {"candidate": stale}})
    f = led.fold()
    assert f.merge_effect["effect_id"] == "e1" and "not the current one" in f.ignored[-1]["why"]


def test_the_merge_predicate_holds_only_for_the_current_candidate_with_test_and_review_passed():
    led = Ledger().to_checks().checks("pass", "pass", "no_change")
    f = led.fold()
    payload = {"candidate": led.candidate, "head_sha": led.candidate["sha"]}
    assert machine.merge_predicate(f, payload) == []
    stale = {**payload, "candidate": {"sha": "old", "turn_id": "t0"}}
    assert [t[0] for t in machine.merge_predicate(f, stale)] == ["1"]
    red = Ledger(review_rounds=0).to_checks().checks("red", "changes", "no_change")
    f = red.fold()
    f.state = State.MERGE  # as if it had got there: the predicate still refuses it
    payload = {"candidate": red.candidate, "head_sha": red.candidate["sha"]}
    assert [t[0] for t in machine.merge_predicate(f, payload)] == ["2", "3"]
