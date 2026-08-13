"""Unit tests for the sdlc-progress-check auto-resume reflection (issue #2696).

The reflection detects a stalled SDLC lane and *acts* on it: it steers a live
eng session, resumes a terminal one, or creates one, and pages a human at most
once per ``(slug, head-sha)`` and only when the system tried and could not.

What is faked here, and why:

* Redis is a local ``_FakeRedis`` with real ``SET NX EX`` / ``GET`` / ``INCR``
  semantics and a ``advance()`` clock, so cooldown expiry and the attempt
  budget are exercised for real rather than asserted structurally.
* ``AgentSession.query`` is fenced at the boundary — the reflection's job is to
  compose queries, not to test Popoto.
* ``gh`` / ``git`` / ``valor-telegram`` are fenced at ``subprocess``.

Two prohibitions this module honors deliberately (see the plan's "Failure Path
Test Strategy"):

* **No test mocks ``touch_issue_lock`` to raise.** It does not raise — it fails
  open and returns ``acquired=True`` on every Redis exception. The reflection
  never calls it; the injectable boundary is the direct ``_R.get`` on
  ``session:issuelock:{N}``, which does raise.
* **No test mocks ``steer_session`` into a failure-while-non-terminal shape.**
  A live non-ledger steer target simply succeeds; every real steer failure is a
  dead end and charges an attempt.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

import reflections.utilities
from reflections import sdlc_progress

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

_PASS = object()  # get_hook sentinel: "fall through to the backing store"

# Captured before any fixture patches it, so the one test that exercises the
# real Telegram subprocess boundary can restore it.
_REAL_SEND_ALERT = sdlc_progress._send_alert


class _FakeRedis:
    """Redis stand-in with real NX/EX/INCR semantics and a manual clock."""

    def __init__(self):
        self._vals: dict[str, str] = {}
        self._exp: dict[str, float | None] = {}
        self.now = 0.0
        self.raise_get = None  # callable(key) -> bool
        self.raise_set = False
        self.raise_incr = False
        self.raise_delete = False
        self.get_hook = None  # callable(key) -> value | _PASS

    # -- test helpers -------------------------------------------------------
    def advance(self, seconds: float) -> None:
        """Move the clock forward, expiring anything whose TTL has lapsed."""
        self.now += seconds
        for key, exp in list(self._exp.items()):
            if exp is not None and exp <= self.now:
                self._vals.pop(key, None)
                self._exp.pop(key, None)

    def keys_present(self) -> set[str]:
        return set(self._vals)

    # -- redis surface ------------------------------------------------------
    def get(self, key):
        if self.raise_get is not None and self.raise_get(key):
            raise RuntimeError("redis down")
        if self.get_hook is not None:
            val = self.get_hook(key)
            if val is not _PASS:
                return val
        return self._vals.get(key)

    def set(self, key, value, nx=False, ex=None):
        if self.raise_set:
            raise RuntimeError("redis down")
        if nx and key in self._vals:
            return False
        self._vals[key] = value
        self._exp[key] = (self.now + ex) if ex else None
        return True

    def incr(self, key):
        if self.raise_incr:
            raise RuntimeError("redis down")
        cur = int(self._vals.get(key, 0)) + 1
        self._vals[key] = str(cur)
        return cur

    def delete(self, key):
        if self.raise_delete:
            raise RuntimeError("redis down")
        existed = key in self._vals
        self._vals.pop(key, None)
        self._exp.pop(key, None)
        return 1 if existed else 0

    def expire(self, key, seconds):
        if key in self._vals:
            self._exp[key] = self.now + seconds
        return True


class _Rows(list):
    """List with Popoto's ``.first()`` so ``_row_is_nonterminal`` works."""

    def first(self):
        return self[0] if self else None


class _FakeQuery:
    """``AgentSession.query`` stand-in routed by the filter kwargs used."""

    def __init__(self):
        self.by_project: list = []
        self.by_session_id: list = []
        self.raise_on: set[str] = set()
        self.calls: list[dict] = []

    def filter(self, **kw):
        self.calls.append(dict(kw))
        for field, rows in (
            ("project_key", self.by_project),
            ("session_id", self.by_session_id),
        ):
            if field in kw:
                if field in self.raise_on:
                    raise RuntimeError("redis down")
                return _Rows(rows)
        return _Rows()


class _Row:
    """Minimal AgentSession row shape the reflection actually reads."""

    def __init__(
        self,
        session_id="s1",
        status="running",
        *,
        is_ledger=False,
        claude_session_uuid=None,
        updated_at=None,
        project_key="valor",
        slug=None,
    ):
        self.session_id = session_id
        self.status = status
        # Popoto round-trips Field(default=False) as the STRING "False"; the
        # reflection's _is_ledger uses _truthy, so mirror the string form.
        self.is_ledger = "True" if is_ledger else "False"
        self.claude_session_uuid = claude_session_uuid
        self.updated_at = time.time() if updated_at is None else updated_at
        self.project_key = project_key
        self.slug = slug


class _Result:
    """``ResumeResult`` / ``CreateResult`` stand-in."""

    def __init__(self, success=True, error=None, session_id="new-1"):
        self.success = success
        self.error = error
        self.session_id = session_id


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_PROJECT = {"slug": "valor", "working_directory": "/tmp/fake-valor-repo"}


