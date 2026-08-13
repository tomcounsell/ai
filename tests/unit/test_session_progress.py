"""Tests for the read-only session-progress verb (issue #2663).

The headline test is :func:`test_2662_scenario_fresh_tool_activity_quiet_transcript`,
which reproduces the exact shape that was misdiagnosed as a deadlock on
2026-08-07: a fresh hook-edge tool-activity marker alongside a parent
transcript that has been silent for twenty minutes. The session was healthy
and went on to open a 14-file PR. The verdict must be ``PROGRESSING``.

Everything here is hermetic. No AgentSession is created, no Redis is touched:
:func:`tools.session_progress.build_report` is duck-typed over the session
object, and every filesystem root is injectable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import agent.session_runner.adapter as adapter
from tests.db_claim import subprocess_env
from tools.session_progress import (
    VERDICT_NO_RECENT_ACTIVITY,
    VERDICT_PROGRESSING,
    VERDICT_UNKNOWN,
    Signal,
    build_report,
    compute_verdict,
    default_window_s,
    format_age,
    pid_alive,
    pr_links,
    task_output_signal,
    tool_activity_signal,
    transcript_path,
    transcript_signal,
)

pytestmark = [pytest.mark.unit, pytest.mark.sessions]

NOW = 1_800_000_000.0
WINDOW = 1800.0


def _session(**overrides):
    """A duck-typed stand-in for an AgentSession row."""
    base = dict(
        session_id="test-progress-session",
        agent_session_id="0123456789abcdef0123456789abcdef",
        status="running",
        session_type="eng",
        claude_session_uuid="6f451ea2-687b-4258-967b-2caff5975fc0",
        runner_cwd=None,
        exec_pid=None,
        created_at=None,
        started_at=None,
        updated_at=None,
        slug="sdlc-2663",
        branch_name="session/sdlc-2663",
        last_tool_use_at=None,
        last_turn_at=None,
        last_stdout_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _unused_pid() -> int:
    """A pid that has certainly been reaped.

    Spawn a trivial child and wait for it. `subprocess` reaps it, so the pid is
    gone rather than a zombie -- a zombie still answers `kill(pid, 0)` and
    would make the test assert the opposite of what it means to.
    """
    proc = subprocess.Popen([sys.executable, "-c", ""], env=subprocess_env())
    proc.wait()
    return proc.pid


def _write_marker(root, session_id, ts, name="pm_hook_edges.toolactivity"):
    d = root / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(str(ts))
    return d


def _point_base_dir_at(monkeypatch, path) -> None:
    """Point the production hook-edge base dir at ``path`` for this test.

    Mirrors ``tests/unit/session_runner/test_tool_activity_liveness.py::
    _point_base_dir_at``. ``tool_activity_signal`` has exactly one
    implementation — it always delegates to
    ``agent.session_runner.liveness.tool_activity_ts``, which resolves
    ``agent.session_runner.adapter._hook_edge_base_dir()`` — so this is how
    every test in this file drives markers through the real read path
    instead of carrying a second, test-only implementation here.
    """
    monkeypatch.setattr(adapter, "_hook_edge_base_dir", lambda: str(path))


def _write_transcript(projects_root, uuid, *, mtime, pr_entries=()):
    d = projects_root / "-Users-tomcounsell-src-ai"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{uuid}.jsonl"
    lines = [json.dumps({"type": "user", "message": "hello"})]
    for entry in pr_entries:
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def _write_task_output(tmp_root, uuid, *, mtime, task_id="a4567bb1ee449e919"):
    d = tmp_root / "claude-501" / "-Users-tomcounsell-src-ai" / uuid / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{task_id}.output"
    path.write_text("running tests...\n")
    os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# The acceptance-criterion regression test for #2662
# ---------------------------------------------------------------------------


def test_2662_scenario_fresh_tool_activity_quiet_transcript(tmp_path, monkeypatch):
    """Fresh tool activity + a 20-minute-quiet parent transcript = PROGRESSING.

    This is the exact evidence shape of the 2026-08-07 misdiagnosis. The
    parent transcript is silent because the PM is blocked in a long
    synchronous ``Agent`` call — subagent steps are not written as sidechain
    entries until the call returns — while the runner's hook-edge marker
    keeps ticking on the subagent's every tool call.

    A tool that reads the silence and reports a hang re-creates #2662. This
    asserts it does not.
    """
    hook_root = tmp_path / "hook_edges"
    projects_root = tmp_path / "projects"
    tmp_root = tmp_path / "tmp"
    uuid = "6f451ea2-687b-4258-967b-2caff5975fc0"

    # Tool activity 4 seconds ago — the load-bearing signal.
    _write_marker(hook_root, "tg_cyndra_-1003900483201_353", NOW - 4)
    _point_base_dir_at(monkeypatch, hook_root)
    # Parent transcript silent for 20 minutes: the EXPECTED shape, not a hang.
    _write_transcript(projects_root, uuid, mtime=NOW - 1200)

    session = _session(
        session_id="tg_cyndra_-1003900483201_353",
        claude_session_uuid=uuid,
        status="running",
        # No ORM liveness fields at all: this session runs against a foreign
        # repo that does not carry this repo's .claude/hooks.
        last_tool_use_at=None,
        last_turn_at=None,
        last_stdout_at=None,
    )

    report = build_report(
        session,
        now=NOW,
        window_s=WINDOW,
        projects_root=str(projects_root),
        task_output_roots=[str(tmp_root)],
    )

    assert report.verdict == VERDICT_PROGRESSING
    assert "tool_activity" in report.verdict_reason
    assert report.verdict_line.startswith("PROGRESSING —")
    # And it must not have invented a hang from the quiet transcript.
    assert "wedged" not in report.render().lower()
    assert "stuck" not in report.render().lower()


def test_2662_scenario_reports_the_pr_it_went_on_to_open(tmp_path, monkeypatch):
    """The verdict line surfaces pr-link artifacts alongside the liveness read."""
    hook_root = tmp_path / "hook_edges"
    projects_root = tmp_path / "projects"
    uuid = "6f451ea2-687b-4258-967b-2caff5975fc0"

    _write_marker(hook_root, "s1", NOW - 4)
    _point_base_dir_at(monkeypatch, hook_root)
    _write_transcript(
        projects_root,
        uuid,
        mtime=NOW - 1200,
        pr_entries=[
            {
                "type": "pr-link",
                "sessionId": uuid,
                "prNumber": 102,
                "prUrl": "https://github.com/Cyndra-AI/cyndra-consulting/pull/102",
                "prRepository": "Cyndra-AI/cyndra-consulting",
                "timestamp": "2027-01-15T22:20:00.000Z",
            }
        ],
    )

    report = build_report(
        _session(session_id="s1", claude_session_uuid=uuid),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(projects_root),
        task_output_roots=[str(tmp_path / "tmp")],
    )
    assert report.verdict == VERDICT_PROGRESSING
    assert "PR #102" in report.verdict_line
    assert report.artifacts[0]["number"] == 102


# ---------------------------------------------------------------------------
# Verdict branches
# ---------------------------------------------------------------------------


def test_absence_of_all_evidence_is_unknown_not_wedged(tmp_path, monkeypatch):
    """No marker, no transcript, no task output, no ORM stamps → UNKNOWN."""
    _point_base_dir_at(monkeypatch, tmp_path / "absent")
    report = build_report(
        _session(claude_session_uuid=None),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_UNKNOWN
    assert "absence of evidence" in report.verdict_reason


def test_all_signals_stale_is_no_recent_activity(tmp_path, monkeypatch):
    hook_root = tmp_path / "hook_edges"
    _write_marker(hook_root, "s1", NOW - 5000)
    _point_base_dir_at(monkeypatch, hook_root)
    report = build_report(
        _session(session_id="s1", claude_session_uuid=None),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_NO_RECENT_ACTIVITY
    # Truthfulness: it must say why this is not proof of a hang.
    assert "not proof of a hang" in report.verdict_reason


@pytest.mark.parametrize("status", ["completed", "failed", "killed", "abandoned", "cancelled"])
def test_terminal_status_is_unknown_even_with_fresh_signals(tmp_path, monkeypatch, status):
    """A finished session is neither progressing nor inactive — decline to guess."""
    hook_root = tmp_path / "hook_edges"
    _write_marker(hook_root, "s1", NOW - 2)
    _point_base_dir_at(monkeypatch, hook_root)
    report = build_report(
        _session(session_id="s1", status=status, claude_session_uuid=None),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_UNKNOWN
    assert status in report.verdict_reason


def test_task_output_alone_can_prove_progress(tmp_path, monkeypatch):
    """A fresh background-task output file is sufficient positive evidence."""
    uuid = "abc00000-0000-0000-0000-000000000000"
    tmp_root = tmp_path / "tmp"
    _write_task_output(tmp_root, uuid, mtime=NOW - 30)
    _point_base_dir_at(monkeypatch, tmp_path / "absent")
    report = build_report(
        _session(session_id="s-nomarker", claude_session_uuid=uuid),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_root)],
    )
    assert report.verdict == VERDICT_PROGRESSING
    assert "task_output" in report.verdict_reason


def test_orm_liveness_fields_count_as_evidence(tmp_path, monkeypatch):
    _point_base_dir_at(monkeypatch, tmp_path / "absent")
    report = build_report(
        _session(claude_session_uuid=None, last_stdout_at=NOW - 10),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_PROGRESSING
    assert "last_stdout_at" in report.verdict_reason


def test_pr_link_artifact_does_not_vote_on_the_verdict(tmp_path, monkeypatch):
    """A recent PR proves work HAPPENED, not that work is HAPPENING."""
    projects_root = tmp_path / "projects"
    uuid = "def00000-0000-0000-0000-000000000000"
    # Transcript mtime is stale; only the pr-link timestamp is recent.
    _write_transcript(
        projects_root,
        uuid,
        mtime=NOW - 9000,
        pr_entries=[
            {
                "type": "pr-link",
                "prNumber": 7,
                "prUrl": "https://github.com/o/r/pull/7",
                "timestamp": "2027-01-15T22:39:00.000Z",
            }
        ],
    )
    _point_base_dir_at(monkeypatch, tmp_path / "absent")
    report = build_report(
        _session(session_id="s-pr", claude_session_uuid=uuid),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(projects_root),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_NO_RECENT_ACTIVITY
    assert report.artifacts and report.artifacts[0]["number"] == 7


def test_window_boundary_is_inclusive():
    sig = [Signal("tool_activity", NOW - WINDOW)]
    verdict, _ = compute_verdict(sig, "running", window_s=WINDOW, now=NOW)
    assert verdict == VERDICT_PROGRESSING

    sig = [Signal("tool_activity", NOW - WINDOW - 1)]
    verdict, _ = compute_verdict(sig, "running", window_s=WINDOW, now=NOW)
    assert verdict == VERDICT_NO_RECENT_ACTIVITY


# ---------------------------------------------------------------------------
# Graceful degradation — every one of these must return, never raise
# ---------------------------------------------------------------------------


def test_missing_marker_dir_degrades_to_absent(tmp_path, monkeypatch):
    _point_base_dir_at(monkeypatch, tmp_path / "gone")
    assert tool_activity_signal("nope").ts is None


def test_unreadable_marker_payload_degrades_to_absent(tmp_path, monkeypatch):
    root = tmp_path / "hook_edges"
    d = root / "s1"
    d.mkdir(parents=True)
    (d / "pm_hook_edges.toolactivity").write_text("not-a-float")
    _point_base_dir_at(monkeypatch, root)
    assert tool_activity_signal("s1").ts is None


def test_multiple_marker_channels_take_the_max(tmp_path, monkeypatch):
    root = tmp_path / "hook_edges"
    _write_marker(root, "s1", NOW - 900, name="pm_hook_edges.toolactivity")
    _write_marker(root, "s1", NOW - 5, name="dev_hook_edges.toolactivity")
    _point_base_dir_at(monkeypatch, root)
    assert tool_activity_signal("s1").ts == NOW - 5


def test_missing_transcript_degrades_to_absent(tmp_path):
    assert transcript_path("no-such-uuid", None, projects_root=str(tmp_path)) is None
    assert transcript_signal(None).ts is None
    assert pr_links(None) == []


def test_unreadable_task_dir_degrades_to_absent(tmp_path):
    assert task_output_signal("uuid", roots=[str(tmp_path / "gone")]).ts is None
    assert task_output_signal(None).ts is None


def test_malformed_transcript_lines_are_skipped(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"type": "pr-link", broken\n{"type":"pr-link","prNumber":3}\n')
    links = pr_links(str(path))
    assert [entry["number"] for entry in links] == [3]


def test_dead_pid_reads_false_and_does_not_raise():
    # PID 1 always exists; a very high pid essentially never does.
    assert pid_alive(None) is None
    assert pid_alive(0) is None
    assert pid_alive(os.getpid()) is True
    assert pid_alive(4_000_000) in (False, None)


def test_dead_pid_never_forces_a_negative_verdict(tmp_path, monkeypatch):
    """Process-table facts are reported, never inferred from."""
    hook_root = tmp_path / "hook_edges"
    _write_marker(hook_root, "s1", NOW - 3)
    _point_base_dir_at(monkeypatch, hook_root)
    report = build_report(
        _session(session_id="s1", claude_session_uuid=None, exec_pid=4_000_000),
        now=NOW,
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    assert report.verdict == VERDICT_PROGRESSING
    assert report.fields["exec_pid"] == 4_000_000


def test_report_never_collects_cpu_or_child_counts():
    """The two readings that caused #2662 must not exist in the output."""
    import tools.session_progress as mod

    source = open(mod.__file__, encoding="utf-8").read()
    lowered = source.lower()
    for banned in ("psutil", "cpu_percent", "num_threads", ".children(", "pgrep"):
        # Docstrings explain WHY they are absent; code must not reference them.
        code_lines = [
            line
            for line in lowered.splitlines()
            if banned in line and not line.strip().startswith(("#", '"', "'"))
        ]
        assert not code_lines, f"{banned} leaked into session_progress code: {code_lines}"

    report_keys = set(
        build_report(_session(claude_session_uuid=None), now=NOW, window_s=WINDOW)
        .to_dict()["fields"]
        .keys()
    )
    assert not report_keys & {"cpu_percent", "child_count", "num_children"}


