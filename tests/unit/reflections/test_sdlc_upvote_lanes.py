"""Unit tests for reflections/sdlc_upvote_lanes.py (issue #2717).

The start-half sibling to reflections/sdlc_progress.py. What is faked here,
and why:

* `subprocess.run` inside the module is captured so `gh` / `valor-telegram
  send` argv can be asserted structurally (not just their return values).
* The module's own private per-gate helpers (`_non_terminal_session_for`,
  `_failed_backoff_active`, `_recent_terminal_failed_session`,
  `_ledger_has_recorded_stage`, `_has_pr_on_branch`, `_count_live_lanes`) are
  monkeypatched directly -- these are exactly the seams the module's own
  design intends as independently testable units, mirroring how
  `test_sdlc_progress_check.py` fences `AgentSession.query` at the boundary
  rather than re-testing Popoto.
* `create_session` and `await_sent_message_id` are patched at their *source*
  modules (`tools.valor_session`, `bridge.outbox_ack`) because the reflection
  imports them lazily inside the function body -- patching the source
  attribute is what a late `from X import Y` actually resolves at call time.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from reflections import sdlc_upvote_lanes as m

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _project(slug="valor", chat_id=-1003449100931, has_eng_group=True, machine_owned=True):
    groups = {"Eng: Valor": {"chat_id": chat_id, "persona": "engineer"}} if has_eng_group else {}
    return {
        "slug": slug,
        "working_directory": "/tmp/fake-valor-repo",
        "github": {"org": "tomcounsell", "repo": "ai"},
        "telegram": {"groups": groups},
    }


def _issue(number, created_at, title="thing"):
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/tomcounsell/ai/issues/{number}",
        "createdAt": created_at,
    }


class _Lab:
    """Bundles the common patches every gate test needs, with sane 'proceed'
    defaults so a test only overrides the one gate under test."""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.send_calls: list[list[str]] = []
        self.send_envs: list[dict] = []
        self.send_rc = 0
        self.ack_value: int | None = 999
        self.create_result = MagicMock(success=True, error=None)
        self.create_calls: list[dict] = []

        monkeypatch.setattr(m, "machine_owns_project", lambda key: True)
        monkeypatch.setattr(m, "resolve_eng_group", lambda p: ("Eng: Valor", -100123))
        monkeypatch.setattr(m, "_project_repo", lambda p: "tomcounsell/ai")
        monkeypatch.setattr(m, "_count_live_lanes", lambda repo, cwd: 0)
        monkeypatch.setattr(m, "_non_terminal_session_for", lambda slug, pk: (None, False))
        monkeypatch.setattr(m, "_failed_backoff_active", lambda repo, n: False)
        monkeypatch.setattr(m, "_recent_terminal_failed_session", lambda slug, pk, s: None)
        monkeypatch.setattr(m, "_ledger_has_recorded_stage", lambda repo, n: False)
        monkeypatch.setattr(m, "_lock_says_live", lambda n: False)
        monkeypatch.setattr(m, "_has_pr_on_branch", lambda repo, n, cwd: None)

        def fake_run_send(argv):
            self.send_calls.append(list(argv))
            return self.send_rc

        monkeypatch.setattr(m, "_run_send", fake_run_send)

        def fake_await(producer_id, timeout_s):
            return self.ack_value

        monkeypatch.setattr("bridge.outbox_ack.await_sent_message_id", fake_await)

        def fake_create_session(**kwargs):
            self.create_calls.append(kwargs)
            return self.create_result

        monkeypatch.setattr("tools.valor_session.create_session", fake_create_session)


# ---------------------------------------------------------------------------
# Entry point / top-level gates
# ---------------------------------------------------------------------------


class TestEntryPoint:
    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("SDLC_UPVOTE_PICKUP_ENABLED", "false")
        result = m.run_sdlc_upvote_lanes()
        assert result["status"] == "disabled"

    def test_zero_upvote_issues_is_ok_with_no_session(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr("reflections.utilities.load_local_projects", lambda: [_project()])
        monkeypatch.setattr(m, "_gh_issue_list", lambda *a, **k: [])
        result = m.run_sdlc_upvote_lanes()
        assert result["status"] == "ok"
        assert lab.create_calls == []


class TestScopeGates:
    def test_non_owned_project_skips_before_any_subprocess(self, monkeypatch):
        monkeypatch.setattr(m, "machine_owns_project", lambda key: False)
        run_calls = []
        monkeypatch.setattr(
            m.subprocess, "run", lambda *a, **k: run_calls.append((a, k)) or MagicMock()
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 100))
        assert result["status"] == "skipped"
        assert run_calls == []

    def test_no_eng_group_is_ok_not_skipped(self, monkeypatch):
        # status must be "ok", not "skipped" -- reflections/utilities.py:207
        # drops findings on "skipped", and a missing Eng: group is a real
        # config gap the operator needs surfaced, not a legitimate no-op.
        monkeypatch.setattr(m, "machine_owns_project", lambda key: True)
        monkeypatch.setattr(m, "resolve_eng_group", lambda p: None)
        result = m._pick_up_upvoted(
            _project(has_eng_group=False), state=m._RunState(deadline=time.monotonic() + 100)
        )
        assert result["status"] == "ok"
        assert "no Eng:" in result["findings"][0]

    def test_no_repo_configured_is_ok_not_skipped(self, monkeypatch):
        monkeypatch.setattr(m, "machine_owns_project", lambda key: True)
        monkeypatch.setattr(m, "resolve_eng_group", lambda p: ("Eng: Valor", -100123))
        monkeypatch.setattr(m, "_project_repo", lambda p: None)
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 100))
        assert result["status"] == "ok"
        assert "no github.org/repo configured" in result["findings"][0]

    def test_config_gap_findings_reach_aggregated_report(self, monkeypatch):
        # Regression for the "findings dropped on skipped status" tech debt:
        # a config-gap project's finding must survive
        # reflections.utilities.run_per_project_audit's aggregation, not just
        # the per-project return value.
        from reflections.utilities import run_per_project_audit

        monkeypatch.setattr(
            "reflections.utilities.load_local_projects",
            lambda: [_project(slug="gappy", has_eng_group=False)],
        )
        monkeypatch.setattr(m, "machine_owns_project", lambda key: True)
        monkeypatch.setattr(m, "resolve_eng_group", lambda p: None)

        aggregated = run_per_project_audit(
            lambda project: m._pick_up_upvoted(
                project, state=m._RunState(deadline=time.monotonic() + 100)
            ),
            name="sdlc-upvote-pickup",
        )
        assert aggregated["status"] == "ok"
        assert any("no Eng: group configured" in f for f in aggregated["findings"])
        assert aggregated["projects"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Ordering (§A) -- must use a fixture larger than the page size
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_picks_true_oldest_from_oversized_page(self, monkeypatch):
        _Lab(monkeypatch)
        # Fixture larger than the scan cap: a 3-item fixture cannot detect a
        # client-side-sort-of-truncated-page defect.
        issues = [_issue(100 + i, f"2026-08-{10 - i:02d}T00:00:00Z") for i in range(15)]
        # gh itself would already return these server-side oldest-first and
        # truncated to the scan cap; simulate that contract here.
        server_sorted = sorted(issues, key=lambda i: i["createdAt"])[: m.UPVOTE_CANDIDATE_SCAN_MAX]
        captured_argv = {}

        def fake_gh_issue_list(repo, labels, cwd, limit, extra_args=None, timeout=None):
            captured_argv["extra_args"] = extra_args
            captured_argv["limit"] = limit
            return server_sorted

        monkeypatch.setattr(m, "_gh_issue_list", fake_gh_issue_list)
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))

        assert captured_argv["extra_args"] == ["--search", "sort:created-asc"]
        assert captured_argv["limit"] == m.UPVOTE_CANDIDATE_SCAN_MAX
        assert any("started SDLC lane for issue #" in f for f in result["findings"])
        started_issue = next(f for f in result["findings"] if "started SDLC lane" in f)
        # The true oldest issue (index 14, 2026-07-27) must be the one picked.
        assert f"#{server_sorted[0]['number']}" in started_issue


# ---------------------------------------------------------------------------
# Skip gates (§C)
# ---------------------------------------------------------------------------


class TestSkipGates:
    def _run(self, monkeypatch, issues, **overrides):
        lab = _Lab(monkeypatch)
        for attr, val in overrides.items():
            monkeypatch.setattr(m, attr, val)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: issues if isinstance(issues, list) else issues
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        return lab, result

    def test_gate1_non_terminal_session_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _non_terminal_session_for=lambda slug, pk: (MagicMock(), False),
        )
        assert lab.create_calls == []
        assert not any("started SDLC lane" in f for f in result["findings"])

    def test_gate1_cross_project_collision_reports_and_proceeds(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _non_terminal_session_for=lambda slug, pk: (None, True),
        )
        assert lab.create_calls  # proceeded to start
        assert any("cross-project slug collision" in f for f in result["findings"])

    def test_gate1_5_failed_backoff_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _failed_backoff_active=lambda repo, n: True,
        )
        assert lab.create_calls == []

    def test_gate1_6_recent_terminal_failed_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _recent_terminal_failed_session=lambda slug, pk, s: MagicMock(),
        )
        assert lab.create_calls == []

    def test_gate2_ledger_recorded_stage_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _ledger_has_recorded_stage=lambda repo, n: True,
        )
        assert lab.create_calls == []

    def test_gate3_lock_live_true_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch, [_issue(1, "2026-08-01T00:00:00Z")], _lock_says_live=lambda n: True
        )
        assert lab.create_calls == []

    def test_gate3_lock_unknown_skips_fail_closed(self, monkeypatch):
        lab, result = self._run(
            monkeypatch, [_issue(1, "2026-08-01T00:00:00Z")], _lock_says_live=lambda n: None
        )
        assert lab.create_calls == []

    def test_gate4_open_pr_skips(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _has_pr_on_branch=lambda repo, n, cwd: {"number": 5, "state": "OPEN"},
        )
        assert lab.create_calls == []

    def test_gate4_merged_pr_skips_and_reports(self, monkeypatch):
        lab, result = self._run(
            monkeypatch,
            [_issue(1, "2026-08-01T00:00:00Z")],
            _has_pr_on_branch=lambda repo, n, cwd: {"number": 5, "state": "MERGED"},
        )
        assert lab.create_calls == []
        assert any("merged PR" in f for f in result["findings"])


# ---------------------------------------------------------------------------
# Gate helpers exercised directly (not through _pick_up_upvoted) --
# `_Lab` monkeypatches both `_recent_terminal_failed_session` and
# `_non_terminal_session_for` away for every other test in this file, so
# neither function's real body runs anywhere else. This is what caught the
# naive/aware datetime TypeError (issue #2717 review): Popoto decodes
# `AgentSession.created_at` as a naive datetime (popoto/models/encoding.py),
# and `_recent_terminal_failed_session` used to compare it directly against
# a tz-aware `cutoff`, raising `TypeError: can't compare offset-naive and
# offset-aware datetimes` -- which `run_per_project_audit`'s per-project
# try/except swallowed, marking the whole project "error" every tick.
# ---------------------------------------------------------------------------


class _GateFakeQuery:
    """``AgentSession.query`` stand-in routed by the ``slug`` filter kwarg,
    mirroring the ``_FakeQuery`` pattern in test_sdlc_progress_check.py."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def filter(self, **kw):
        self.calls.append(dict(kw))
        return list(self.rows)