def _pr(number=1237, branch="session/sdlc-1395", draft=False):
    return {"number": number, "headRefName": branch, "isDraft": draft, "baseRefName": "main"}


def _lock_key(issue_number: int) -> str:
    return sdlc_progress._LOCK_KEY.format(issue_number=issue_number)


def _live_lock_payload() -> str:
    """A payload ``_lock_owner_is_live`` really classifies as live.

    Renewal recency is consulted first and short-circuits to True, so a fresh
    ``renewed_at`` is genuine positive evidence — no patching of the classifier.
    """
    return json.dumps({"renewed_at": time.time(), "pid": 4242, "hostname": "somebox"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Lab:
    """Everything a ladder test needs to observe, in one place."""

    def __init__(self, redis: _FakeRedis, query: _FakeQuery):
        self.redis = redis
        self.query = query
        self.alerts: list[str] = []
        self.steers: list[tuple[str, str]] = []
        self.resumes: list[tuple] = []
        self.creates: list[dict] = []
        self.steer_result: dict = {"success": True, "session_id": "s1", "error": None}
        self.resume_result = _Result(True)
        self.create_result = _Result(True)
        self.steer_raises = False
        self.create_raises = False

    def attempts(self, slug="sdlc-1395", sha="abc123def456") -> int:
        raw = self.redis.get(sdlc_progress._ATTEMPTS_KEY.format(slug=slug, sha=sha))
        return int(raw) if raw is not None else 0


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(sdlc_progress, "_get_redis", lambda: r)
    # _lock_says_live's body now resolves its Redis client in
    # reflections.utilities (moved there in #2717 so a sibling reflection can
    # share the liveness rule); the sdlc_progress patch above no longer
    # reaches it, so patch both.
    monkeypatch.setattr(reflections.utilities, "_get_redis", lambda: r)
    return r


@pytest.fixture
def fake_query(monkeypatch):
    q = _FakeQuery()
    session_cls = MagicMock()
    session_cls.query = q
    monkeypatch.setattr("models.agent_session.AgentSession", session_cls)
    return q


@pytest.fixture
def owns_project(monkeypatch):
    """Gate 6' passes.

    Deliberately NOT autouse: it is pulled in by ``lab``, which is what every
    ladder test uses. A test that reaches ``_check_project_stalls`` without the
    ``lab`` fence therefore still hits the real gate 6' and stops there, rather
    than scanning real projects and dispatching real actions.
    """
    monkeypatch.setattr(sdlc_progress, "machine_owns_project", lambda key: True)


@pytest.fixture
def lab(fake_redis, fake_query, owns_project, monkeypatch):
    """Wire every external boundary the ladder can reach to a recorder."""
    lab = _Lab(fake_redis, fake_query)

    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: lab.alerts.append(msg))

    def _steer(session_id, message):
        lab.steers.append((session_id, message))
        if lab.steer_raises:
            raise RuntimeError("steer blew up")
        return lab.steer_result

    def _resume(session, message, source=None):
        lab.resumes.append((session, message, source))
        return lab.resume_result

    def _create(**kwargs):
        lab.creates.append(kwargs)
        if lab.create_raises:
            raise RuntimeError("create blew up")
        return lab.create_result

    monkeypatch.setattr("agent.session_executor.steer_session", _steer)
    monkeypatch.setattr("tools.valor_session.resume_session", _resume)
    monkeypatch.setattr("tools.valor_session.create_session", _create)
    return lab


@pytest.fixture
def stub_workdir(monkeypatch):
    """Bypass the ``Path(wd).is_dir()`` gate."""
    monkeypatch.setattr(sdlc_progress.Path, "is_dir", lambda self: True)


@pytest.fixture
def stalled_pr(monkeypatch):
    """Gates 1-4 pass: one open non-draft SDLC PR whose head commit is 9h old."""
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr(number=1237)])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(
        sdlc_progress, "_last_commit", lambda cwd, branch: ("abc123def456", now - 9 * 3600)
    )


# ---------------------------------------------------------------------------
# Gate 5' — _lock_says_live: unknown vs. free must be distinguishable
# ---------------------------------------------------------------------------


def test_lock_key_absent_is_free_not_unknown(fake_redis):
    """No lock key at all → nobody owns the lane → False (continue)."""
    assert sdlc_progress._lock_says_live(1395) is False


def test_lock_read_raising_is_unknown(fake_redis):
    """A Redis exception on the GET → None, never False."""
    fake_redis.raise_get = lambda key: key == _lock_key(1395)
    assert sdlc_progress._lock_says_live(1395) is None


def test_lock_read_malformed_payload_is_unknown_not_free(fake_redis):
    """A non-JSON payload is *unknown*, not "held by nobody"."""
    fake_redis.set(_lock_key(1395), "}{ not json")
    assert sdlc_progress._lock_says_live(1395) is None


def test_lock_read_live_payload_is_live(fake_redis):
    fake_redis.set(_lock_key(1395), _live_lock_payload())
    assert sdlc_progress._lock_says_live(1395) is True


def test_lock_read_three_outcomes_are_mutually_distinguishable(fake_redis):
    """free / live / unknown must be three distinct values, not two."""
    absent = sdlc_progress._lock_says_live(1395)

    fake_redis.set(_lock_key(1395), _live_lock_payload())
    live = sdlc_progress._lock_says_live(1395)

    fake_redis.raise_get = lambda key: True
    unknown = sdlc_progress._lock_says_live(1395)

    assert (absent, live, unknown) == (False, True, None)
    assert len({absent, live, unknown}) == 3