# ---------------------------------------------------------------------------
# Foreign-repo behavior and misc
# ---------------------------------------------------------------------------


def test_foreign_repo_transcript_found_by_glob_without_runner_cwd(tmp_path):
    """No runner_cwd recorded (foreign repo) still resolves the transcript."""
    projects_root = tmp_path / "projects"
    uuid = "aaa00000-0000-0000-0000-000000000000"
    written = _write_transcript(projects_root, uuid, mtime=NOW - 10)
    found = transcript_path(uuid, None, projects_root=str(projects_root))
    assert found == str(written)


def test_hook_marker_works_without_any_repo_hooks(tmp_path, monkeypatch):
    """The marker is repo-independent: no ORM stamps, no repo hooks, still fresh."""
    hook_root = tmp_path / "hook_edges"
    _write_marker(hook_root, "foreign-session", NOW - 1)
    _point_base_dir_at(monkeypatch, hook_root)
    sig = tool_activity_signal("foreign-session")
    assert sig.age_s(NOW) == pytest.approx(1.0)


def test_default_window_matches_the_watchdog_deadline():
    from agent.agent_session_queue import SESSION_PROGRESS_DEADLINE_S

    assert default_window_s() == int(SESSION_PROGRESS_DEADLINE_S)


def test_format_age_shapes():
    assert format_age(None) == "unknown"
    assert format_age(4) == "4s"
    assert format_age(300) == "5m"
    assert format_age(7560) == "2h 6m"
    assert format_age(7200) == "2h"


