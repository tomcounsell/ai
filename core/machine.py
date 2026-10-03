"""The SDLC state machine: states, verdicts, transitions, loops, the join,
and the merge predicate, as one total fold over a task's ledger rows.

State is never stored. `fold(rows)` reads a task's rows in id order and
applies each row only when it is a transition from the state the task is in;
every other row is listed in `Fold.ignored` with why. It never raises, so
every prefix of every ledger folds to exactly one `State`.

Verdict values are refused at write by Postgres (`constraint_sql`, applied
by `db.migrate`), so the fold never meets a verdict outside its enum from
the kernel; it ignores one anyway.

Pure code: the standard library only, no I/O.
"""

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath as Path
from typing import Any


class State(StrEnum):
    JUDGE = "judge"
    CLARIFY = "clarify"
    WAITING = "waiting"
    PLAN = "plan"
    CRITIQUE = "critique"
    BUILD = "build"
    CHECKS = "checks"
    PATCH = "patch"
    MERGE = "merge"
    MERGED = "merged"
    STOPPED = "stopped"


class Check(StrEnum):
    TEST = "test"
    REVIEW = "review"
    DOCS = "docs"


# The states a turn of the working session runs in.
WORKING = (State.CLARIFY, State.PLAN, State.BUILD, State.PATCH)
# What the fold records as the state of a fresh session's turn: never a
# working state, so its session id is never resumed.
FRESH = "fresh"

VERDICTS: dict[State | Check, frozenset[str]] = {
    State.JUDGE: frozenset({"precise", "thin"}),
    State.CLARIFY: frozenset({"asked", "no_material_question", "idle", "failed"}),
    State.WAITING: frozenset({"answered"}),
    State.PLAN: frozenset({"planned", "asked", "idle", "failed"}),
    State.CRITIQUE: frozenset({"sound", "revise"}),
    State.BUILD: frozenset({"candidate", "asked", "idle", "failed"}),
    State.PATCH: frozenset({"candidate", "asked", "idle", "failed"}),
    State.MERGE: frozenset({"released", "feedback", "governance_granted"}),
    State.MERGED: frozenset({"feedback"}),
    Check.TEST: frozenset({"pass", "red", "gaps"}),
    Check.REVIEW: frozenset({"pass", "changes", "governance_refused"}),
    Check.DOCS: frozenset({"updated", "no_change", "changes"}),
}

TRANSITIONS: dict[tuple[State, str], State] = {
    (State.JUDGE, "precise"): State.PLAN,
    (State.JUDGE, "thin"): State.CLARIFY,
    (State.CLARIFY, "asked"): State.WAITING,
    (State.CLARIFY, "no_material_question"): State.PLAN,
    (State.PLAN, "planned"): State.CRITIQUE,
    (State.PLAN, "asked"): State.WAITING,
    (State.CRITIQUE, "sound"): State.BUILD,
    (State.CRITIQUE, "revise"): State.PLAN,  # while critique rounds remain, else BUILD
    (State.BUILD, "candidate"): State.CHECKS,
    (State.BUILD, "asked"): State.WAITING,
    (State.PATCH, "candidate"): State.CHECKS,
    (State.PATCH, "asked"): State.WAITING,
    (State.MERGE, "released"): State.MERGED,
    (State.MERGE, "feedback"): State.PATCH,
    (State.MERGE, "governance_granted"): State.CHECKS,  # review reruns alone
    (State.MERGED, "feedback"): State.PATCH,
}
# CHECKS leaves only through `join`. "answered" returns to the state named on
# the question.asked row. Any state goes to STOPPED on task.stopped; nothing
# leaves STOPPED.

# The verdict row of each stage whose verdict is its own row.
VERDICT_ROWS: dict[str, State | Check] = {
    "judge.decided": State.JUDGE,
    "critique.decided": State.CRITIQUE,
    "test.decided": Check.TEST,
    "review.decided": Check.REVIEW,
    "docs.decided": Check.DOCS,
}
CHECK_ROWS = {"test.decided": Check.TEST, "review.decided": Check.REVIEW, "docs.decided": Check.DOCS}

ROUNDS = (0, 1, 2)

# The guards each redirect fires under (core/guards.py seeds them).
GUARD_JUDGE = "intake.underspecified"
GUARD_BREADTH = "checks.test.breadth"
GUARD_CRITIQUE = "critique.loop"
GUARD_REVIEW = "review.loop"


def _in(expr: str, values) -> str:
    return f"coalesce({expr}, '') IN ({', '.join(repr(v) for v in sorted(values))})"