def test_lock_read_decodes_bytes_payload(fake_redis):
    """redis-py returns bytes by default; the gate must not choke on it."""
    fake_redis.set(_lock_key(1395), _live_lock_payload().encode("utf-8"))
    assert sdlc_progress._lock_says_live(1395) is True


# ---------------------------------------------------------------------------
# Gate 5' — _lane_is_live is the lock signal and nothing else
#
# An earlier design OR-ed in a secondary AgentSession row check keyed on
# issue_number. That branch was structurally dead — no non-ledger creation path
# populates AgentSession.issue_number — so it, its freshness knob, and its tests
# are gone. What survives is the lock polarity.
# ---------------------------------------------------------------------------


def test_lane_is_live_absent_lock_is_free(fake_redis, fake_query):
    assert sdlc_progress._lane_is_live(1395) is False


def test_lane_is_live_live_lock_is_live(fake_redis, fake_query):
    fake_redis.set(_lock_key(1395), _live_lock_payload())
    assert sdlc_progress._lane_is_live(1395) is True


def test_lane_is_live_unknown_lock_is_unknown_and_queries_nothing(fake_redis, fake_query):
    """A degraded backend declines to act; it never falls back to a row query."""
    fake_redis.raise_get = lambda key: True
    assert sdlc_progress._lane_is_live(1395) is None
    assert fake_query.calls == [], "the gate must consult no session rows at all"


def test_lane_is_live_malformed_payload_is_unknown(fake_redis, fake_query):
    fake_redis.set(_lock_key(1395), "}{ not json")
    assert sdlc_progress._lane_is_live(1395) is None


# ---------------------------------------------------------------------------
# Steer-target selection order
# ---------------------------------------------------------------------------