def test_json_output_is_serializable(tmp_path, monkeypatch):
    hook_root = tmp_path / "hook_edges"
    _write_marker(hook_root, "s1", time.time())
    _point_base_dir_at(monkeypatch, hook_root)
    report = build_report(
        _session(session_id="s1", claude_session_uuid=None),
        window_s=WINDOW,
        projects_root=str(tmp_path / "absent"),
        task_output_roots=[str(tmp_path / "absent")],
    )
    payload = json.loads(json.dumps(report.to_dict(), default=str))
    assert payload["verdict"] == VERDICT_PROGRESSING
    assert any(s["name"] == "tool_activity" for s in payload["signals"])


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_valor_cli_registers_the_progress_verb():
    from tools.valor_cli import KNOWN_SUBCOMMANDS, _build_parser

    assert "progress" in KNOWN_SUBCOMMANDS
    args = _build_parser().parse_args(["progress", "abc123", "--window", "60", "--json"])
    assert args.command == "progress"
    assert args.id == "abc123"
    assert args.window == 60.0
    assert args.json is True


def test_valor_cli_rejects_negative_window_at_parse_time(capsys):
    """A negative ``--window`` has no honest reading (round-2 finding, half fixed).

    Before this, a negative window sailed through to ``compute_verdict`` and
    the reason line reported a threshold the caller never passed, e.g.
    "older than the -5s window". Reject it in the parser itself so the
    error is immediate and unambiguous, and never reaches session lookup.
    """
    from tools.valor_cli import _build_parser

    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["progress", "abc123", "--window", "-5"])
    assert exc_info.value.code == 2
    assert "--window must be >= 0" in capsys.readouterr().err