VERDICT = "payload->>'verdict'"


def constraint_sql() -> str:
    """The `CHECK` expression that refuses a verdict outside its enum, built
    from `VERDICTS` so the enum lives in one place. Every branch is a
    boolean, never NULL, since a CHECK passes on NULL."""
    rounds = [str(n) for n in ROUNDS]
    branches = []
    for type_, stage in sorted(VERDICT_ROWS.items()):
        test = _in(VERDICT, VERDICTS[stage])
        if type_ == "critique.decided":
            for count in ("critique_rounds", "review_rounds"):
                raised = f"payload->'raised'->>'{count}'"
                test += f" AND ({raised} IS NULL OR {_in(raised, rounds)})"
        branches.append(f"WHEN '{type_}' THEN {test}")
    per_state = " ".join(f"WHEN '{s.value}' THEN {_in(VERDICT, VERDICTS[s])}" for s in WORKING)
    branches.append(
        "WHEN 'turn.collected' THEN ((payload->>'state') IS NULL AND (payload->>'verdict') IS NULL) "
        f"OR coalesce(CASE payload->>'state' {per_state} ELSE false END, false)"
    )
    branches.append(
        "WHEN 'plan.written' THEN "
        f"{_in("payload->>'critique_rounds'", rounds)} AND "
        f"{_in("payload->>'review_rounds'", rounds)}"
    )
    return "coalesce(CASE type " + " ".join(branches) + " ELSE true END, false)"