def test_target_prefers_live_over_resumable(fake_query):
    fake_query.by_project = [
        _Row("resumable", status="completed", claude_session_uuid="u1"),
        _Row("live", status="running"),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor")
    assert (kind, session.session_id) == ("steer", "live")


def test_target_live_picks_most_recently_updated(fake_query):
    now = time.time()
    fake_query.by_project = [
        _Row("old", status="running", updated_at=now - 900),
        _Row("newest", status="running", updated_at=now),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor")
    assert (kind, session.session_id) == ("steer", "newest")


def test_target_falls_back_to_most_recent_resumable(fake_query):
    now = time.time()
    fake_query.by_project = [
        _Row("older", status="completed", claude_session_uuid="u1", updated_at=now - 900),
        _Row("newer", status="killed", claude_session_uuid="u2", updated_at=now),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor")
    assert (kind, session.session_id) == ("resume", "newer")


def test_target_resumable_without_uuid_is_not_resumable(fake_query):
    """resume_session requires a claude_session_uuid; without one we create."""
    fake_query.by_project = [_Row("no-uuid", status="completed", claude_session_uuid=None)]
    assert sdlc_progress._pick_steer_target("valor") == ("create", None)


def test_target_ledger_anchors_are_never_selected(fake_query):
    fake_query.by_project = [
        _Row("sdlc-local-1395", status="running", is_ledger=True),
        _Row("ledger-terminal", status="completed", is_ledger=True, claude_session_uuid="u1"),
    ]
    assert sdlc_progress._pick_steer_target("valor") == ("create", None)


def test_target_query_is_scoped_to_project_and_eng(fake_query):
    """Other-project / non-eng sessions are excluded at the query, not after it."""
    sdlc_progress._pick_steer_target("valor")
    assert fake_query.calls == [{"project_key": "valor", "session_type": "eng"}]


def test_target_no_sessions_is_the_create_rung(fake_query):
    assert sdlc_progress._pick_steer_target("valor") == ("create", None)


def test_target_query_failure_is_unknown(fake_query):
    fake_query.raise_on.add("project_key")
    assert sdlc_progress._pick_steer_target("valor") == ("unknown", None)


def test_target_prefers_the_stalled_lanes_own_session_over_a_newer_one(fake_query):
    """Resuming another lane's session lands this issue's commits on that lane's branch."""
    now = time.time()
    fake_query.by_project = [
        _Row("other-lane", status="running", slug="sdlc-999", updated_at=now),
        _Row("this-lane", status="running", slug="sdlc-1395", updated_at=now - 3600),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor", lane_slug="sdlc-1395")
    assert (kind, session.session_id) == ("steer", "this-lane")


def test_resume_target_also_prefers_the_stalled_lanes_own_session(fake_query):
    """The preference applies inside the resumable bucket too, not just the live one."""
    now = time.time()
    fake_query.by_project = [
        _Row(
            "other-lane",
            status="completed",
            claude_session_uuid="u1",
            slug="sdlc-999",
            updated_at=now,
        ),
        _Row(
            "this-lane",
            status="completed",
            claude_session_uuid="u2",
            slug="sdlc-1395",
            updated_at=now - 3600,
        ),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor", lane_slug="sdlc-1395")
    assert (kind, session.session_id) == ("resume", "this-lane")


def test_target_falls_back_to_recency_when_no_session_matches_the_lane(fake_query):
    now = time.time()
    fake_query.by_project = [
        _Row("old", status="running", slug="sdlc-1", updated_at=now - 900),
        _Row("newest", status="running", slug=None, updated_at=now),
    ]
    kind, session = sdlc_progress._pick_steer_target("valor", lane_slug="sdlc-1395")
    assert (kind, session.session_id) == ("steer", "newest")


def test_ladder_threads_the_stalled_lanes_slug_into_target_selection(lab, stub_workdir, stalled_pr):
    now = time.time()
    lab.query.by_project = [
        _Row("other-lane", status="running", slug="sdlc-999", updated_at=now),
        _Row("this-lane", status="running", slug="sdlc-1395", updated_at=now - 3600),
    ]

    sdlc_progress._check_project_stalls(_PROJECT)

    assert [sid for sid, _ in lab.steers] == ["this-lane"]


# ---------------------------------------------------------------------------
# Happy path — a steer, and NO Telegram message
# ---------------------------------------------------------------------------


def test_happy_path_steers_and_sends_no_alert(lab, stub_workdir, stalled_pr):
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "ok"
    assert lab.alerts == [], "the happy path must page nobody"
    assert len(lab.steers) == 1
    session_id, message = lab.steers[0]
    assert session_id == "eng-live"
    assert "issue #1395" in message
    assert "/sdlc" in message
    assert "PR #1237" in message
    assert "1 steered" in result["summary"]
    assert lab.attempts() == 1, "a successful steer still charges an attempt"


def test_live_lane_is_suppressed_entirely(lab, stub_workdir, stalled_pr):
    """Gate 5': a live owner holds the lock → no steer, no create, no alert."""
    lab.redis.set(_lock_key(1395), _live_lock_payload())
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])
    assert result["status"] == "ok"
    assert result["findings"] == []


def test_terminal_sessions_are_a_resume_target_not_an_alert(lab, stub_workdir, stalled_pr):
    """UPDATE of the old "only terminal sessions → alert" contract."""
    lab.query.by_project = [_Row("eng-done", status="completed", claude_session_uuid="uuid-1")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.alerts == []
    assert len(lab.resumes) == 1
    session, message, source = lab.resumes[0]
    assert session.session_id == "eng-done"
    assert source == "sdlc-stall"
    assert "issue #1395" in message
    assert "1 resumed" in result["summary"]


def test_no_sessions_at_all_is_the_create_rung(lab, stub_workdir, stalled_pr):
    """REPLACE of "no session → alert": no session is now rung 3."""
    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.alerts == [], "creating a lane is not an escalation"
    assert len(lab.creates) == 1
    kwargs = lab.creates[0]
    assert kwargs["slug"] == "sdlc-1395"
    assert kwargs["session_type"] == "eng"
    assert kwargs["project_key"] == "valor"
    assert "issue #1395" in kwargs["message"]
    assert "1 created" in result["summary"]
    assert lab.attempts() == 1


# ---------------------------------------------------------------------------
# The anti-ladder: one human message per (slug, head-sha), ever
# ---------------------------------------------------------------------------


def test_many_ticks_over_one_sha_produce_exactly_one_human_message(lab, stub_workdir, stalled_pr):
    """The v1 6-hour dedup key re-fired forever. Twelve ticks, one message."""
    lab.query.by_project = [_Row("eng-live", status="running")]

    for _ in range(12):  # 12 simulated hours — twice the old 6-hour cooldown
        sdlc_progress._check_project_stalls(_PROJECT)
        lab.redis.advance(3600)

    assert len(lab.alerts) == 1, f"expected exactly one escalation, got {lab.alerts}"
    assert "auto-resume failed after" in lab.alerts[0]
    assert len(lab.steers) == sdlc_progress._max_attempts()


def test_attempt_budget_exhaustion_escalates_once_then_goes_silent(lab, stub_workdir, stalled_pr):
    lab.redis.set(
        sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456"),
        str(sdlc_progress._max_attempts()),
    )
    lab.query.by_project = [_Row("eng-live", status="running")]

    for _ in range(4):
        sdlc_progress._check_project_stalls(_PROJECT)
        lab.redis.advance(3600)

    assert len(lab.alerts) == 1
    assert "attempt budget exhausted" in lab.alerts[0]
    assert lab.steers == [], "an exhausted budget must stop acting, not keep steering"


def test_escalation_redis_unavailable_sends_nothing(lab, stub_workdir, stalled_pr):
    """UPDATE: retargeted from the deleted alert key to the escalation key.

    Redis unavailable for the ``SET NX`` guard → do not send. Under-alerting
    during a flap beats spamming during one.
    """
    lab.redis.set(
        sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456"),
        str(sdlc_progress._max_attempts()),
    )
    lab.redis.raise_set = True

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.alerts == []


def test_existing_escalation_key_stops_the_ladder_acting(lab, stub_workdir, stalled_pr):
    """ "Escalate once and STOP ACTING" is literal, not just "page once"."""
    lab.redis.set(sdlc_progress._ESCALATED_KEY.format(slug="sdlc-1395", sha="abc123def456"), "1")
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.resumes, lab.creates, lab.alerts) == ([], [], [], [])
    assert lab.attempts() == 0
    assert "already-escalated: sdlc-1395" in result["findings"]


def test_lapsed_attempts_key_cannot_re_arm_the_budget_under_a_live_escalation(
    lab, stub_workdir, stalled_pr
):
    """The regression that made the ladder non-convergent.

    The attempts key used to carry a 24h TTL while the escalation key carried
    30 days, so once the budget lapsed the ladder dispatched a fresh
    MAX_ATTEMPTS actions per day for a month behind one silent SET NX.
    """
    lab.query.by_project = [_Row("eng-live", status="running")]

    for _ in range(4):  # exhaust the budget and escalate
        sdlc_progress._check_project_stalls(_PROJECT)
        lab.redis.advance(3600)

    assert len(lab.alerts) == 1
    dispatched_before = len(lab.steers)

    lab.redis.advance(48 * 3600)  # past the OLD 24h attempts TTL
    for _ in range(3):
        sdlc_progress._check_project_stalls(_PROJECT)
        lab.redis.advance(3600)

    assert len(lab.steers) == dispatched_before, "the ladder must not re-arm"
    assert len(lab.alerts) == 1


def test_attempts_ttl_is_never_shorter_than_the_escalation_ttl(monkeypatch):
    """The invariant, asserted directly rather than only through behavior."""
    monkeypatch.setenv("SDLC_STALL_ATTEMPTS_TTL_DAYS", "0.5")
    assert sdlc_progress._attempts_ttl_seconds() >= sdlc_progress._escalation_ttl_seconds()


def test_escalation_read_failure_declines_without_acting(lab, stub_workdir, stalled_pr):
    """Blind on the anti-ladder key means do not act."""
    esc_key = sdlc_progress._ESCALATED_KEY.format(slug="sdlc-1395", sha="abc123def456")
    lab.redis.raise_get = lambda key: key == esc_key
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])
    assert "gate-unknown: escalation-read sdlc-1395" in result["findings"]


def test_corrupt_attempts_payload_is_unknown_not_under_budget(lab, stub_workdir, stalled_pr):
    """A payload we cannot parse must not read as "0 attempts, go ahead"."""
    lab.redis.set(
        sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456"), "not-a-number"
    )
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert sdlc_progress._attempts_count("sdlc-1395", "abc123def456") is None
    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])
    assert "gate-unknown: attempts-read sdlc-1395" in result["findings"]