def test_valor_session_rejects_negative_window_at_parse_time(monkeypatch, capsys):
    """Same guard, other entry point: ``valor-session progress --window -5``.

    Pins parity with ``tools/valor_cli.py`` — both parsers share
    ``tools.session_progress.window_arg_type`` so a negative value fails
    identically (and before any session lookup) no matter which CLI is
    invoked.
    """
    from tools import valor_session

    monkeypatch.setattr(
        "sys.argv", ["valor-session", "progress", "--id", "abc123", "--window", "-5"]
    )
    with pytest.raises(SystemExit) as exc_info:
        valor_session.main()
    assert exc_info.value.code == 2
    assert "--window must be >= 0" in capsys.readouterr().err


@pytest.mark.parametrize("bad_value", ["nan", "inf", "1e400"])
def test_window_arg_type_rejects_nonfinite_values(bad_value):
    """``nan``/``inf`` compare false to everything and slip past ``< 0`` (round-4 finding).

    ``nan < 0`` is ``False`` (all ``nan`` comparisons are), so a ``nan``
    window silently reported "no recent activity" against a threshold the
    caller never passed. ``inf`` is not ``< 0`` either, and ``format_age``
    cannot render it (``int(inf)`` raises ``OverflowError``), violating this
    module's "never raises" contract. ``1e400`` parses to ``float('inf')``
    and must be caught the same way as a literal ``"inf"``.
    """
    from tools.session_progress import window_arg_type

    with pytest.raises(argparse.ArgumentTypeError, match="--window must be >= 0"):
        window_arg_type(bad_value)


