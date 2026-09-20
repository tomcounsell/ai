"""Unit tests for the shared restart gate (issue #3528).

The gate (`python -m scripts.update.restart_gate`) is what
`scripts/remote-update.sh` calls in place of its old inline
``git diff $BEFORE_SHA $AFTER_SHA`` gates. Its whole reason to exist is that
the gate and the terminal release verify must reach their verdict through the
SAME classifier over the SAME diff base. These tests pin:

- the wedge regression (#3528): a no-op cron cycle (``BEFORE_SHA ==
  AFTER_SHA``) still restarts a process the classifier calls ``stale``;
- the inverse (a ``matches`` process is never restarted on a no-op cycle, so
  no restart loop and no killed sessions);
- the ``unknown`` fallback to the pull delta, preserving pre-#3528 behavior
  on a fresh install with no beacon and no running service;
- gate/verifier agreement by construction — the gate's verdict tracks
  ``verify_running_release``'s classification for the same repo state;
- per-process path scoping (a worker-only commit must not restart the bridge).

Everything runs against tmp_path git repos with mocked pids / ``ps``
timestamps. No production Redis, no live services.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.update import restart_gate, service
from scripts.update.git import get_short_sha
from scripts.update.service import verify_running_release

pytestmark = pytest.mark.unit

FULL_MACHINE_CHECK = {"hostname": "test", "projects": ["p"], "bridge_projects": ["p"]}
PROC_START_TS = 1_000_000.0
FRESH_TS = PROC_START_TS + 100  # beacon written after the process image started


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(repo: Path, relpath: str, msg: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {msg}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg, "--no-verify")


def _write_beacon(repo: Path, name: str, sha: str, ts: float = FRESH_TS) -> None:
    iso = datetime.fromtimestamp(ts, UTC).isoformat()
    (repo / "data" / f"{name}_boot_sha").write_text(f"{sha}\n{iso}\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tmp git repo with an initial commit touching bridge/ and worker/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "bridge/mod.py", "initial")
    (repo / "worker").mkdir()
    (repo / "worker" / "mod.py").write_text("# worker\n")
    (repo / "data").mkdir()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add worker", "--no-verify")
    return repo


@pytest.fixture
def live_processes(monkeypatch, tmp_path: Path):
    """Mock running bridge+worker pids, a fixed process start ts, and a plist."""
    plist = tmp_path / "com.valor.bridge.plist"
    plist.write_text("<plist/>")
    monkeypatch.setattr(service, "get_bridge_pid", lambda: 4242)
    monkeypatch.setattr(service, "get_worker_pid", lambda: 4343)
    monkeypatch.setattr(service, "get_process_start_ts", lambda pid: PROC_START_TS)
    monkeypatch.setattr(service, "BRIDGE_PLIST_PATH", plist)
    return plist


def _run_gate(repo: Path, process: str, before: str = "", after: str = "") -> int:
    return restart_gate.main(
        [
            "--process",
            process,
            "--before",
            before,
            "--after",
            after,
            "--project-dir",
            str(repo),
        ]
    )


# ---------------------------------------------------------------------------
# The #3528 wedge: no-op cycle, stale process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("process", "relpath"),
    [("worker", "worker/new.py"), ("bridge", "bridge/new.py")],
)
def test_noop_cycle_restarts_stale_process(repo, live_processes, process, relpath):
    """The regression this issue is about.

    A prior cycle pulled process-relevant commits but deferred the restart, so
    the process boots on an older SHA. On every later cycle the pull is a no-op
    (BEFORE == AFTER), which is precisely what made the old inline
    ``git diff $BEFORE $AFTER`` gate report "no relevant changes" forever.
    """
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, process, boot_sha)
    _commit(repo, relpath, "relevant commit landed on a previous cycle")
    head = get_short_sha(repo)

    # Same base the verifier uses -> both must say stale.
    results = verify_running_release(repo, head, FULL_MACHINE_CHECK)
    assert results[process]["classification"] == "stale"
    assert _run_gate(repo, process, before=head, after=head) == 0


def test_noop_cycle_does_not_restart_current_process(repo, live_processes):
    """A `matches` process on a no-op cycle restarts nothing — no restart loop."""
    head = get_short_sha(repo)
    _write_beacon(repo, "worker", head)
    _write_beacon(repo, "bridge", head)
    assert _run_gate(repo, "worker", before=head, after=head) == 1
    assert _run_gate(repo, "bridge", before=head, after=head) == 1


def test_docs_only_commits_ahead_do_not_restart(repo, live_processes):
    """#1091 relevance survives the new diff base: docs commits never restart."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "worker", boot_sha)
    _commit(repo, "docs/plans/some-plan.md", "docs-only commit")
    head = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=head, after=head) == 1