def constraint_name() -> str:
    """The constraint's name carries a digest of its SQL, so `migrate` can
    tell whether the one in place is current without comparing Postgres's
    deparsed text."""
    return "events_verdict_in_enum_" + hashlib.sha256(constraint_sql().encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Candidate:
    sha: str
    turn_id: str


@dataclass(frozen=True)
class Finding:
    source: str
    kind: str
    text: str


@dataclass(frozen=True)
class Instance:
    """One governance instance a review or docs verdict named: a hunk of the
    diff, identified by `git.Hunk.id`."""

    id: str
    path: str
    summary: str = ""
    incident: str | None = None
    mission_item: str | None = None


@dataclass(frozen=True)
class CheckVerdict:
    check: Check
    candidate: Candidate
    verdict: str
    findings: tuple[Finding, ...]
    instances: tuple[Instance, ...]
    event_id: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class Loops:
    critique_rounds: int
    review_rounds: int


@dataclass(frozen=True)
class JoinResult:
    row: int  # the join table's row, 1 to 7
    target: State
    outcome: str | None  # for MERGE: passed, gaps, did_not_pass, governance_refused
    spends: str | None  # "review" or "repair" for a send-back
    findings: tuple[Finding, ...]


@dataclass
class Fold:
    state: State = State.JUDGE
    legacy: bool = False
    # A calibration task (`python -m core calibrate`): metered judgement
    # calls and nothing else. No SDLC row applies; every SDLC writer refuses it.
    calibration: bool = False
    return_to: State | None = None
    open_question: str | None = None
    plan: dict[str, Any] | None = None
    raised: dict[str, int] = field(default_factory=lambda: {"critique_rounds": 0, "review_rounds": 0})
    candidate: Candidate | None = None
    checks: dict[Check, CheckVerdict] = field(default_factory=dict)
    counts: dict[str, int] = field(
        default_factory=lambda: {"critique_revisions": 0, "review_rounds": 0, "repair_rounds": 0}
    )
    entry: dict[str, Any] | None = None
    entry_finished: bool = False
    join: JoinResult | None = None
    delivery: dict[str, Any] | None = None
    granted: set[str] = field(default_factory=set)
    merge_effect: dict[str, Any] | None = None
    session: str | None = None
    last_collected: dict[str, Any] | None = None
    turn_states: dict[str, str] = field(default_factory=dict)
    ignored: list[dict[str, Any]] = field(default_factory=list)
    # Steering: `message.steered` rows by id, and the id of the
    # `turn.started` of the last working-session turn that finished. Only
    # such a turn renders `next_prompt`, so only it spends steering.
    steered: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    steer_since: int = 0
    turn_rows: dict[str, int] = field(default_factory=dict)

    @property
    def steering(self) -> list[dict[str, Any]]:
        """What Tom wrote after the start of the last working turn that
        finished, oldest first: the next working turn reads it."""
        return [p for i, p in self.steered if i > self.steer_since]

    @property
    def loops(self) -> Loops:
        plan = self.plan or {}
        return Loops(
            max(int(plan.get("critique_rounds", 0)), self.raised["critique_rounds"]),
            max(int(plan.get("review_rounds", 0)), self.raised["review_rounds"]),
        )

    def instances(self) -> list[Instance]:
        """Every governance instance the current candidate's review and docs
        verdicts name."""
        found: dict[str, Instance] = {}
        for check in (Check.REVIEW, Check.DOCS):
            if check in self.checks:
                for i in self.checks[check].instances:
                    found.setdefault(i.id, i)
        return list(found.values())

    def ungranted(self) -> list[Instance]:
        return [i for i in self.instances() if i.id not in self.granted]

    def summary(self) -> dict[str, Any]:
        """The fold as `status` shows it: plain JSON."""
        return {
            "state": self.state.value,
            "legacy": self.legacy,
            "calibration": self.calibration,
            "return_to": self.return_to.value if self.return_to else None,
            "plan": self.plan,
            "loops": {
                "critique_rounds": self.loops.critique_rounds,
                "review_rounds": self.loops.review_rounds,
            },
            "counts": dict(self.counts),
            "candidate": {"sha": self.candidate.sha, "turn_id": self.candidate.turn_id}
            if self.candidate
            else None,
            "checks": {
                c.value: {"verdict": v.verdict, "event_id": v.event_id} for c, v in self.checks.items()
            },
            "join": {"row": self.join.row, "goes_to": self.join.target.value, "outcome": self.join.outcome}
            if self.join
            else None,
            "governance": [
                {"id": i.id, "path": i.path, "summary": i.summary, "granted": i.id in self.granted}
                for i in self.instances()
            ],
            "merge_effect": self.merge_effect,
            "ignored": len(self.ignored),
        }


def _candidate(value: Any) -> Candidate:
    return Candidate(str(value["sha"]), str(value["turn_id"]))


def _findings(source: str, payload: dict[str, Any]) -> tuple[Finding, ...]:
    out = []
    for f in payload.get("findings") or []:
        if isinstance(f, dict):
            out.append(Finding(source, str(f.get("kind", "finding")), str(f.get("text", ""))))
        else:
            out.append(Finding(source, "finding", str(f)))
    return tuple(out)


def _instances(payload: dict[str, Any]) -> tuple[Instance, ...]:
    gov = payload.get("governance") or {}
    return tuple(
        Instance(
            str(i["id"]),
            str(i.get("path", "")),
            str(i.get("summary", "")),
            i.get("incident"),
            i.get("mission_item"),
        )
        for i in gov.get("instances") or []
    )


DOCS_PASS = ("updated", "no_change")


def join(checks: dict[Check, CheckVerdict], loops: Loops, counts: dict[str, int]) -> JoinResult:
    """The join table, read in its order, first match wins. Row 2 reads
    review only."""
    r, t, d = (checks[c].verdict for c in (Check.REVIEW, Check.TEST, Check.DOCS))
    found = tuple(f for c in (Check.TEST, Check.REVIEW, Check.DOCS) for f in checks[c].findings)
    if r == "pass" and t == "pass" and d in DOCS_PASS:
        return JoinResult(1, State.MERGE, "passed", None, found)
    if r == "governance_refused":
        return JoinResult(2, State.MERGE, "governance_refused", None, found)
    if r == "changes" and counts["review_rounds"] < loops.review_rounds:
        return JoinResult(3, State.PATCH, None, "review", found)
    if r == "changes":
        return JoinResult(4, State.MERGE, "did_not_pass", None, found)
    if counts["repair_rounds"] < 1 and (t in ("red", "gaps") or d == "changes"):
        return JoinResult(5, State.PATCH, None, "repair", found)
    if t == "red" or d == "changes":
        return JoinResult(6, State.MERGE, "did_not_pass", None, found)
    return JoinResult(7, State.MERGE, "gaps", None, found)


def fold(rows: list[dict[str, Any]]) -> Fold:
    """The task's state from its rows, in id order. Total: never raises."""
    rows = [r for r in rows if isinstance(r, dict)]
    start = next((r for r in rows if r.get("type") == "task.started"), None)
    payload = start.get("payload") if start is not None else None
    if isinstance(payload, dict) and payload.get("calibration"):
        return Fold(calibration=True)
    sdlc = isinstance(payload, dict) and payload.get("sdlc") == 1
    if not sdlc:
        return _legacy(rows)
    f = Fold()
    started = False
    for row in rows:
        try:
            why = _apply(f, row, started)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            why = f"malformed: {exc!r}"
        if row.get("type") == "task.started" and not started:
            started = True
        if why:
            f.ignored.append({"id": row.get("id"), "type": row.get("type"), "why": why})
    return f


def _enter(f: Fold, state: State, entry: dict[str, Any]) -> None:
    f.state = state
    f.entry = entry
    f.entry_finished = False


def _apply(f: Fold, row: dict[str, Any], started: bool) -> str | None:
    kind, p = row["type"], row["payload"]
    if not isinstance(p, dict):
        return "payload is not an object"
    if f.state is State.STOPPED:
        return "the task is stopped"
    if kind == "task.started":
        if started:
            return "a second task.started"
        _enter(f, State.JUDGE, {"type": kind, "id": row.get("id"), "payload": p})
        return None
    if kind == "task.stopped":
        _enter(f, State.STOPPED, {"type": kind, "id": row.get("id"), "payload": p})
        return None
    entry = {"type": kind, "id": row.get("id"), "payload": p}
    s = f.state
    if kind == "turn.started":
        f.turn_states[str(p["turn_id"])] = FRESH if p.get("fresh") else str(p.get("state"))
        f.turn_rows[str(p["turn_id"])] = int(row.get("id") or 0)
        return None
    if kind == "message.steered":
        f.steered.append((int(row.get("id") or 0), p))
        return None
    if kind == "turn.ended":
        turn_id = str(p["turn_id"])  # read before anything changes, so a malformed row changes nothing
        result = p.get("result") or {}
        if not isinstance(result, dict):
            return "turn.ended result is not an object"
        # Only the working session's turns name the session the next
        # clarify, plan, build, or patch turn resumes; a fresh session's
        # (critique, review, docs) never does.
        if result.get("session_id") and f.turn_states.get(turn_id) != FRESH:
            f.session = result["session_id"]
        if p.get("outcome") == "done" and turn_id in f.turn_states and f.turn_states[turn_id] != FRESH:
            f.steer_since = max(f.steer_since, f.turn_rows.get(turn_id, 0))
        if (
            p.get("outcome") == "done"
            and not result.get("is_error")
            and f.turn_states.get(turn_id) == s.value
        ):
            f.entry_finished = True
        return None
    if kind == "judge.decided":
        if s is not State.JUDGE:
            return f"judge verdict in {s}"
        verdict = p["verdict"]
        if verdict not in VERDICTS[State.JUDGE]:
            return f"verdict {verdict!r} outside its enum"
        _enter(f, TRANSITIONS[(State.JUDGE, verdict)], entry)
        return None
    if kind == "turn.collected":
        f.last_collected = p
        state, verdict = p.get("state"), p.get("verdict")
        if state is None:
            return "a turn.collected without a state"
        if state != s.value:
            return f"collected in {state}, the task is in {s}"
        if verdict not in VERDICTS[s]:
            return f"verdict {verdict!r} outside its enum"
        if verdict == "no_material_question":
            _enter(f, State.PLAN, entry)
        elif verdict == "candidate":
            f.candidate = _candidate(p["candidate"])
            f.checks = {}
            f.join = None
            f.merge_effect = None
            _enter(f, State.CHECKS, entry)
        # `asked` and `planned` move on their own rows, written with this one.
        return None
    if kind == "question.asked":
        asked_in = p.get("state", s.value)
        if s not in WORKING or asked_in != s.value:
            return f"question asked in {asked_in}, the task is in {s}"
        f.return_to = s
        f.open_question = str(p["question_id"])
        _enter(f, State.WAITING, entry)
        return None
    if kind == "question.answered":
        if s is not State.WAITING or p.get("question_id") != f.open_question:
            return "no open question with that id"
        back = f.return_to or State.BUILD
        f.return_to = None
        f.open_question = None
        _enter(f, back, entry)
        return None
    if kind == "plan.written":
        if s is not State.PLAN:
            return f"plan written in {s}"
        if int(p["critique_rounds"]) not in ROUNDS or int(p["review_rounds"]) not in ROUNDS:
            return "loop counts outside 0..2"
        f.plan = {k: p.get(k) for k in ("path", "commit", "sha256", "stakes", "critique_rounds",
                                          "review_rounds", "scope")}  # fmt: skip
        _enter(f, State.CRITIQUE, entry)
        return None
    if kind == "critique.decided":
        if s is not State.CRITIQUE:
            return f"critique verdict in {s}"
        if not f.plan or p.get("plan_sha256") != f.plan.get("sha256"):
            return "critique of a plan that is not the current one"
        verdict = p["verdict"]
        if verdict not in VERDICTS[State.CRITIQUE]:
            return f"verdict {verdict!r} outside its enum"
        raised = p.get("raised") or {}
        if not isinstance(raised, dict) or any(
            k in f.raised and (not isinstance(v, int) or isinstance(v, bool) or v not in ROUNDS)
            for k, v in raised.items()
        ):
            return "raised counts outside 0..2"
        # Validated whole, then applied, so a row never half-applies.
        for k, v in raised.items():
            if k in f.raised:
                f.raised[k] = max(f.raised[k], v)
        if verdict == "revise" and f.counts["critique_revisions"] < f.loops.critique_rounds:
            f.counts["critique_revisions"] += 1
            _enter(f, State.PLAN, entry)
        else:
            _enter(f, State.BUILD, entry)
        return None
    if kind in CHECK_ROWS:
        check = CHECK_ROWS[kind]
        if s is not State.CHECKS:
            return f"{check} verdict in {s}"
        candidate = _candidate(p["candidate"])
        if candidate != f.candidate:
            return "stale: keyed to a candidate that is not the current one"
        if p["verdict"] not in VERDICTS[check]:
            return f"verdict {p['verdict']!r} outside its enum"
        f.checks[check] = CheckVerdict(
            check, candidate, p["verdict"], _findings(check.value, p), _instances(p), row.get("id") or 0, p
        )
        if len(f.checks) == 3:
            result = join(f.checks, f.loops, f.counts)
            f.join = result
            if result.spends == "review":
                f.counts["review_rounds"] += 1
            elif result.spends == "repair":
                f.counts["repair_rounds"] += 1
            _enter(
                f,
                result.target,
                {
                    "type": "join",
                    "id": row.get("id"),
                    "payload": {"row": result.row, "outcome": result.outcome},
                },
            )
        return None
    if kind == "task.delivered":
        f.delivery = p
        return None
    if kind == "feedback.given":
        if s not in (State.MERGE, State.MERGED):
            return f"feedback in {s}"
        f.counts = {"critique_revisions": 0, "review_rounds": 0, "repair_rounds": 0}
        _enter(f, State.PATCH, entry)
        return None
    if kind == "guard.granted":
        if p.get("instance_id"):
            f.granted.add(str(p["instance_id"]))
        if (
            s is State.MERGE
            and f.join is not None
            and f.join.outcome == "governance_refused"
            and Check.REVIEW in f.checks
            and all(i.id in f.granted for i in f.checks[Check.REVIEW].instances)
        ):
            del f.checks[Check.REVIEW]
            f.join = None
            _enter(f, State.CHECKS, entry)
        return None
    if kind in ("effect.held", "effect.intent", "effect.outcome", "effect.refused"):
        return _merge_effect(f, kind, p, entry)
    return None


def _merge_effect(f: Fold, kind: str, p: dict[str, Any], entry: dict[str, Any]) -> str | None:
    if kind in ("effect.held", "effect.refused"):
        if p.get("action_type") != "merge":
            return None
        named = _candidate((p.get("payload") or {})["candidate"])
        if named != f.candidate:
            return "a merge effect for a candidate that is not the current one"
        f.merge_effect = {
            "effect_id": p["effect_id"],
            "state": "held" if kind == "effect.held" else "refused",
            "candidate": {"sha": named.sha, "turn_id": named.turn_id},
            "payload_sha256": p.get("payload_sha256"),
            "grants": len(f.granted),  # the grants that stood when it was held or refused
        }
        return None
    if not f.merge_effect or p.get("effect_id") != f.merge_effect["effect_id"]:
        return None
    if kind == "effect.intent":
        f.merge_effect = {**f.merge_effect, "state": "in_flight"}
        return None
    f.merge_effect = {**f.merge_effect, "state": p.get("kind")}
    if p.get("kind") == "done" and f.state is State.MERGE:
        _enter(f, State.MERGED, entry)
    return None


def _legacy(rows: list[dict[str, Any]]) -> Fold:
    """A task started before the state machine, folded with the old kernel's
    own precedence: stopped; a delivery not reopened by feedback; an
    unanswered question; feedback after a delivery; any turn; nothing."""
    f = Fold(legacy=True)
    stopped = delivered = reopened = turns = False
    asked: dict[str, bool] = {}
    fresh: set[str] = set()
    for row in rows:
        try:
            kind, p = row["type"], row["payload"]
            if kind == "task.stopped":
                stopped = True
            elif kind == "task.delivered":
                delivered, reopened = True, False
                f.delivery = p
            elif kind == "feedback.given":
                reopened = True
            elif kind == "question.asked":
                asked[str(p["question_id"])] = False
            elif kind == "question.answered":
                asked[str(p["question_id"])] = True
            elif kind == "turn.started":
                turns = True
                if p.get("fresh"):
                    fresh.add(str(p["turn_id"]))
            elif kind == "turn.ended":
                result = p.get("result") or {}
                if str(p["turn_id"]) not in fresh:
                    f.session = result.get("session_id") or f.session
        except (KeyError, TypeError, AttributeError) as exc:
            f.ignored.append({"id": row.get("id"), "type": row.get("type"), "why": f"malformed: {exc!r}"})
    if stopped:
        f.state = State.STOPPED
    elif delivered and not reopened:
        f.state = State.MERGE
    elif not all(asked.values()):
        f.state, f.return_to = State.WAITING, State.BUILD
        f.open_question = next(q for q, done in asked.items() if not done)
    elif delivered and reopened:
        f.state = State.PATCH
    elif turns:
        f.state = State.BUILD
    return f


# Markdown that is instruction, not description: rendered into a turn's
# Brief or read by a harness as its standing orders. A docs commit never
# touches these; changing them is the builder's work, under review.
# Compared casefolded: this Mac's file system ignores case, so `claude.md`
# or `Skills/build.md` is read as `CLAUDE.md` or `skills/` on a clone.
INSTRUCTION_FILES = ("claude.md", "claude.local.md", "agents.md", "agents.override.md")
INSTRUCTION_DIRS = ("skills", "persona", ".claude")


def is_doc_path(path: str) -> bool:
    """Whether a docs commit may touch this path: a Markdown file, and not
    one that instructs a turn (`INSTRUCTION_FILES` anywhere, anything under
    `INSTRUCTION_DIRS` at any depth), whatever the case of any part. A plan
    cannot widen this."""
    parts = [part.casefold() for part in Path(path).parts]
    if not parts or not parts[-1].endswith(".md"):
        return False
    if parts[-1] in INSTRUCTION_FILES:
        return False
    return not any(part in INSTRUCTION_DIRS for part in parts[:-1])


@dataclass(frozen=True)
class GitFacts:
    """What the broker reads from the workspace at release, between the
    candidate and the head being merged."""

    ancestor: bool
    merges: tuple[str, ...]
    paths: tuple[str, ...]


def merge_predicate(
    f: Fold, payload: dict[str, Any], *, approval_unused: bool, facts: GitFacts | None
) -> list[str]:
    """The five terms of the merge predicate over a merge effect's payload.
    Returns each term that does not hold, named; empty means the merge may
    be performed. Each term reads a row or a git fact."""
    failed = []
    named = payload.get("candidate") or {}
    current = f.candidate
    checks = f.checks
    if (
        f.state is not State.MERGE
        or current is None
        or named != {"sha": current.sha, "turn_id": current.turn_id}
        or len(checks) != 3
    ):
        failed.append("1: one current candidate with a test, review, and docs verdict, named by this merge")
    review = checks.get(Check.REVIEW)
    if review is None or review.verdict != "pass" or any(i.id not in f.granted for i in review.instances):
        failed.append("2: review passed, and every governance instance it named is granted by Tom")
    test = checks.get(Check.TEST)
    gaps_ok = (
        test is not None
        and test.verdict == "gaps"
        and f.counts["repair_rounds"] >= 1
        and (f.delivery or {}).get("outcome") == "gaps"
    )
    if test is None or not (test.verdict == "pass" or gaps_ok):
        failed.append("3: test passed, or gaps with the repair round spent and the gaps on the delivery")
    docs = checks.get(Check.DOCS)
    if (
        docs is None
        or docs.verdict not in DOCS_PASS
        or docs.payload.get("head") != payload.get("head_sha")
        or facts is None
        or not facts.ancestor
        or facts.merges
        or not all(is_doc_path(p) for p in facts.paths)
        or any(i.id not in f.granted for i in docs.instances)
    ):
        failed.append(
            "4: docs updated or unchanged, its commits running from the candidate to this head "
            "with no merge commit and touching only doc paths, any governance granted"
        )
    if not approval_unused:
        failed.append("5: an unused approval from Tom bound to this merge's digest")
    return failed