@pytest.mark.parametrize("bad_value", ["nan", "inf", "1e400"])
def test_valor_cli_rejects_nonfinite_window_at_parse_time(bad_value, capsys):
    """Same round-4 finding, exercised through the ``valor`` CLI parser."""
    from tools.valor_cli import _build_parser

    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["progress", "abc123", "--window", bad_value])
    assert exc_info.value.code == 2
    assert "--window must be >= 0" in capsys.readouterr().err


@pytest.mark.parametrize("bad_value", ["nan", "inf", "1e400"])
def test_valor_session_rejects_nonfinite_window_at_parse_time(bad_value, monkeypatch, capsys):
    """Same round-4 finding, other entry point: ``valor-session progress --window nan``."""
    from tools import valor_session

    monkeypatch.setattr(
        "sys.argv", ["valor-session", "progress", "--id", "abc123", "--window", bad_value]
    )
    with pytest.raises(SystemExit) as exc_info:
        valor_session.main()
    assert exc_info.value.code == 2
    assert "--window must be >= 0" in capsys.readouterr().err


def test_valor_cli_dispatches_progress_to_valor_session(monkeypatch):
    from tools import valor_cli, valor_session

    seen = {}

    def fake(args):
        seen["id"] = args.id
        seen["window"] = args.window
        seen["json"] = args.json
        return 0

    monkeypatch.setattr(valor_session, "cmd_progress", fake)
    assert valor_cli.main(["progress", "sess-x", "--window", "5"]) == 0
    assert seen == {"id": "sess-x", "window": 5.0, "json": False}


def test_valor_session_registers_progress_in_its_dispatch():
    import inspect

    from tools import valor_session

    source = inspect.getsource(valor_session.main)
    assert '"progress": cmd_progress' in source


def test_cmd_progress_reports_missing_session(monkeypatch, capsys):
    from tools import valor_session

    monkeypatch.setattr(valor_session, "_load_env", lambda: None)
    monkeypatch.setattr(valor_session, "_find_session", lambda _id: None)
    rc = valor_session.cmd_progress(SimpleNamespace(id="nope", window=None, json=False))
    assert rc == 1
    assert "Session not found" in capsys.readouterr().err