def test_cooldown_skip_never_charges_an_attempt(lab, stub_workdir, stalled_pr):
    """Read-vs-charge regression guard: the pre-action gate is a GET, not an INCR."""
    attempts_key = sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456")
    lab.redis.set(sdlc_progress._COOLDOWN_KEY.format(slug="sdlc-1395", sha="abc123def456"), "1")
    lab.query.by_project = [_Row("eng-live", status="running")]

    for _ in range(6):
        sdlc_progress._check_project_stalls(_PROJECT)

    assert attempts_key not in lab.redis.keys_present()
    assert lab.attempts() == 0
    assert lab.alerts == []
    assert lab.steers == []


# ---------------------------------------------------------------------------
# Failure classification: benign races vs. real dead ends
# ---------------------------------------------------------------------------


def test_steer_failure_always_charges_an_attempt_and_escalates(lab, stub_workdir, stalled_pr):
    """No benign-race branch on the steer rung, deliberately."""
    lab.query.by_project = [_Row("eng-live", status="running")]
    lab.query.by_session_id = [_Row("eng-live", status="running")]
    lab.steer_result = {"success": False, "session_id": "eng-live", "error": "boom"}

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 1
    assert len(lab.alerts) == 1
    assert "steer failed" in lab.alerts[0]


def test_steer_raising_is_still_a_charged_failure(lab, stub_workdir, stalled_pr):
    lab.query.by_project = [_Row("eng-live", status="running")]
    lab.steer_raises = True

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 1
    assert len(lab.alerts) == 1


def test_resume_failure_with_terminal_row_charges_and_escalates(lab, stub_workdir, stalled_pr):
    lab.query.by_project = [_Row("eng-done", status="completed", claude_session_uuid="u1")]
    lab.query.by_session_id = [_Row("eng-done", status="completed")]
    lab.resume_result = _Result(False, "transition lost")

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 1
    assert len(lab.alerts) == 1
    assert "resume failed" in lab.alerts[0]


def test_resume_failure_with_nonterminal_row_is_a_benign_race(lab, stub_workdir, stalled_pr):
    """crash_recovery got there first: no attempt, no escalation, a findings marker."""
    lab.query.by_project = [_Row("eng-done", status="completed", claude_session_uuid="u1")]
    lab.query.by_session_id = [_Row("eng-done", status="running")]
    lab.resume_result = _Result(False, "already pending")

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 0
    assert lab.alerts == []
    assert "benign-race: resume sdlc-1395" in result["findings"]


def test_create_failure_charges_and_escalates(lab, stub_workdir, stalled_pr):
    lab.create_result = _Result(False, "worktree provisioning failed")

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 1
    assert len(lab.alerts) == 1
    assert "create failed" in lab.alerts[0]


def test_create_raising_charges_and_escalates(lab, stub_workdir, stalled_pr):
    lab.create_raises = True

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.attempts() == 1
    assert len(lab.alerts) == 1


# ---------------------------------------------------------------------------
# The create rung's own guards
# ---------------------------------------------------------------------------


def _lock_becomes_live_after_first_read(redis: _FakeRedis, issue_number: int):
    """Gate 5' sees a free lock; the pre-create re-read sees a live one."""
    key = _lock_key(issue_number)
    state = {"reads": 0}

    def hook(k):
        if k != key:
            return _PASS
        state["reads"] += 1
        return None if state["reads"] == 1 else _live_lock_payload()

    redis.get_hook = hook
    return state