def _gate_row(project_key="valor", status="failed", created_at=None):
    row = MagicMock()
    row.project_key = project_key
    row.status = status
    row.created_at = created_at
    return row


@pytest.fixture
def fake_agent_session(monkeypatch):
    """Patches `models.agent_session.AgentSession` with a MagicMock whose
    `.query` is swappable per-test via `.query = _GateFakeQuery(rows)`."""
    session_cls = MagicMock()
    monkeypatch.setattr("models.agent_session.AgentSession", session_cls)
    return session_cls


class TestRecentTerminalFailedSessionDirect:
    """Direct tests of `_recent_terminal_failed_session` -- no monkeypatch of
    the function itself, exercising the real naive/aware comparison."""

    def test_naive_created_at_within_backoff_matches(self, fake_agent_session):
        from datetime import UTC, datetime

        # This is literally what Popoto returns: naive, no tzinfo -- but
        # representing UTC wall-clock time, per bridge.utc.to_unix_ts's
        # documented contract ("Popoto strips tzinfo on save").
        recent_naive = datetime.now(UTC).replace(tzinfo=None, microsecond=469935)
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="valor", status="failed", created_at=recent_naive)]
        )

        result = m._recent_terminal_failed_session("sdlc-1", "valor", backoff_s=3600)

        assert result is not None

    def test_naive_created_at_outside_backoff_does_not_match(self, fake_agent_session):
        from datetime import UTC, datetime, timedelta

        old_naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="valor", status="failed", created_at=old_naive)]
        )

        result = m._recent_terminal_failed_session("sdlc-1", "valor", backoff_s=3600)

        assert result is None

    def test_wrong_project_key_does_not_match(self, fake_agent_session):
        from datetime import UTC, datetime

        recent_naive = datetime.now(UTC).replace(tzinfo=None)
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="other-project", status="failed", created_at=recent_naive)]
        )

        result = m._recent_terminal_failed_session("sdlc-1", "valor", backoff_s=3600)

        assert result is None

    def test_wrong_status_does_not_match(self, fake_agent_session):
        from datetime import UTC, datetime

        recent_naive = datetime.now(UTC).replace(tzinfo=None)
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="valor", status="running", created_at=recent_naive)]
        )

        result = m._recent_terminal_failed_session("sdlc-1", "valor", backoff_s=3600)

        assert result is None