def test_cmd_progress_is_read_only(monkeypatch, capsys):
    """The command must never call save()/delete() on the resolved session."""
    from tools import valor_session

    calls = []

    class Tripwire(SimpleNamespace):
        def save(self, *a, **k):
            calls.append("save")

        def delete(self, *a, **k):
            calls.append("delete")

    session = Tripwire(**{k: v for k, v in vars(_session(claude_session_uuid=None)).items()})
    monkeypatch.setattr(valor_session, "_load_env", lambda: None)
    monkeypatch.setattr(valor_session, "_find_session", lambda _id: session)

    rc = valor_session.cmd_progress(SimpleNamespace(id="whatever", window=60.0, json=True))
    assert rc == 0
    assert calls == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] in {
        VERDICT_PROGRESSING,
        VERDICT_NO_RECENT_ACTIVITY,
        VERDICT_UNKNOWN,
    }


def test_cmd_progress_window_zero_is_distinct_from_absent(monkeypatch, capsys):
    """``--window 0`` must not be conflated with "no ``--window`` given".

    ``float(window) if window else None`` treats ``0.0`` as falsy, so
    ``--window 0`` used to silently discard the caller's window and fall
    back to the watchdog's default (1800s). This pins the exact ``window_s``
    ``cmd_progress`` hands to ``build_report`` for both cases so the
    falsy/absent conflation cannot regress silently.
    """
    import tools.session_progress as session_progress_module
    from tools import valor_session

    seen: list[float | None] = []

    class _FakeReport:
        def to_dict(self):
            return {"verdict": "UNKNOWN"}

    def fake_build_report(_session, *, window_s=None):
        seen.append(window_s)
        return _FakeReport()

    monkeypatch.setattr(session_progress_module, "build_report", fake_build_report)
    monkeypatch.setattr(valor_session, "_load_env", lambda: None)
    monkeypatch.setattr(valor_session, "_find_session", lambda _id: _session())

    rc = valor_session.cmd_progress(SimpleNamespace(id="s1", window=0.0, json=True))
    assert rc == 0
    rc = valor_session.cmd_progress(SimpleNamespace(id="s1", window=None, json=True))
    assert rc == 0

    assert seen == [0.0, None], (
        f"--window 0 must pass window_s=0.0, not fall back to the default: {seen}"
    )


# ---------------------------------------------------------------------------
# The load-bearing signal's PRODUCTION path (review of #2668)
# ---------------------------------------------------------------------------


