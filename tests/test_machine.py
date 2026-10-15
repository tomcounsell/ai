"""The state machine as a pure fold: the join table row by row, the loops,
staleness, governance grants, legacy ledgers, and, by property, that every
prefix of any ledger folds to exactly one state.

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

    def check(self, check: str, verdict: str, candidate: dict | None = None, instances=(), **extra) -> Ledger:
        payload = {"candidate": candidate or self.candidate, "verdict": verdict,
                   "findings": [{"kind": check, "text": f"{check} said {verdict}"}], **extra}  # fmt: skip
        if instances:
            payload["governance"] = {
                "adds": True,
                "instances": [{"id": i, "path": "x.py"} for i in instances],
            }
        return self.add(f"{check}.decided", payload)

    def checks(self, test: str, review: str, docs: str, instances=()) -> Ledger:
        self.check("test", test).check("review", review, instances=instances)
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


def test_a_legacy_question_without_a_state_returns_to_the_state_it_was_asked_in():
    led = Ledger().plan().critique("sound")
    led.turn("asked")
    led.add("question.asked", {"question_id": "q", "turn_id": "t", "text": "?"})
    assert led.fold().return_to is State.BUILD


@pytest.mark.parametrize("question_id", [None, 5, ["q"], {"q": 1}, True])
def test_a_question_id_that_is_not_a_string_is_malformed(question_id):
    """Its answer could never name it, so the task does not wait on it."""
    led = Ledger().plan().critique("sound")
    led.turn("asked")
    led.add("question.asked", {"question_id": question_id, "turn_id": "t", "text": "?", "state": "build"})
    f = led.fold()
    assert f.state is State.BUILD and f.open_question is None
    assert f.ignored[-1]["type"] == "question.asked" and "not a string" in f.ignored[-1]["why"]


# -- the join table ----------------------------------------------------------------

# (review rounds in the plan, what is already spent, test, review, docs) -> row, goes to, outcome
JOIN_ROWS = [
    (1, None, "pass", "pass", "updated", 1, State.MERGE, "passed"),
    (1, None, "pass", "pass", "no_change", 1, State.MERGE, "passed"),
    (1, None, "red", "governance_refused", "changes", 2, State.MERGE, "governance_refused"),
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
    led.checks(test, review, docs, instances=("i1",) if review == "governance_refused" else ())
    f = led.fold()
    assert (f.join.row, f.state, f.join.outcome) == (row, goes, outcome)
    assert len(f.join.findings) == 3  # every finding of the three branches together


def test_row_two_reads_review_only_and_docs_governance_does_not_change_the_row():
    led = Ledger(review_rounds=1).to_checks()
    led.check("test", "pass").check("review", "changes")
    led.check("docs", "updated", head=led.candidate["sha"], instances=("doc-rule",))
    f = led.fold()
    assert f.join.row == 3 and f.state is State.PATCH


def test_governance_refused_comes_before_a_red_test():
    led = Ledger(review_rounds=1).to_checks()
    led.checks("red", "governance_refused", "no_change", instances=("i1",))
    assert led.fold().join.row == 2


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


def test_a_raise_applies_to_the_verdict_carrying_it_and_a_revised_plan_cannot_lower_it():
    led = Ledger(critique_rounds=0).plan()
    led.critique("revise", critique_rounds=1)
    assert led.state is State.PLAN  # sent back by its own raise
    led.plan(critique_rounds=0)
    assert led.fold().loops.critique_rounds == 1
    led.critique("revise", critique_rounds=0)  # a "raise" to 0 lowers nothing
    assert led.state is State.BUILD and led.fold().loops.critique_rounds == 1
    led2 = Ledger(critique_rounds=0).plan()
    led2.critique("revise", critique_rounds=2)
    led2.plan(critique_rounds=0).critique("revise")
    assert led2.state is State.PLAN  # the second revision the raise to 2 allows


def test_a_critique_of_an_older_plan_is_ignored():
    led = Ledger(critique_rounds=1).plan()
    old = led.plan_digest
    led.critique("revise").plan()
    led.critique("sound", digest=old)
    f = led.fold()
    assert f.state is State.CRITIQUE and "not the current one" in f.ignored[-1]["why"]


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


def test_a_critique_raise_of_review_rounds_to_2_is_accepted_and_read_as_1():
    led = Ledger(review_rounds=0).plan()
    led.critique("sound", review_rounds=2)
    f = led.fold()
    assert f.raised["review_rounds"] == 2 and f.loops.review_rounds == 1


def test_a_red_test_and_docs_changes_spend_the_plans_rounds_like_a_review_changes():
    for test, docs in (("red", "no_change"), ("pass", "changes")):
        led = Ledger(review_rounds=1).to_checks()
        led.checks(test, "pass", docs).build()
        f = led.fold()
        assert f.counts["review_rounds"] == 1 and f.counts["last_rounds"] == 0
        led.checks(test, "pass", docs)
        assert led.fold().join.spends == "last"


def test_the_last_round_is_spent_once_per_window_and_feedback_on_an_ended_task_opens_a_new_window():
    led = Ledger(review_rounds=0).to_checks()
    led.checks("red", "pass", "no_change")
    assert led.state is State.PATCH and led.fold().join.row == 5
    led.build().checks("red", "pass", "no_change")
    assert led.state is State.ENDED
    led.add("feedback.given", {"feedback_id": "f", "text": "fix the test"})
    f = led.fold()
    assert f.state is State.PATCH and f.counts == machine.FRESH_COUNTS
    led.build().checks("red", "pass", "no_change")
    assert led.state is State.PATCH  # the last round is available again


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


def test_a_rebase_sends_a_merge_back_to_patch_spending_no_round():
    led = Ledger(review_rounds=1).to_checks().checks("pass", "pass", "no_change")
    assert led.state is State.MERGE
    led.add("rebase.requested", {"effect_id": "e", "target_branch": "main", "tip": "abc"})
    f = led.fold()
    assert f.state is State.PATCH and f.counts["rebases"] == 1 and f.counts["review_rounds"] == 0
    led.build().checks("pass", "pass", "no_change")
    assert led.state is State.MERGE
    led.add("rebase.requested", {"effect_id": "e2"}).build()
    led.add("rebase.requested", {"effect_id": "e3"})
    assert "rebase requested in checks" in led.fold().ignored[-1]["why"]


def test_carried_verdicts_and_grants_make_the_replayed_sha_the_merged_candidate_spending_no_rebase():
    led = Ledger().to_checks()
    led.check("test", "pass").check("review", "pass", instances=["i1"]).check("docs", "updated", head="d1")
    led.add("guard.granted", {"instance_id": "i1"})
    led.add("task.delivered", {"candidate": led.candidate, "outcome": "passed"})
    old = led.candidate
    carried = {"from": {**old, "head": "d1"}, "to": {"sha": "r1", "turn_id": old["turn_id"], "head": "rd1"},
               "tip": "tip1", "verdicts": {}, "grants": ["i1"]}  # fmt: skip
    led.add("verdicts.carried", {**carried, "from": {**old, "head": "other"}})
    assert "not the current one" in led.fold().ignored[-1]["why"]
    led.add("verdicts.carried", carried)
    f = led.fold()
    new = {"sha": "r1", "turn_id": old["turn_id"]}
    assert f.state is State.MERGE and f.counts["rebases"] == 0 and f.merge_effect is None
    assert f.delivery["candidate"] == new and f.summary()["carried"][0]["tip"] == "tip1"
    assert machine.merge_predicate(f, {"candidate": new, "head_sha": "rd1"}) == []
    assert [t[0] for t in machine.merge_predicate(f, {"candidate": old, "head_sha": "d1"})] == ["1", "4"]
    led.add("verdicts.carried", carried)
    assert "not the current verified one" in led.fold().ignored[-1]["why"]


def test_feedback_after_merged_patches_and_review_rounds_are_available_again():
    led = Ledger(review_rounds=1).to_checks()
    led.checks("pass", "changes", "no_change").build().checks("pass", "pass", "no_change")
    led.add(
        "effect.held", {"effect_id": "e", "action_type": "merge", "payload": {"candidate": led.candidate}}
    )
    led.add("effect.outcome", {"effect_id": "e", "kind": "done"})
    assert led.state is State.MERGED and led.fold().counts["review_rounds"] == 1
    led.add("feedback.given", {"feedback_id": "f", "text": "a defect in use"})
    assert led.state is State.PATCH
    led.build().checks("pass", "changes", "no_change")
    assert led.state is State.PATCH  # a review round again, in the new window


# -- candidates, staleness, governance -----------------------------------------------


def test_a_stale_candidate_verdict_is_ignored():
    led = Ledger(review_rounds=1).to_checks()
    first = led.candidate
    led.checks("pass", "changes", "no_change").build()
    led.check("review", "pass", candidate=first)
    f = led.fold()
    assert Check.REVIEW not in f.checks and "stale" in f.ignored[-1]["why"]


def test_a_patch_with_reasons_and_no_code_change_is_a_new_candidate():
    led = Ledger(review_rounds=1).to_checks()
    sha = led.candidate["sha"]
    led.checks("pass", "changes", "no_change")
    led.build(sha=sha)  # the same commit, a new turn
    f = led.fold()
    assert f.candidate.sha == sha and f.state is State.CHECKS and f.checks == {}


def test_a_turn_collected_in_a_state_the_task_left_is_ignored():
    led = Ledger().plan().critique("sound")
    led.add("turn.collected", {"turn_id": "late", "state": "plan", "verdict": "planned"})
    f = led.fold()
    assert f.state is State.BUILD and "the task is in build" in f.ignored[-1]["why"]


def test_governance_granted_reruns_only_review_and_a_partial_grant_stays_in_merge():
    led = Ledger(review_rounds=1).to_checks()
    led.checks("pass", "governance_refused", "no_change", instances=("i1", "i2"))
    assert led.state is State.MERGE
    led.add("guard.granted", {"guard_id": "g1", "instance_id": "i1"})
    assert led.state is State.MERGE
    led.add("guard.granted", {"guard_id": "g2", "instance_id": "i2"})
    f = led.fold()
    assert f.state is State.CHECKS and set(f.checks) == {Check.TEST, Check.DOCS}
    led.check("review", "pass", instances=("i1", "i2"))
    f = led.fold()
    assert f.state is State.MERGE and f.join.row == 1 and not f.ungranted()


def test_a_grant_binds_to_the_instance_so_an_unchanged_hunk_stays_granted_across_patches():
    led = Ledger(review_rounds=1).to_checks()
    led.checks("pass", "governance_refused", "no_change", instances=("i1",))
    led.add("guard.granted", {"guard_id": "g1", "instance_id": "i1"})
    led.check("review", "changes", instances=("i1",)).build()
    led.checks("pass", "pass", "no_change", instances=("i1",))
    assert led.fold().ungranted() == []
    led.add("feedback.given", {"feedback_id": "f", "text": "x"}).build()
    led.checks("pass", "governance_refused", "no_change", instances=("i9",))
    assert [i.id for i in led.fold().ungranted()] == ["i9"]


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


# -- legacy -----------------------------------------------------------------------------


def legacy(*rows) -> machine.Fold:
    base = [{"id": 1, "type": "task.started", "payload": {"instruction": "x", "mode": "bare"}}]
    return machine.fold(base + [{"id": i + 2, "type": t, "payload": p} for i, (t, p) in enumerate(rows)])


def test_legacy_tasks_fold_with_the_old_precedence():
    q = ("question.asked", {"question_id": "q", "turn_id": "t", "text": "?"})
    a = ("question.answered", {"question_id": "q", "text": "a"})
    d = ("task.delivered", {"turn_id": "t", "summary": "done"})
    fb = ("feedback.given", {"feedback_id": "f", "text": "again"})
    t = ("turn.started", {"turn_id": "t"})
    assert legacy().state is State.JUDGE and legacy().legacy
    assert legacy(t).state is State.BUILD
    assert legacy(t, q).state is State.WAITING and legacy(t, q).return_to is State.BUILD
    assert legacy(t, q, a, d).state is State.MERGE
    assert legacy(t, d, q).state is State.MERGE  # a delivery beats an open question, as it did
    assert legacy(t, d, fb).state is State.PATCH
    assert legacy(t, d, fb, d).state is State.MERGE
    assert legacy(t, d, ("task.stopped", {"reason": "x"})).state is State.STOPPED


# -- the constraint is generated from VERDICTS ------------------------------------------


def test_the_constraint_names_every_verdict_value_and_its_name_tracks_its_text():
    sql = machine.constraint_sql()
    for values in machine.VERDICTS.values():
        for v in values:
            if v not in ("answered", "released", "feedback", "governance_granted", "rebase"):
                assert f"'{v}'" in sql
    assert machine.constraint_name().startswith("events_verdict_in_enum_")


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


def test_a_task_started_without_a_payload_folds_and_a_bad_raise_applies_nothing():
    assert machine.fold([{"id": 1, "type": "task.started"}]).legacy
    led = Ledger(critique_rounds=0).plan()
    led.add("critique.decided", {"plan_sha256": led.plan_digest, "verdict": "revise",
                                 "raised": {"critique_rounds": 2, "review_rounds": "x"}})  # fmt: skip
    f = led.fold()
    assert f.raised == {"critique_rounds": 0, "review_rounds": 0}  # neither count applied
    assert f.state is State.CRITIQUE and "raised counts" in f.ignored[-1]["why"]


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


def test_the_predicate_docs_term_reads_the_docs_verdict_and_its_head():
    led = Ledger().to_checks()
    led.check("test", "pass").check("review", "pass").check("docs", "updated", head="d1")
    f = led.fold()
    payload = {"candidate": led.candidate, "head_sha": "d1"}
    assert machine.merge_predicate(f, payload) == []
    assert [t[0] for t in machine.merge_predicate(f, {**payload, "head_sha": "d2"})] == ["4"]


@pytest.mark.parametrize(
    ("path", "doc"),
    [
        ("docs/guide.md", True),
        ("README.MD", True),
        ("docs/Deep/Notes.Md", True),
        ("core/x.py", False),
        ("claude.md", False),
        ("docs/Claude.MD", False),
        ("CLAUDE.local.md", False),
        ("x/claude.LOCAL.md", False),
        ("AGENTS.md", False),
        ("agents.override.md", False),
        ("Skills/build.md", False),
        ("docs/SKILLS/sdlc/plan.md", False),
        ("Persona/voice.md", False),
        (".Claude/agents/x.md", False),
        ("docs/skills.md", True),  # a file named like a directory is a doc
    ],
)
def test_doc_paths_ignore_letter_case_as_the_file_system_does(path, doc):
    assert machine.is_doc_path(path) is doc


def test_a_boolean_raise_is_not_a_count():
    led = Ledger(critique_rounds=0).plan()
    led.critique("revise", review_rounds=True)
    f = led.fold()
    assert f.state is State.CRITIQUE and f.raised == {"critique_rounds": 0, "review_rounds": 0}


def test_a_brief_takes_no_mode_and_an_old_document_with_one_still_loads():
    from core import tasks

    with pytest.raises(TypeError):
        tasks.Brief(instruction="x", mode="x")
    b = tasks.Brief(instruction="x")
    assert tasks.Brief.load({**tasks.asdict(b), "mode": "bare"}) == b


def test_a_malformed_turn_ended_changes_nothing():
    led = Ledger().plan().critique("sound")
    before = led.fold()
    led.add("turn.ended", {"outcome": "done", "result": {"session_id": "other"}})  # no turn_id
    led.add("turn.ended", {"turn_id": "t", "outcome": "done", "result": "text"})
    after = led.fold()
    assert after.session == before.session and len(after.ignored) == len(before.ignored) + 2


def test_a_turn_that_ended_in_an_error_does_not_spend_the_steering():
    led = Ledger()
    state = led.state.value
    led.add("message.steered", {"text": "use the other branch"})
    led.add("turn.started", {"turn_id": "e", "state": state})
    led.add(
        "turn.ended", {"turn_id": "e", "outcome": "done", "result": {"session_id": "s", "is_error": True}}
    )
    kept = led.fold().steering
    led.add("turn.started", {"turn_id": "ok", "state": state})
    led.add("turn.ended", {"turn_id": "ok", "outcome": "done", "result": {"session_id": "s"}})
    assert [p["text"] for p in kept] == ["use the other branch"] and led.fold().steering == []