class TestNonTerminalSessionForDirect:
    """Direct tests of `_non_terminal_session_for` -- no monkeypatch of the
    function itself."""

    def test_own_project_non_terminal_match(self, fake_agent_session):
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="valor", status="running")]
        )

        own_match, cross_project = m._non_terminal_session_for("sdlc-1", "valor")

        assert own_match is not None
        assert cross_project is False

    def test_cross_project_match_sets_flag_not_own_match(self, fake_agent_session):
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="other-project", status="running")]
        )

        own_match, cross_project = m._non_terminal_session_for("sdlc-1", "valor")

        assert own_match is None
        assert cross_project is True

    def test_terminal_status_excluded(self, fake_agent_session):
        fake_agent_session.query = _GateFakeQuery(
            [_gate_row(project_key="valor", status="completed")]
        )

        own_match, cross_project = m._non_terminal_session_for("sdlc-1", "valor")

        assert own_match is None
        assert cross_project is False


# ---------------------------------------------------------------------------
# Concurrency ceilings (§B)
# ---------------------------------------------------------------------------


class TestCeilings:
    def test_per_project_ceiling_blocks_start(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr(m, "_count_live_lanes", lambda repo, cwd: m.UPVOTE_LANE_MAX_LIVE)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert lab.create_calls == []
        assert any("lane ceiling reached" in f for f in result["findings"])

    def test_machine_wide_ceiling_blocks_start_no_extra_gh_calls(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr(m, "_count_live_lanes", lambda repo, cwd: 2)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        state = m._RunState(deadline=time.monotonic() + 1000)
        state.machine_live_total = m.UPVOTE_LANE_MAX_LIVE_MACHINE - 1
        result = m._pick_up_upvoted(_project(), state=state)
        assert lab.create_calls == []
        assert any("machine-wide lane ceiling reached" in f for f in result["findings"])


# ---------------------------------------------------------------------------
# Anchor happy path / timeout / create failure / retraction
# ---------------------------------------------------------------------------


class TestAnchorAndCreate:
    def test_happy_path_creates_anchored_session(self, monkeypatch):
        lab = _Lab(monkeypatch)
        lab.ack_value = 4242
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert len(lab.create_calls) == 1
        assert lab.create_calls[0]["telegram_message_id"] == 4242
        assert lab.create_calls[0]["chat_id"] == "-100123"
        assert lab.create_calls[0]["slug"] == "sdlc-1"
        assert any("started SDLC lane" in f for f in result["findings"])

    def test_anchor_timeout_still_starts_unanchored_with_finding(self, monkeypatch):
        lab = _Lab(monkeypatch)
        lab.ack_value = None
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert len(lab.create_calls) == 1
        assert lab.create_calls[0]["telegram_message_id"] == 0
        assert any("delivery unconfirmed" in f for f in result["findings"])

    def test_announce_failure_no_create_no_finding_of_success(self, monkeypatch):
        lab = _Lab(monkeypatch)
        lab.send_rc = 1
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert lab.create_calls == []
        assert any("announcement send failed" in f for f in result["findings"])

    def test_create_failure_writes_backoff_and_retracts_with_reply_to(self, monkeypatch):
        lab = _Lab(monkeypatch)
        lab.ack_value = 555
        lab.create_result = MagicMock(success=False, error="boom")
        backoff_writes = []
        monkeypatch.setattr(
            m, "_set_failed_backoff", lambda repo, n, err: backoff_writes.append((repo, n, err))
        )
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))

        assert backoff_writes and backoff_writes[0][:2] == ("tomcounsell/ai", 1)
        # Two sends: the announcement, then the retraction, and the
        # retraction must carry --reply-to since anchor (555) was truthy.
        assert len(lab.send_calls) == 2
        retraction_argv = lab.send_calls[1]
        assert "--reply-to" in retraction_argv
        assert retraction_argv[retraction_argv.index("--reply-to") + 1] == "555"
        assert any("create_session failed" in f for f in result["findings"])

    def test_create_failure_with_unconfirmed_anchor_omits_reply_to(self, monkeypatch):
        """Both degradations co-occurring (cycle-5 CONCERN): ack timeout +
        create failure. The retraction must still be sent, without --reply-to
        since the anchor was never confirmed (falsy)."""
        lab = _Lab(monkeypatch)
        lab.ack_value = None
        lab.create_result = MagicMock(success=False, error="boom")
        monkeypatch.setattr(m, "_set_failed_backoff", lambda *a: None)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))

        assert len(lab.send_calls) == 2
        retraction_argv = lab.send_calls[1]
        assert "--reply-to" not in retraction_argv
        # Distinct producer id for the retraction (Race 3).
        session_id_idx = retraction_argv.index("--session-id") + 1
        assert retraction_argv[session_id_idx].endswith("-retract")

    def test_admission_check_defers_when_budget_insufficient(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        tight_deadline = time.monotonic() + (m.UPVOTE_PICKUP_WORST_CASE_S / 2)
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=tight_deadline))
        assert lab.send_calls == []
        assert lab.create_calls == []
        assert any("insufficient run budget" in f for f in result["findings"])

    def test_budget_early_return_when_already_expired(self, monkeypatch):
        lab = _Lab(monkeypatch)
        result = m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() - 1))
        # "ok" (not "skipped") -- the budget-exhausted finding was actually
        # evaluated and is worth surfacing, so run_per_project_audit must not
        # silently drop it (it drops findings whenever status == "skipped").
        assert result["status"] == "ok"
        assert "budget exhausted; project not scanned" in result["findings"]
        assert lab.send_calls == []


