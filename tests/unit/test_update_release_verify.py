"""Unit tests for boot-SHA release verification (issue #1898).

Covers:

- ``verify_running_release`` classification matrix (matches | stale | unknown):
  missing/empty/malformed beacons, empty vs. non-empty relevant-range git log,
  the docs-only-commits-ahead regression guard (#1091 consistency), orphaned
  beacons, unresolvable boot SHAs, and missing process start timestamps.
- The generalized ``get_process_start_ts`` (works for any PID, not just bridge).
- The per-process machine-role gate (bridge role + plist; worker projects).
- The swallowed-write inversion guard (missing beacon → unknown warn, exit 0 —
  never FAILED, never a restart trigger).
- SHA-form round trip: the real beacon writer + the real classifier agree
  (fails hard if a full 40-char SHA ever leaks into the beacon).
- Beacon write failure never crashes (best-effort contract).
- ``verify_release`` CLI exit codes, summary lines, --skip-bridge, the
  restart-marker skip signal (Decision 27), and the --since beacon poll.

No production Redis, no live services: everything runs against tmp_path git
repos with mocked pids / ``ps`` timestamps.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from monitoring.boot_beacon import write_boot_beacon
from scripts.update import service, verify_release
from scripts.update.git import get_short_sha
from scripts.update.service import (
    get_process_start_ts,
    read_boot_beacon,
    verify_running_release,
)

pytestmark = pytest.mark.unit

FULL_MACHINE_CHECK = {"hostname": "test", "projects": ["p"], "bridge_projects": ["p"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(repo: Path, relpath: str, msg: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {msg}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg, "--no-verify")


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


def _write_beacon(repo: Path, name: str, sha: str, ts: float) -> None:
    iso = datetime.fromtimestamp(ts, UTC).isoformat()
    (repo / "data" / f"{name}_boot_sha").write_text(f"{sha}\n{iso}\n")


PROC_START_TS = 1_000_000.0
FRESH_TS = PROC_START_TS + 100  # beacon written after the process image started


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


# ---------------------------------------------------------------------------
# Classification matrix
# ---------------------------------------------------------------------------


def test_missing_beacon_classifies_unknown(repo, live_processes):
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "unknown"
    assert results["worker"]["classification"] == "unknown"
    assert results["bridge"]["boot_sha"] is None


def test_empty_beacon_classifies_unknown(repo, live_processes):
    (repo / "data" / "bridge_boot_sha").write_text("")
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "unknown"


@pytest.mark.parametrize(
    "content",
    ["abc1234\n", "abc1234\nnot-a-timestamp\n", "\n2026-07-05T00:00:00+00:00\n"],
    ids=["missing-ts-line", "garbage-ts", "missing-sha"],
)
def test_malformed_beacon_classifies_unknown(repo, live_processes, content):
    (repo / "data" / "bridge_boot_sha").write_text(content)
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "unknown"


def test_boot_sha_at_head_classifies_matches(repo, live_processes):
    """Empty relevant range — trivial subcase boot_sha == HEAD."""
    head = get_short_sha(repo)
    _write_beacon(repo, "bridge", head, FRESH_TS)
    _write_beacon(repo, "worker", head, FRESH_TS)
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "matches"
    assert results["worker"]["classification"] == "matches"


def test_docs_only_commits_ahead_classify_matches(repo, live_processes):
    """The #1091-consistency regression guard: docs-only commits never → stale."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "bridge", boot_sha, FRESH_TS)
    _write_beacon(repo, "worker", boot_sha, FRESH_TS)
    _commit(repo, "docs/plans/some-plan.md", "docs-only commit")
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "matches"
    assert results["worker"]["classification"] == "matches"


def test_relevant_commit_ahead_classifies_stale(repo, live_processes):
    """bridge/ is in both path sets — a bridge/ commit stales both processes."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "bridge", boot_sha, FRESH_TS)
    _write_beacon(repo, "worker", boot_sha, FRESH_TS)
    _commit(repo, "bridge/new_handler.py", "bridge-relevant commit")
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "stale"
    assert results["worker"]["classification"] == "stale"


def test_path_sets_diverge_per_process(repo, live_processes):
    """config/ is bridge-relevant only; worker stays matches (per-process sets)."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "bridge", boot_sha, FRESH_TS)
    _write_beacon(repo, "worker", boot_sha, FRESH_TS)
    _commit(repo, "config/settings.py", "config commit")
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "stale"
    assert results["worker"]["classification"] == "matches"