def test_gate_scopes_to_the_process_own_paths(repo, live_processes):
    """A worker-only commit is stale for the worker and current for the bridge."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "worker", boot_sha)
    _write_beacon(repo, "bridge", boot_sha)
    _commit(repo, "worker/only.py", "worker-only commit")
    head = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=head, after=head) == 0
    assert _run_gate(repo, "bridge", before=head, after=head) == 1


# ---------------------------------------------------------------------------
# `unknown` classification → pull-delta fallback (pre-#3528 behavior)
# ---------------------------------------------------------------------------


def test_missing_beacon_falls_back_to_pull_delta(repo, live_processes):
    """Fresh install: no beacon at all. The old gate's answer must survive."""
    before = get_short_sha(repo)
    _commit(repo, "worker/new.py", "worker-relevant pull")
    after = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=before, after=after) == 0


def test_missing_beacon_and_irrelevant_pull_does_not_restart(repo, live_processes):
    before = get_short_sha(repo)
    _commit(repo, "docs/notes.md", "docs-only pull")
    after = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=before, after=after) == 1


def test_missing_beacon_and_noop_pull_does_not_restart(repo, live_processes):
    """Unknown release + nothing pulled must never invent a restart (#1898)."""
    head = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=head, after=head) == 1


def test_process_not_running_falls_back_to_pull_delta(repo, live_processes, monkeypatch):
    """No PID → unknown. The shell's own label-absent branch owns recovery."""
    monkeypatch.setattr(service, "get_worker_pid", lambda: None)
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "worker", boot_sha)
    _commit(repo, "worker/new.py", "worker-relevant pull")
    after = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=boot_sha, after=after) == 0
    assert _run_gate(repo, "worker", before=after, after=after) == 1


def test_orphaned_beacon_falls_back_to_pull_delta(repo, live_processes):
    """Beacon predates the running image → unknown, never a blind restart."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "worker", boot_sha, ts=PROC_START_TS - 100)
    _commit(repo, "worker/new.py", "worker-relevant commit")
    head = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=head, after=head) == 1


def test_unresolvable_boot_sha_falls_back_to_pull_delta(repo, live_processes):
    """History rewrite / shallow clone → unknown, not stale."""
    _write_beacon(repo, "worker", "deadbee")
    head = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=head, after=head) == 1


def test_unresolvable_fallback_shas_do_not_restart(repo, live_processes):
    """A bad --before/--after pair is inconclusive, never a live-worker kill."""
    assert _run_gate(repo, "worker", before="deadbee", after="cafebab") == 1


def test_classification_failure_degrades_to_pull_delta(repo, live_processes, monkeypatch):
    """Any probe blowing up must not wedge the gate shut."""

    def boom(*args, **kwargs):
        raise RuntimeError("ps exploded")

    monkeypatch.setattr(service, "classify_process", boom)
    before = get_short_sha(repo)
    _commit(repo, "worker/new.py", "worker-relevant pull")
    after = get_short_sha(repo)
    assert _run_gate(repo, "worker", before=before, after=after) == 0


# ---------------------------------------------------------------------------
# Structural: gate and verifier share their inputs
# ---------------------------------------------------------------------------


def test_gate_and_verifier_share_the_relevant_path_lists():
    """No second copy of the path sets can exist (#3528)."""
    assert service.PROCESS_RELEVANT_PATHS["worker"] is service.WORKER_RELEVANT_PATHS
    assert service.PROCESS_RELEVANT_PATHS["bridge"] is service.BRIDGE_RELEVANT_PATHS
    assert set(restart_gate.PID_GETTERS) == set(service.PROCESS_RELEVANT_PATHS)


def test_shell_gates_call_the_module_and_hand_roll_no_diff():
    """remote-update.sh must not grow an inline relevance diff again."""
    script = (Path(__file__).parent.parent.parent / "scripts" / "remote-update.sh").read_text()
    assert script.count("scripts.update.restart_gate --process") == 2
    assert "--process worker" in script
    assert "--process bridge" in script
    assert 'diff "$BEFORE_SHA" "$AFTER_SHA"' not in script