def test_create_lock_reread_live_owner_is_benign(lab, stub_workdir, stalled_pr):
    """The lane re-took its lock between gate 5' and create → no lane on top of a lane."""
    _lock_becomes_live_after_first_read(lab.redis, 1395)

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.creates == []
    assert lab.attempts() == 0
    assert lab.alerts == []
    assert "benign-race: create sdlc-1395" in result["findings"]


def test_create_lock_reread_unknown_declines(lab, stub_workdir, stalled_pr):
    """An unknown re-read declines: no create, no attempt, no escalation, a marker."""
    key = _lock_key(1395)
    state = {"reads": 0}

    def raise_get(k):
        if k != key:
            return False
        state["reads"] += 1
        return state["reads"] > 1

    lab.redis.raise_get = raise_get

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.creates == []
    assert lab.attempts() == 0
    assert lab.alerts == []
    assert any("create-lock-reread" in f for f in result["findings"])


def test_create_brake_caps_creations_per_project_per_tick(lab, stub_workdir, monkeypatch):
    """Two stalled lanes both falling to create → only the budgeted number is created."""
    now = int(time.time())
    monkeypatch.setattr(
        sdlc_progress,
        "_list_open_sdlc_prs",
        lambda cwd: [
            _pr(number=1, branch="session/sdlc-100"),
            _pr(number=2, branch="session/sdlc-200"),
        ],
    )
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(
        sdlc_progress,
        "_last_commit",
        lambda cwd, branch: (f"sha-{branch[-3:]}", now - 9 * 3600),
    )

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert len(lab.creates) == sdlc_progress._create_max_per_tick() == 1
    assert lab.creates[0]["slug"] == "sdlc-100"
    assert any("create-brake" in f for f in result["findings"])
    assert lab.alerts == [], "a deferred create is not an escalation"


# ---------------------------------------------------------------------------
# Action-window release — a tick that dispatched nothing must not burn the hour
#
# The cooldown is claimed BEFORE rung selection because it doubles as the
# overlapping-tick guard. Paths that then bail without dispatching anything
# have to hand the window back, or a lane the plan says "waits for the next
# tick" actually waits a full SDLC_STALL_RESUME_COOLDOWN_HOURS.
# ---------------------------------------------------------------------------


def _cooldown_key(slug: str, sha: str) -> str:
    return sdlc_progress._COOLDOWN_KEY.format(slug=slug, sha=sha)


def test_create_brake_releases_the_deferred_lanes_action_window(lab, stub_workdir, monkeypatch):
    """The braked lane must be actionable on the very next tick, not an hour later."""
    now = int(time.time())
    monkeypatch.setattr(
        sdlc_progress,
        "_list_open_sdlc_prs",
        lambda cwd: [
            _pr(number=1, branch="session/sdlc-100"),
            _pr(number=2, branch="session/sdlc-200"),
        ],
    )
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(
        sdlc_progress,
        "_last_commit",
        lambda cwd, branch: (f"sha-{branch[-3:]}", now - 9 * 3600),
    )

    sdlc_progress._check_project_stalls(_PROJECT)

    assert [c["slug"] for c in lab.creates] == ["sdlc-100"]
    assert _cooldown_key("sdlc-100", "sha-100") in lab.redis.keys_present()
    assert _cooldown_key("sdlc-200", "sha-200") not in lab.redis.keys_present()

    # Next tick, clock untouched: lane 1 is still on cooldown (so it does not
    # eat the create budget), lane 2 acts.
    sdlc_progress._check_project_stalls(_PROJECT)

    assert [c["slug"] for c in lab.creates] == ["sdlc-100", "sdlc-200"]


def test_target_query_failure_releases_the_action_window(lab, stub_workdir, stalled_pr):
    """Unknown target → no action, no escalation, marker kept, window handed back."""
    lab.query.raise_on.add("project_key")

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.resumes, lab.creates, lab.alerts) == ([], [], [], [])
    assert "gate-unknown: target-query sdlc-1395" in result["findings"]
    assert _cooldown_key("sdlc-1395", "abc123def456") not in lab.redis.keys_present()


def test_declined_create_lock_reread_releases_the_action_window(lab, stub_workdir, stalled_pr):
    """A declined rung dispatched nothing, so it owes the window back too."""
    key = _lock_key(1395)
    state = {"reads": 0}

    def raise_get(k):
        if k != key:
            return False
        state["reads"] += 1
        return state["reads"] > 1

    lab.redis.raise_get = raise_get

    sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.creates == []
    assert _cooldown_key("sdlc-1395", "abc123def456") not in lab.redis.keys_present()


def test_predispatch_benign_race_releases_the_action_window(lab, stub_workdir, stalled_pr):
    """The create rung's pre-create race sent nothing, so it owes the hour back."""
    _lock_becomes_live_after_first_read(lab.redis, 1395)

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert lab.creates == []
    assert "benign-race: create sdlc-1395" in result["findings"]
    assert _cooldown_key("sdlc-1395", "abc123def456") not in lab.redis.keys_present()


def test_dispatched_benign_race_keeps_the_action_window(lab, stub_workdir, stalled_pr):
    """A resume race DID push a steering message — that hour is legitimately spent."""
    lab.query.by_project = [_Row("eng-done", status="completed", claude_session_uuid="u1")]
    lab.query.by_session_id = [_Row("eng-done", status="running")]
    lab.resume_result = _Result(False, "already pending")

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert "benign-race: resume sdlc-1395" in result["findings"]
    assert _cooldown_key("sdlc-1395", "abc123def456") in lab.redis.keys_present()


