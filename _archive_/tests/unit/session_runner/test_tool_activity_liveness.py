"""Mid-turn liveness for headless sessions running against a foreign repo.

Regression cover for the 2026-07-30 root cause: ``_session_progress_ts`` (the
progress-deadline watchdog's clock) read only ``last_tool_use_at`` /
``last_turn_at`` / ``acquired_at``. ``last_tool_use_at`` is stamped solely by
THIS repo's ``.claude/hooks/pre_tool_use.py``, so a session whose cwd is any
other repo had no signal that ticks mid-turn — the clock collapsed to
``acquired_at`` and the deadline became a hard 30-minute cap on every turn
(three kills in 24h on one Cyndra thread, at 1799.99–1800.4s, twice
mid-deploy).

The fix has two halves, both covered here:

1. The runner registers its own stdlib-only ``matcher: ""`` PreToolUse stamp
   (``agent/session_runner/liveness_hook.py``) which writes a marker file on
   every tool call — including a subagent's, since subagent tool calls fire
   the parent's hooks (verified empirically against the real CLI).
2. ``_session_progress_ts`` consumes that marker plus ``last_stdout_at``, the
   headless replacement for the ``last_pty_activity_at`` signal that the
   #1930 cutover deleted from the list without a substitute.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.session_runner.hook_edge import (
    TOOL_ACTIVITY_SUFFIX,
    generate_hook_settings,
    tool_activity_path,
)
from agent.session_runner.liveness import tool_activity_ts
from tests.db_claim import subprocess_env

pytestmark = pytest.mark.unit

_HOOK = str(Path(__file__).resolve().parents[3] / "agent" / "session_runner" / "liveness_hook.py")

_HOOK_PAYLOAD = json.dumps(
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "abc"}
)


def _run_hook(*args: str) -> subprocess.CompletedProcess:
    """Run the hook the way Claude Code does: bare ``python3``, JSON on stdin.

    ``python3`` (NOT ``sys.executable``) is load-bearing — the hook fires
    inside the spawned ``claude`` process with no access to this repo's venv,
    so a stdlib-only import surface is a hard requirement, not a preference.
    """
    return subprocess.run(
        ["python3", _HOOK, *args],
        input=_HOOK_PAYLOAD,
        capture_output=True,
        text=True,
        timeout=30,
        # No project_root: this repo's importability is precisely what the hook
        # must NOT depend on. subprocess_env only pins REDIS_URL (#2763).
        env=subprocess_env(),
    )


# ---------------------------------------------------------------------------
# The hook itself — stdlib-only, always exit 0
# ---------------------------------------------------------------------------


def test_hook_stamps_marker_under_bare_python3(tmp_path):
    marker = tmp_path / "pm_hook_edges.toolactivity"
    before = time.time()
    proc = _run_hook(str(marker))

    assert proc.returncode == 0, proc.stderr
    stamped = float(marker.read_text())
    assert before <= stamped <= time.time() + 1


def test_hook_never_blocks_a_tool_call_on_failure(tmp_path):
    """A PreToolUse hook exiting 2 BLOCKS the tool. Every failure path exits 0."""
    unwritable = tmp_path / "no-such-dir" / "marker.toolactivity"

    assert _run_hook(str(unwritable)).returncode == 0  # unwritable path
    assert _run_hook().returncode == 0  # no argv
    assert _run_hook("").returncode == 0  # empty path
    assert not unwritable.exists()


def test_hook_emits_nothing_on_stderr(tmp_path):
    """Hook stderr surfaces in the transcript — the stamp must stay silent."""
    proc = _run_hook(str(tmp_path / "m.toolactivity"))
    assert proc.stderr == ""
    assert proc.stdout == ""


# ---------------------------------------------------------------------------
# Registration — fires on EVERY tool, without flooding the edge file
# ---------------------------------------------------------------------------


def test_generated_settings_stamp_liveness_on_every_tool(tmp_path):
    settings_path, edge = generate_hook_settings(tmp_path / "pm", tmp_path / "pm_hook_edges.ndjson")
    pre_tool_use = json.loads(Path(settings_path).read_text())["hooks"]["PreToolUse"]

    all_tools = [e for e in pre_tool_use if e["matcher"] == ""]
    assert len(all_tools) == 1, "the liveness stamp must fire for every tool call"
    command = all_tools[0]["hooks"][0]["command"]
    assert "liveness_hook.py" in command
    assert str(tool_activity_path(edge)) in command
    # The whole reason for a separate marker file: ordinary tool calls must
    # not flood the NDJSON edge file the consumer cursors through.
    assert "hook_forwarder.py" not in command


def test_ask_user_question_edge_survives(tmp_path):
    """The needs-human edge (#1919) shares PreToolUse — it must not be displaced."""
    settings_path, edge = generate_hook_settings(tmp_path / "pm", tmp_path / "pm_hook_edges.ndjson")
    pre_tool_use = json.loads(Path(settings_path).read_text())["hooks"]["PreToolUse"]

    ask = [e for e in pre_tool_use if e["matcher"] == "AskUserQuestion"]
    assert len(ask) == 1
    assert "hook_forwarder.py" in ask[0]["hooks"][0]["command"]
    assert str(edge) in ask[0]["hooks"][0]["command"]


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def _point_base_dir_at(monkeypatch, path: Path) -> None:
    import agent.session_runner.adapter as adapter

    monkeypatch.setattr(adapter, "_hook_edge_base_dir", lambda: str(path))


def test_tool_activity_ts_takes_max_across_role_channels(monkeypatch, tmp_path):
    """One AgentSession can provision several hook channels — any tick counts."""
    _point_base_dir_at(monkeypatch, tmp_path)
    session_dir = tmp_path / "tg_cyndra_-100_292"
    session_dir.mkdir()
    (session_dir / f"pm_hook_edges{TOOL_ACTIVITY_SUFFIX}").write_text("1000.5")
    (session_dir / f"dev_hook_edges{TOOL_ACTIVITY_SUFFIX}").write_text("2000.25")

    assert tool_activity_ts("tg_cyndra_-100_292") == 2000.25


@pytest.mark.parametrize(
    "session_id, marker_body",
    [
        ("missing-session", None),  # no directory at all
        ("malformed", "not-a-float"),  # truncated / garbage payload
        ("empty", ""),  # created but never written
        (None, None),  # no session_id
        ("", None),  # empty session_id
    ],
)
def test_tool_activity_ts_degrades_to_no_signal(monkeypatch, tmp_path, session_id, marker_body):
    """Any read failure must read as 'no signal', never raise, never fabricate."""
    _point_base_dir_at(monkeypatch, tmp_path)
    if marker_body is not None:
        d = tmp_path / str(session_id)
        d.mkdir()
        (d / f"pm_hook_edges{TOOL_ACTIVITY_SUFFIX}").write_text(marker_body)

    assert tool_activity_ts(session_id) is None


# ---------------------------------------------------------------------------
# The deadline clock — the actual regression
# ---------------------------------------------------------------------------


class _Row:
    """Minimal AgentSession stand-in — ``_session_progress_ts`` is all getattr."""

    def __init__(self, **fields):
        self.session_id = fields.pop("session_id", "sess-1")
        for name in ("last_tool_use_at", "last_turn_at", "last_stdout_at"):
            setattr(self, name, fields.pop(name, None))
        assert not fields, fields


def test_foreign_repo_session_is_not_capped_at_the_deadline(monkeypatch, tmp_path):
    """THE regression: no CLI hooks in the target repo, so both SDK fields stay
    None for the row's whole life. Before the fix the clock collapsed to
    ``acquired_at`` and any turn past 1800s was killed regardless of activity.
    """
    from agent.agent_session_queue import SESSION_PROGRESS_DEADLINE_S, _session_progress_ts

    _point_base_dir_at(monkeypatch, tmp_path)
    session_dir = tmp_path / "sess-1"
    session_dir.mkdir()
    now = time.time()
    # A subagent tool call 10s ago — real work, mid-turn.
    (session_dir / f"pm_hook_edges{TOOL_ACTIVITY_SUFFIX}").write_text(str(now - 10))

    acquired_at = now - (SESSION_PROGRESS_DEADLINE_S + 600)  # 40 min into the turn
    progress = _session_progress_ts(_Row(), acquired_at)

    assert progress == pytest.approx(now - 10, abs=1)
    assert (now - progress) < SESSION_PROGRESS_DEADLINE_S, "would have been killed mid-work"


def test_stdout_liveness_counts_as_progress(monkeypatch, tmp_path):
    """#1930 deleted ``last_pty_activity_at`` from the candidates; its headless
    replacement ``last_stdout_at`` (#1935) was never added in its place.
    """
    from datetime import UTC, datetime, timedelta

    from agent.agent_session_queue import SESSION_PROGRESS_DEADLINE_S, _session_progress_ts

    _point_base_dir_at(monkeypatch, tmp_path)  # no marker files — stdout only
    now = time.time()
    row = _Row(last_stdout_at=datetime.now(tz=UTC) - timedelta(seconds=30))

    progress = _session_progress_ts(row, now - (SESSION_PROGRESS_DEADLINE_S + 600))

    assert progress == pytest.approx(now - 30, abs=2)


def test_acquired_at_remains_the_floor(monkeypatch, tmp_path):
    """A session with no signal at all must still get a well-defined clock —
    the never-started/init-hang legs depend on the deadline still firing.
    """
    from agent.agent_session_queue import _session_progress_ts

    _point_base_dir_at(monkeypatch, tmp_path)
    acquired_at = time.time() - 4000

    assert _session_progress_ts(_Row(), acquired_at) == acquired_at


def test_progress_ts_never_raises_when_marker_dir_is_hostile(monkeypatch, tmp_path):
    """The watchdog polls this every 30s per running session — an exception
    here would tear down the worker loop, not just the read.
    """
    import agent.session_runner.adapter as adapter
    from agent.agent_session_queue import _session_progress_ts

    def _boom():
        raise OSError("data dir unavailable")

    monkeypatch.setattr(adapter, "_hook_edge_base_dir", _boom)
    acquired_at = time.time() - 100

    assert _session_progress_ts(_Row(), acquired_at) == acquired_at


def test_hook_and_reader_agree_on_the_path(monkeypatch, tmp_path):
    """Writer and reader are wired through different modules — pin the contract
    that the path the settings file hands the hook is the path the worker globs.
    """
    from agent.agent_session_queue import _session_progress_ts

    _point_base_dir_at(monkeypatch, tmp_path)
    session_dir = tmp_path / "sess-1"
    session_dir.mkdir()
    _settings, edge = generate_hook_settings(
        session_dir / "pm", session_dir / "pm_hook_edges.ndjson"
    )

    assert _run_hook(str(tool_activity_path(edge))).returncode == 0

    progress = _session_progress_ts(_Row(), time.time() - 4000)
    assert progress == pytest.approx(time.time(), abs=5), "worker did not see the hook's stamp"


def test_hook_module_imports_only_stdlib():
    """Enforced structurally: the hook pays ~0.07s per tool call stdlib-only vs
    ~2.0s if it ever imports the ORM (measured 2026-07-30). It fires on EVERY
    tool call in every session, so that delta is the whole design constraint.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path=[p for p in sys.path if 'src/ai' not in p];"
            f" exec(open({_HOOK!r}).read())",
        ],
        input=_HOOK_PAYLOAD,
        capture_output=True,
        text=True,
        timeout=30,
        # No project_root: the child strips this repo off sys.path on purpose.
        env=subprocess_env(),
    )
    assert proc.returncode == 0, proc.stderr