# ---------------------------------------------------------------------------
# Two consecutive ticks -> exactly one lane (Risk 2)
# ---------------------------------------------------------------------------


class TestTwoConsecutiveTicks:
    def test_second_tick_sees_session_and_skips(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )

        # Tick 1: starts the lane.
        m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert len(lab.create_calls) == 1

        # Tick 2: the session now exists non-terminally (gate 1 fires).
        monkeypatch.setattr(m, "_non_terminal_session_for", lambda slug, pk: (MagicMock(), False))
        m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))
        assert len(lab.create_calls) == 1  # unchanged -- no second start


# ---------------------------------------------------------------------------
# Send-path structural assertions (§I)
# ---------------------------------------------------------------------------


class TestSendPath:
    def test_send_argv_shape_and_no_outbox_write(self, monkeypatch):
        lab = _Lab(monkeypatch)
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )
        m._pick_up_upvoted(_project(), state=m._RunState(deadline=time.monotonic() + 1000))

        announce_argv = lab.send_calls[0]
        assert "--session-id" in announce_argv
        assert "--ack-sent-id" in announce_argv
        assert "--no-read-the-room" in announce_argv
        assert "send" in announce_argv

        with open(m.__file__) as fh:
            source = fh.read()
        assert "telegram:outbox" not in source
        assert '"valor-telegram"' not in source and "'valor-telegram'" not in source

    def test_run_send_pins_interpreter_and_scrubs_env(self, monkeypatch):
        captured = {}

        def fake_subprocess_run(argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs.get("env")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setenv("VALOR_SESSION_ID", "some-session")
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "999")
        monkeypatch.setenv("AGENT_SESSION_ID", "agt_123")
        monkeypatch.setattr(m.subprocess, "run", fake_subprocess_run)

        m._run_send(["send", "--chat", "-1", "text"])

        assert captured["argv"][0] == m.sys.executable
        assert captured["argv"][1] == "-m"
        assert captured["argv"][2] == "tools.valor_telegram"
        env = captured["env"]
        assert "VALOR_SESSION_ID" not in env
        assert "TELEGRAM_REPLY_TO" not in env
        assert "AGENT_SESSION_ID" not in env

    def test_run_send_timeout_expired_treated_as_failure(self, monkeypatch):
        import subprocess as sp

        def raise_timeout(*a, **k):
            raise sp.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(m.subprocess, "run", raise_timeout)
        rc = m._run_send(["send", "--chat", "-1", "text"])
        assert rc != 0