@pytest.mark.parametrize("succeeds", [True, False])
def test_dispatched_action_keeps_the_action_window(lab, stub_workdir, stalled_pr, succeeds):
    """Success or real failure, something happened — the hour is legitimately spent."""
    lab.query.by_project = [_Row("eng-live", status="running")]
    lab.query.by_session_id = [_Row("eng-live", status="running")]
    lab.steer_result = {"success": succeeds, "session_id": "eng-live", "error": None}

    sdlc_progress._check_project_stalls(_PROJECT)

    assert len(lab.steers) == 1
    assert _cooldown_key("sdlc-1395", "abc123def456") in lab.redis.keys_present()


def test_release_failure_is_swallowed(lab, stub_workdir, stalled_pr):
    """Redis dying on the DELETE degrades to "window stays claimed", never a raise."""
    lab.query.raise_on.add("project_key")
    lab.redis.raise_delete = True

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "ok"
    assert "gate-unknown: target-query sdlc-1395" in result["findings"]


# ---------------------------------------------------------------------------
# Gate 6' — single-machine ownership
# ---------------------------------------------------------------------------


def test_non_owner_machine_takes_no_action(lab, stub_workdir, stalled_pr, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "machine_owns_project", lambda key: False)
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "skipped"
    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])


def test_ownership_check_raising_is_treated_as_not_owner(
    lab, stub_workdir, stalled_pr, monkeypatch
):
    def _boom(key):
        raise RuntimeError("projects.json unreadable")

    monkeypatch.setattr(sdlc_progress, "machine_owns_project", _boom)

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "skipped"
    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])


# ---------------------------------------------------------------------------
# Degradation markers — unknown must not read like a healthy quiet tick
# ---------------------------------------------------------------------------


def test_lock_read_failure_marks_findings_and_acts_on_nothing(lab, stub_workdir, stalled_pr):
    lab.redis.raise_get = lambda key: key == _lock_key(1395)

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "ok"
    assert (lab.steers, lab.resumes, lab.creates, lab.alerts) == ([], [], [], [])
    assert "gate-unknown: lock-read sdlc-1395" in result["findings"]


def test_degradation_marker_distinguishes_from_a_healthy_zero_stall_tick(
    lab, stub_workdir, stalled_pr, monkeypatch
):
    lab.redis.raise_get = lambda key: key == _lock_key(1395)
    degraded = sdlc_progress._check_project_stalls(_PROJECT)

    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    healthy = sdlc_progress._check_project_stalls(_PROJECT)

    assert healthy["findings"] == []
    assert degraded["findings"] != healthy["findings"]
    assert any(f.startswith("gate-unknown:") for f in degraded["findings"])


def test_unknown_issue_state_marks_findings(lab, stub_workdir, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: None)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, b: ("sha", now - 9 * 3600))

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert "gate-unknown: issue-state sdlc-1395" in result["findings"]
    assert lab.alerts == []


def test_attempts_read_failure_declines_without_escalating(lab, stub_workdir, stalled_pr):
    """Cannot prove we are under budget → decline; and never escalate on a read failure."""
    attempts_key = sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456")
    lab.redis.raise_get = lambda key: key == attempts_key
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.creates, lab.alerts) == ([], [], [])
    assert "gate-unknown: attempts-read sdlc-1395" in result["findings"]


def test_attempts_incr_failure_marks_but_does_not_escalate(lab, stub_workdir, stalled_pr):
    """A charge failure cannot un-dispatch the action; log it and continue."""
    lab.redis.raise_incr = True
    lab.query.by_project = [_Row("eng-live", status="running")]

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert len(lab.steers) == 1, "the action was already dispatched"
    assert lab.alerts == []
    assert "charge-failed: attempts-incr sdlc-1395" in result["findings"]


def test_target_query_failure_takes_no_action(lab, stub_workdir, stalled_pr):
    lab.query.raise_on.add("project_key")

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.resumes, lab.creates, lab.alerts) == ([], [], [], [])
    assert "gate-unknown: target-query sdlc-1395" in result["findings"]


def test_valor_telegram_missing_does_not_break_the_tick(lab, stub_workdir, stalled_pr, monkeypatch):
    """The escalation path swallows a missing CLI and the reflection still returns ok."""
    # Put the REAL _send_alert back so the subprocess boundary is the thing under test.
    monkeypatch.setattr(sdlc_progress, "_send_alert", _REAL_SEND_ALERT)
    lab.redis.set(
        sdlc_progress._ATTEMPTS_KEY.format(slug="sdlc-1395", sha="abc123def456"),
        str(sdlc_progress._max_attempts()),
    )
    monkeypatch.setattr(
        sdlc_progress.subprocess, "run", MagicMock(side_effect=FileNotFoundError("valor-telegram"))
    )

    result = sdlc_progress._check_project_stalls(_PROJECT)
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Break-glass
# ---------------------------------------------------------------------------