def test_orphaned_beacon_classifies_unknown(repo, live_processes):
    """beacon_ts <= process_start_ts → the beacon predates the image → unknown."""
    boot_sha = get_short_sha(repo)
    _write_beacon(repo, "bridge", boot_sha, PROC_START_TS - 100)
    _commit(repo, "bridge/new_handler.py", "bridge-relevant commit")
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    # Positive staleness only — an orphaned beacon can never escalate to stale.
    assert results["bridge"]["classification"] == "unknown"


def test_unresolvable_boot_sha_classifies_unknown(repo, live_processes):
    _write_beacon(repo, "bridge", "0000000", FRESH_TS)
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "unknown"


def test_process_start_ts_none_classifies_unknown(repo, live_processes, monkeypatch):
    monkeypatch.setattr(service, "get_process_start_ts", lambda pid: None)
    _write_beacon(repo, "bridge", get_short_sha(repo), FRESH_TS)
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["classification"] == "unknown"
    assert results["bridge"]["process_start_ts"] is None


def test_process_not_running_classifies_unknown(repo, live_processes, monkeypatch):
    monkeypatch.setattr(service, "get_bridge_pid", lambda: None)
    _write_beacon(repo, "bridge", get_short_sha(repo), FRESH_TS)
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["bridge"]["running"] is False
    assert results["bridge"]["classification"] == "unknown"


# ---------------------------------------------------------------------------
# get_process_start_ts (generalized, any-PID)
# ---------------------------------------------------------------------------


def test_get_process_start_ts_works_for_arbitrary_pid():
    """The shared helper computes an absolute start ts for a non-bridge PID."""
    ts = get_process_start_ts(os.getpid())
    assert ts is not None
    assert isinstance(ts, float)
    assert ts > 1577836800  # after 2020-01-01
    assert ts <= time.time() + 60


def test_get_process_start_ts_nonexistent_pid_returns_none():
    assert get_process_start_ts(999999999) is None


# ---------------------------------------------------------------------------
# Machine-role gate
# ---------------------------------------------------------------------------


def test_no_bridge_role_skips_bridge(repo, live_processes):
    results = verify_running_release(
        repo, "HEAD", {"hostname": "t", "projects": ["p"], "bridge_projects": []}
    )
    assert "bridge" not in results
    assert "worker" in results


def test_no_worker_role_skips_worker(repo, live_processes):
    results = verify_running_release(
        repo, "HEAD", {"hostname": "t", "projects": [], "bridge_projects": []}
    )
    assert results == {}


def test_bridge_role_without_plist_skips_bridge(repo, live_processes, monkeypatch):
    """Decision 23: verify shares the restart gate's on-disk plist signal."""
    monkeypatch.setattr(service, "BRIDGE_PLIST_PATH", Path("/nonexistent/bridge.plist"))
    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert "bridge" not in results
    assert "worker" in results


# ---------------------------------------------------------------------------
# Beacon writer: round trip + best-effort contract
# ---------------------------------------------------------------------------


def test_beacon_round_trip_classifies_matches(repo, live_processes, monkeypatch):
    """Write with the REAL writer, classify with the REAL classifier.

    Fails hard if a full 40-char SHA ever leaks into the beacon: writer and
    classifier must share the short-SHA representation by construction.
    """
    monkeypatch.setattr(service, "get_process_start_ts", lambda pid: time.time() - 3600)
    assert write_boot_beacon("worker", project_dir=repo) is True
    assert write_boot_beacon("bridge", project_dir=repo) is True

    beacon = read_boot_beacon(repo / "data" / "worker_boot_sha")
    assert beacon is not None
    boot_sha, beacon_ts = beacon
    assert boot_sha == get_short_sha(repo)
    assert len(boot_sha) < 40, "full 40-char SHA leaked into the beacon"

    results = verify_running_release(repo, "HEAD", FULL_MACHINE_CHECK)
    assert results["worker"]["classification"] == "matches"
    assert results["bridge"]["classification"] == "matches"


def test_beacon_write_failure_is_swallowed(repo, caplog):
    """Unwritable data/ → returns False, logs a warning, never raises."""
    data = repo / "data"
    for child in data.iterdir():
        child.unlink()
    data.rmdir()
    data.write_text("not a directory")  # mkdir(data) now fails

    with caplog.at_level("WARNING"):
        assert write_boot_beacon("worker", project_dir=repo) is False
    assert any("Boot beacon write failed" in r.message for r in caplog.records)