# ---------------------------------------------------------------------------
# Budget invariants
# ---------------------------------------------------------------------------


class TestBudgetInvariants:
    def test_pickup_worst_case_below_run_budget(self):
        assert m.UPVOTE_PICKUP_WORST_CASE_S < m.UPVOTE_RUN_BUDGET_S

    def test_run_budget_plus_gh_timeout_below_entry_timeout(self):
        assert m.UPVOTE_RUN_BUDGET_S + m.UPVOTE_GH_TIMEOUT_S < m.UPVOTE_ENTRY_TIMEOUT_S

    def test_create_worst_case_is_derived_not_literal(self):
        from config.settings import settings

        assert (
            m.UPVOTE_CREATE_WORST_CASE_S
            == settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s
        )

    def test_aggregate_budget_test_exercises_create_path(self, monkeypatch):
        """A never-arriving-ack fixture never enters the create path and
        proves nothing about the create-side bound -- this test stubs a slow
        create_session instead and asserts the whole run still returns
        inside UPVOTE_ENTRY_TIMEOUT_S."""
        _Lab(monkeypatch)
        monkeypatch.setattr("reflections.utilities.load_local_projects", lambda: [_project()])
        monkeypatch.setattr(
            m, "_gh_issue_list", lambda *a, **k: [_issue(1, "2026-08-01T00:00:00Z")]
        )

        def slow_create(**kwargs):
            # Shorter than the timeout budget for a fast unit test, but the
            # assertion is about the *bound*, not about literally sleeping
            # the full worst case.
            time.sleep(0.05)
            return MagicMock(success=True, error=None)

        monkeypatch.setattr("tools.valor_session.create_session", slow_create)

        start = time.monotonic()
        result = m.run_sdlc_upvote_lanes()
        elapsed = time.monotonic() - start
        assert elapsed < m.UPVOTE_ENTRY_TIMEOUT_S
        assert result["status"] in ("ok", "error")


# ---------------------------------------------------------------------------
# Anti-criteria structural checks (belt-and-suspenders alongside the plan's
# ## Verification table, which greps the checked-out file directly)
# ---------------------------------------------------------------------------


class TestAntiCriteria:
    def test_lock_says_live_is_imported_not_forked(self):
        import reflections.sdlc_progress as sdlc_progress
        import reflections.utilities as utilities

        assert m._lock_says_live is utilities._lock_says_live
        assert sdlc_progress._lock_says_live is utilities._lock_says_live

    def test_no_local_lock_says_live_definition(self):
        with open(m.__file__) as fh:
            assert "def _lock_says_live" not in fh.read()