def test_resume_disabled_escalates_once_and_acts_on_nothing(
    lab, stub_workdir, stalled_pr, monkeypatch
):
    monkeypatch.setenv("SDLC_STALL_RESUME_ENABLED", "false")
    lab.query.by_project = [_Row("eng-live", status="running")]

    sdlc_progress._check_project_stalls(_PROJECT)
    lab.redis.advance(3600)
    sdlc_progress._check_project_stalls(_PROJECT)

    assert (lab.steers, lab.creates) == ([], [])
    assert len(lab.alerts) == 1
    assert "auto-resume disabled" in lab.alerts[0]


# ---------------------------------------------------------------------------
# Gates 1-4 — unchanged contracts that must keep passing
# ---------------------------------------------------------------------------


def test_draft_prs_not_flagged(lab, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    sdlc_progress._check_project_stalls(_PROJECT)
    assert (lab.alerts, lab.steers, lab.creates) == ([], [], [])


def test_closed_issue_skipped(lab, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: False)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda *a: ("x", 0))
    sdlc_progress._check_project_stalls(_PROJECT)
    assert (lab.alerts, lab.steers, lab.creates) == ([], [], [])


def test_missing_local_branch_silently_skipped(lab, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: None)
    result = sdlc_progress._check_project_stalls(_PROJECT)
    assert (lab.alerts, lab.steers, lab.creates) == ([], [], [])
    assert result["findings"] == []


def test_fresh_commit_is_not_a_stall(lab, stub_workdir, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, b: ("sha", now - 600))
    sdlc_progress._check_project_stalls(_PROJECT)
    assert (lab.alerts, lab.steers, lab.creates) == ([], [], [])


# ---------------------------------------------------------------------------
# Subprocess failure tolerance — unchanged
# ---------------------------------------------------------------------------


def test_gh_pr_list_filenotfound_returns_empty(monkeypatch):
    monkeypatch.setattr(
        sdlc_progress.subprocess, "run", MagicMock(side_effect=FileNotFoundError("gh"))
    )
    assert sdlc_progress._list_open_sdlc_prs("/tmp") == []


def test_gh_pr_list_filters_non_sdlc_branches(monkeypatch):
    payload = json.dumps(
        [
            _pr(branch="session/sdlc-1395"),
            _pr(branch="session/some-feature"),
            _pr(branch="dependabot/update"),
            _pr(branch="session/sdlc-9999", draft=True),  # draft → excluded
        ]
    )
    monkeypatch.setattr(
        sdlc_progress, "_run_gh", lambda args, cwd, timeout=20: _FakeProc(stdout=payload)
    )
    out = sdlc_progress._list_open_sdlc_prs("/tmp")
    assert len(out) == 1
    assert out[0]["headRefName"] == "session/sdlc-1395"


def test_git_log_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        sdlc_progress.subprocess,
        "run",
        MagicMock(return_value=_FakeProc(returncode=128, stdout="", stderr="bad ref")),
    )
    assert sdlc_progress._last_commit("/tmp", "session/sdlc-1") is None


def test_send_alert_swallows_filenotfound(monkeypatch):
    monkeypatch.setattr(
        sdlc_progress.subprocess, "run", MagicMock(side_effect=FileNotFoundError("valor-telegram"))
    )
    sdlc_progress._send_alert("hello")  # must not raise


# ---------------------------------------------------------------------------
# Return shape contract — unchanged
# ---------------------------------------------------------------------------


def test_check_project_returns_canonical_shape(lab, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    result = sdlc_progress._check_project_stalls(_PROJECT)
    assert set(result.keys()) >= {"status", "findings", "summary", "duration"}
    assert isinstance(result["findings"], list)
    assert isinstance(result["duration"], float)


def test_summary_carries_per_rung_counters(lab, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    summary = sdlc_progress._check_project_stalls(_PROJECT)["summary"]
    for rung in ("steered", "resumed", "created", "escalated"):
        assert rung in summary


def test_subprocess_cwd_is_working_directory(lab, stub_workdir, monkeypatch):
    """All gh/git invocations must run inside the project's working_directory."""
    captured: list[dict] = []

    def fake_run(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and cmd[0] == "gh" and "pr" in cmd:
            return _FakeProc(stdout="[]")
        return _FakeProc(returncode=1)

    monkeypatch.setattr(sdlc_progress.subprocess, "run", fake_run)
    sdlc_progress._check_project_stalls(_PROJECT)
    gh_calls = [c for c in captured if c["args"][0][0] == "gh"]
    assert gh_calls, "expected at least one gh invocation"
    assert all(c["kwargs"].get("cwd") == "/tmp/fake-valor-repo" for c in gh_calls)


def test_no_working_directory_returns_skipped():
    result = sdlc_progress._check_project_stalls({"slug": "x", "working_directory": ""})
    assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Public entrypoint smoke
# ---------------------------------------------------------------------------


def test_run_sdlc_progress_check_uses_per_project_helper(lab, monkeypatch):
    """Fenced: patch the name ``run_per_project_audit`` really resolves.

    ``sdlc_progress`` imports ``run_per_project_audit``, not
    ``load_local_projects`` — patching the latter on this module with
    ``raising=False`` would silently no-op and let the entrypoint scan every
    real project with live dispatch functions attached.
    """
    monkeypatch.setattr("reflections.utilities.load_local_projects", lambda: [])

    out = sdlc_progress.run_sdlc_progress_check()

    assert out["status"] in {"ok", "disabled"}
    assert "summary" in out
    assert (lab.steers, lab.resumes, lab.creates, lab.alerts) == ([], [], [], [])