def test_swallowed_write_never_inverts_to_failed(repo, live_processes, monkeypatch, capsys):
    """Missing beacon (swallowed write) → unknown warn + exit 0 — never FAILED."""
    monkeypatch.setattr(
        verify_release.verify, "check_machine_identity", lambda pd: FULL_MACHINE_CHECK
    )
    exit_code = verify_release.main(["--project-dir", str(repo)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "FAILED" not in out
    assert "could not be confirmed" in out


# ---------------------------------------------------------------------------
# verify_release CLI
# ---------------------------------------------------------------------------


def _canned_results(bridge_cls: str | None, worker_cls: str | None) -> dict:
    results = {}
    for name, cls in (("bridge", bridge_cls), ("worker", worker_cls)):
        if cls is None:
            continue
        results[name] = {
            "running": True,
            "boot_sha": "659756a4" if cls == "stale" else "aaaaaaa",
            "beacon_ts": FRESH_TS,
            "process_start_ts": PROC_START_TS,
            "classification": cls,
        }
    return results


@pytest.fixture
def cli_env(repo, monkeypatch):
    """Point the CLI at the tmp repo with a full machine role."""
    monkeypatch.setattr(
        verify_release.verify, "check_machine_identity", lambda pd: FULL_MACHINE_CHECK
    )
    return repo


def test_cli_exit_1_and_summary_on_stale(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("stale", "matches"),
    )
    head = get_short_sha(cli_env)
    exit_code = verify_release.main(["--project-dir", str(cli_env)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"bridge running 659756a4 but HEAD is {head}" in out
    assert "FAILED" in out


def test_cli_exit_0_on_all_matches(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("matches", "matches"),
    )
    exit_code = verify_release.main(["--project-dir", str(cli_env)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "release verify OK" in out
    assert "bridge matches, worker matches" in out


def test_cli_unknown_warns_but_exits_0(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("unknown", "matches"),
    )
    exit_code = verify_release.main(["--project-dir", str(cli_env)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WARNING: bridge release could not be confirmed" in out


def test_cli_skip_bridge_ignores_stale_bridge(cli_env, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("stale", "matches"),
    )
    exit_code = verify_release.main(["--skip-bridge", "--project-dir", str(cli_env)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "bridge" not in out.lower() or "FAILED" not in out


def test_cli_fresh_restart_marker_shares_skip_signal(cli_env, monkeypatch, capsys):
    """Decision 27: a fresh update-restart-in-progress marker skips bridge
    escalation even without --skip-bridge."""
    (cli_env / "data" / "update-restart-in-progress").write_text(str(time.time()))
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("stale", "matches"),
    )
    exit_code = verify_release.main(["--project-dir", str(cli_env)])
    assert exit_code == 0
    assert "FAILED" not in capsys.readouterr().out


def test_cli_stale_restart_marker_does_not_skip(cli_env, monkeypatch):
    """An aged-out marker no longer suppresses bridge escalation."""
    marker = cli_env / "data" / "update-restart-in-progress"
    marker.write_text("old")
    old = time.time() - (verify_release.UPDATE_RESTART_MARKER_TTL_SECONDS + 10)
    os.utime(marker, (old, old))
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results("stale", "matches"),
    )
    assert verify_release.main(["--project-dir", str(cli_env)]) == 1


def test_cli_since_poll_forces_stale_when_beacon_never_freshens(cli_env, monkeypatch, capsys):
    """A worker beacon that never freshens past --since → stale, exit 1 —
    the worker failed to come up on new code within the bounded window."""
    monkeypatch.setattr(verify_release, "POLL_INTERVAL_SECONDS", 0)
    since = time.time()
    _write_beacon(cli_env, "worker", "aaaaaaa", since - 500)  # stale beacon
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results(None, "matches"),
    )
    exit_code = verify_release.main(["--since", str(since), "--project-dir", str(cli_env)])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "worker running" in out


def test_cli_since_poll_passes_on_fresh_beacon(cli_env, monkeypatch):
    monkeypatch.setattr(verify_release, "POLL_INTERVAL_SECONDS", 0)
    since = time.time() - 60
    _write_beacon(cli_env, "worker", "aaaaaaa", since + 30)  # fresher than since
    monkeypatch.setattr(
        verify_release.service,
        "verify_running_release",
        lambda pd, head, mc: _canned_results(None, "matches"),
    )
    assert verify_release.main(["--since", str(since), "--project-dir", str(cli_env)]) == 0


def test_cli_no_in_role_processes_exits_0(repo, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_release.verify,
        "check_machine_identity",
        lambda pd: {"hostname": "t", "projects": [], "bridge_projects": []},
    )
    exit_code = verify_release.main(["--project-dir", str(repo)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no in-role processes" in out