class TestToolActivityProductionPath:
    """Isolated coverage of ``tool_activity_signal``'s wrapping behaviour.

    ``tool_activity_signal`` has exactly one implementation, which delegates
    straight to ``agent.session_runner.liveness.tool_activity_ts`` (the rest
    of this file drives that real function end to end via
    ``_point_base_dir_at``). These tests monkeypatch ``tool_activity_ts``
    itself so propagation, absence, and the loud-degradation-on-exception
    contract are pinned independently of on-disk marker mechanics.
    """

    def test_production_branch_propagates_the_real_value(self, monkeypatch):
        import agent.session_runner.liveness as liveness

        monkeypatch.setattr(liveness, "tool_activity_ts", lambda _sid: NOW - 4.0)
        sig = tool_activity_signal("some-session")
        assert sig.ts == NOW - 4.0
        assert sig.age_s(NOW) == 4.0

    def test_production_branch_propagates_absence(self, monkeypatch):
        import agent.session_runner.liveness as liveness

        monkeypatch.setattr(liveness, "tool_activity_ts", lambda _sid: None)
        assert tool_activity_signal("some-session").ts is None

    def test_a_broken_liveness_import_degrades_loudly_not_silently(self, monkeypatch, caplog):
        """Degrading to absent is right; doing it invisibly is not."""
        import agent.session_runner.liveness as liveness

        def boom(_sid):
            raise RuntimeError("liveness contract changed")

        monkeypatch.setattr(liveness, "tool_activity_ts", boom)
        with caplog.at_level("WARNING"):
            sig = tool_activity_signal("some-session")

        assert sig.ts is None, "must degrade, not raise"
        assert sig.detail and "RuntimeError" in sig.detail
        assert any("load-bearing" in r.getMessage() for r in caplog.records), (
            "a break in the headline signal must be visible in the logs"
        )

    def test_report_uses_the_production_path_when_no_override(self, monkeypatch):
        """End to end: the value reaches the verdict, not just the Signal."""
        import agent.session_runner.liveness as liveness

        monkeypatch.setattr(liveness, "tool_activity_ts", lambda _sid: NOW - 10.0)
        report = build_report(
            _session(),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_PROGRESSING
        assert "tool_activity" in report.verdict_reason


# ---------------------------------------------------------------------------
# A known-dead exec_pid contradicts PROGRESSING (review of #2668)
# ---------------------------------------------------------------------------


class TestDeadPidIsSurfacedButDoesNotVote:
    def test_pid_alive_distinguishes_false_from_unknowable(self):
        """`False` is positive evidence; `None` is absence of it."""
        assert pid_alive(None) is None
        # pid 1 exists on every unix; either alive or permission-denied-alive.
        assert pid_alive(1) is True
        dead = _unused_pid()
        assert pid_alive(dead) is False, "a reaped pid must read False, never None"

    def test_dead_pid_is_named_in_the_verdict_line(self, tmp_path, monkeypatch):
        dead = _unused_pid()
        root = tmp_path / "hooks"
        _write_marker(root, "test-progress-session", NOW - 300.0)
        _point_base_dir_at(monkeypatch, root)
        report = build_report(
            _session(exec_pid=dead),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_PROGRESSING, "pid must not vote"
        assert report.fields["exec_pid_alive"] is False
        assert "not running" in report.verdict_line
        assert str(dead) in report.verdict_line

    def test_unknowable_pid_adds_no_note(self, tmp_path, monkeypatch):
        """Only a definite negative is a contradiction. `None` is silence."""
        root = tmp_path / "hooks"
        _write_marker(root, "test-progress-session", NOW - 300.0)
        _point_base_dir_at(monkeypatch, root)
        report = build_report(
            _session(exec_pid=None),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_PROGRESSING
        assert report.contradiction_note is None
        assert "not running" not in report.verdict_line

    def test_no_note_when_the_verdict_is_not_progressing(self, tmp_path, monkeypatch):
        """A dead pid alongside NO RECENT ACTIVITY is not a contradiction."""
        dead = _unused_pid()
        root = tmp_path / "hooks"
        _write_marker(root, "test-progress-session", NOW - 99_999.0)
        _point_base_dir_at(monkeypatch, root)
        report = build_report(
            _session(exec_pid=dead),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_NO_RECENT_ACTIVITY
        assert report.contradiction_note is None


# ---------------------------------------------------------------------------
# Future-dated timestamps are not evidence (review of #2668)
# ---------------------------------------------------------------------------


class TestFutureDatedTimestamps:
    def test_far_future_signal_reads_as_absent(self):
        sig = Signal("tool_activity", NOW + 86400.0)
        assert sig.age_s(NOW) is None
        assert sig.is_implausible(NOW) is True

    def test_small_skew_still_clamps_to_zero(self):
        """Ordinary jitter between two clocks is not corruption."""
        sig = Signal("tool_activity", NOW + 5.0)
        assert sig.age_s(NOW) == 0.0
        assert sig.is_implausible(NOW) is False

    def test_future_marker_yields_unknown_not_progressing(self, tmp_path, monkeypatch):
        root = tmp_path / "hooks"
        _write_marker(root, "test-progress-session", NOW + 86400.0)
        _point_base_dir_at(monkeypatch, root)
        report = build_report(
            _session(),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_UNKNOWN, (
            "a marker from the future is skew or corruption, not maximal freshness"
        )
        assert "future" in report.verdict_reason

    def test_a_real_signal_still_wins_over_a_skewed_one(self, tmp_path, monkeypatch):
        """One bad clock must not suppress evidence that is actually good."""
        root = tmp_path / "hooks"
        _write_marker(root, "test-progress-session", NOW + 86400.0, name="a.toolactivity")
        _point_base_dir_at(monkeypatch, root)
        report = build_report(
            _session(last_turn_at=NOW - 12.0),
            now=NOW,
            window_s=WINDOW,
            projects_root="/nonexistent",
            task_output_roots=[],
        )
        assert report.verdict == VERDICT_PROGRESSING
