"""Tests for the Step 1.659 update hook that repoints reflection callables.

Issue #2875.

Every test passes explicit ``targets`` — without it the wrapper resolves the
REAL vault registry (``~/Desktop/Valor/reflections.yaml``) and rewrites it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.update.reflections_callables import (
    PROBE_SENTINEL,
    run_reflections_callables_migration,
    run_registry_probe,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_registry(path: Path, callables: list[str]) -> Path:
    body = ["reflections:"]
    for i, c in enumerate(callables):
        body += [
            f"  - name: entry-{i}",
            "    every: 60s",
            "    execution_type: function",
            f'    callable: "{c}"',
        ]
    path.write_text("\n".join(body) + "\n")
    return path


def test_rewrites_registry_and_reports_counts(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml",
        [
            "agent.sustainability.circuit_health_gate",
            "agent.sustainability.session_recovery_drip",
            "reflections.maintenance.run_disk_space_check",
        ],
    )

    result = run_reflections_callables_migration(_REPO_ROOT, targets=[target])

    assert result.success is True
    assert result.action == "rewrote"
    assert result.rewrites_count == 2
    assert result.targets == [str(target)]
    assert "agent.sustainability" not in target.read_text()


def test_second_run_is_a_noop(tmp_path):
    target = _write_registry(
        tmp_path / "reflections.yaml", ["agent.sustainability.failure_loop_detector"]
    )

    run_reflections_callables_migration(_REPO_ROOT, targets=[target])
    after_first = target.read_text()
    result = run_reflections_callables_migration(_REPO_ROOT, targets=[target])

    assert result.success is True
    assert result.action == "noop"
    assert result.rewrites_count == 0
    assert target.read_text() == after_first


def test_absent_registry_is_a_noop_not_an_error(tmp_path):
    """A machine with no vault and no materialized config must not warn."""
    result = run_reflections_callables_migration(_REPO_ROOT, targets=[tmp_path / "absent.yaml"])

    assert result.success is True
    assert result.action == "noop"


def test_missing_migration_script_is_a_soft_error(tmp_path, monkeypatch):
    """A partial checkout surfaces a warning rather than crashing /update."""
    monkeypatch.setattr(
        "scripts.update.reflections_callables.Path.exists", lambda self: False, raising=False
    )

    result = run_reflections_callables_migration(tmp_path, targets=[tmp_path / "x.yaml"])

    assert result.success is False
    assert result.action == "error"
    assert "migration script missing" in (result.error or "")


# ─── Step 4.65 acceptance probe (run_registry_probe) ────────────────────────
#
# The probe is what Step 4.65 actually gates on, because
# `ReflectionsCallablesResult.success` is a weaker proposition than the restart
# needs: it is True for `action="noop"`, and "noop" covers both *no registry at
# all* and *the line-anchored rewrite regex matched nothing*.


def _write_flow_style_registry(path: Path, dotted: str) -> Path:
    """A registry entry the Step 1.659 rewriter provably cannot see.

    `_CALLABLE_LINE_RE` is line-anchored on `    callable: <dotted>`; a
    flow-style mapping puts the key mid-line, so `rewrite_callable_lines`
    returns count=0, `migrate_yaml_callables` returns BEFORE its
    `verify_targets_importable()` call, and the wrapper reports a clean noop.
    """
    path.write_text(
        "reflections:\n"
        f"  - {{name: flow-entry, every: 60s, execution_type: function, callable: {dotted}}}\n"
    )
    return path


def test_probe_catches_the_false_green_the_migration_reports_as_noop(tmp_path, monkeypatch):
    """The exact hazard R2-TD2 names: rewriter says "noop", registry is broken."""
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.circuit_health_gate"
    )

    # The rewriter is happy — this is the false green.
    migration = run_reflections_callables_migration(_REPO_ROOT, targets=[broken])
    assert migration.success is True
    assert migration.action == "noop"
    assert "agent.sustainability" in broken.read_text()

    # The probe is not.
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")  # skip the vault: keeps this hermetic

    # project_dir is a tmp dir, never _REPO_ROOT: a failing probe stamps
    # data/registry-probe-failed, and that is the live control-plane file
    # scripts/remote-update.sh reads to decide whether to block the worker
    # restart. Pointing this at the real checkout makes a *passing* test run
    # leave a stray blocking sentinel behind on the developer's machine. The
    # probe script itself is still found via the helper's own repo root.
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    probe = run_registry_probe(fake_repo)

    assert probe.success is False
    assert "agent.sustainability" in probe.detail


def test_probe_failure_stamps_the_sentinel_remote_update_reads(tmp_path, monkeypatch):
    """The verdict must cross the process boundary to the shell half of /update."""
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.failure_loop_detector"
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    # project_dir is a tmp dir so the real repo's sentinel is never touched; the
    # probe script itself falls back to the helper's own repo root.
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    result = run_registry_probe(fake_repo)

    assert result.success is False
    assert (fake_repo / PROBE_SENTINEL).exists()


def test_probe_success_clears_a_stale_sentinel(tmp_path, monkeypatch):
    """A green run must un-block the next restart, not block it forever."""
    clean = _write_registry(
        tmp_path / "reflections.yaml", ["reflections.agents.circuit_health_gate.run"]
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(clean))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    fake_repo = tmp_path / "repo"
    (fake_repo / "data").mkdir(parents=True)
    stale = fake_repo / PROBE_SENTINEL
    stale.write_text("from a previous failing run\n")

    result = run_registry_probe(fake_repo)

    assert result.success is True, result.detail
    assert not stale.exists()


def test_missing_probe_script_fails_closed(tmp_path, monkeypatch):
    """The probe not running proves nothing — it must not read as a pass."""
    monkeypatch.setattr(
        "scripts.update.reflections_callables.Path.exists", lambda self: False, raising=False
    )

    result = run_registry_probe(tmp_path)

    assert result.success is False
    assert "probe script missing" in result.detail


def test_unstampable_failure_sentinel_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The false-green hazard: probe fails, sentinel cannot be written.

    `remote-update.sh` reads `[ -f ]`, so an ABSENT sentinel is its green light.
    A swallowed write error on the failure path therefore produces exactly the
    pass the gate exists to prevent. `sentinel_recorded=False` is how that
    crosses back to `run.py`, which escalates it to a non-zero exit.
    """
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.session_count_throttle"
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    fake_repo = tmp_path / "repo"
    data_dir = fake_repo / "data"
    data_dir.mkdir(parents=True)
    data_dir.chmod(0o500)  # readable + traversable, not writable
    try:
        result = run_registry_probe(fake_repo)
    finally:
        data_dir.chmod(0o700)

    assert result.success is False
    assert result.sentinel_recorded is False
    assert not (fake_repo / PROBE_SENTINEL).exists()


def test_writable_failure_sentinel_reports_recorded(tmp_path, monkeypatch):
    """Green control for the case above: same failure, writable data/ dir."""
    broken = _write_flow_style_registry(
        tmp_path / "reflections.yaml", "agent.sustainability.session_count_throttle"
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(broken))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    result = run_registry_probe(fake_repo)

    assert result.success is False
    assert result.sentinel_recorded is True
    assert (fake_repo / PROBE_SENTINEL).exists()


def _stub_probe(project_dir: Path, exit_code: int, *, stdout: str = "", stderr: str = "") -> Path:
    """Plant a probe script in `project_dir` that exits with a chosen code.

    `run_registry_probe` prefers `project_dir/scripts/verify_registry_without_shim.py`
    over the helper's own repo copy, so this drives the helper's exit-code
    branching directly. Deliberate: what these cases pin is which verdict the
    *helper* derives from each code, not how the probe picks candidates —
    `tests/unit/test_verify_registry_without_shim.py::TestMainExitCodes` owns
    that half, and forcing the real probe to see zero candidates would mean
    reaching into the machine's own checkout.
    """
    script = project_dir / "scripts" / "verify_registry_without_shim.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )
    return script


def test_no_registry_exit_is_a_non_blocking_verdict_that_still_reports(tmp_path):
    """Exit 2 must clear the sentinel and still refuse to look like a clean green.

    Both halves matter and pull opposite ways. Blocking here would wedge an
    uninstalled checkout's update cycle every 30 minutes, since nothing clears
    the sentinel but a passing probe — hence `success=True` and the stale
    sentinel removed. But a run that probed nothing is not a run that proved
    something, so `nothing_probed` carries the distinction out to Step 4.65,
    which routes it to the operator warning channel.
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "data").mkdir(parents=True)
    stale = fake_repo / PROBE_SENTINEL
    stale.write_text("from a previous failing run\n")
    _stub_probe(
        fake_repo,
        2,
        stderr="WARN: no reflections registry found, so nothing was probed; candidates: /nope\n",
    )

    result = run_registry_probe(fake_repo)

    assert result.success is True
    assert result.nothing_probed is True
    assert not stale.exists(), "a verdict with no evidence behind it must not keep blocking"
    # Read off stderr on purpose: the success path below reads only stdout, and
    # this branch's entire explanation is written to stderr.
    assert "no reflections registry found" in result.detail


def test_an_ordinary_failure_is_not_mistaken_for_the_vacuous_verdict(tmp_path):
    """Red control on the same axis: exit 1 still blocks and still stamps."""
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    _stub_probe(fake_repo, 1, stderr="FAIL: 1 of 1 registry copy(ies) did not resolve\n")

    result = run_registry_probe(fake_repo)

    assert result.success is False
    assert result.nothing_probed is False
    assert (fake_repo / PROBE_SENTINEL).exists()


def test_a_real_pass_is_not_mistaken_for_the_vacuous_verdict(tmp_path):
    """Green control: exit 0 passes with `nothing_probed` false, so the two differ."""
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    _stub_probe(fake_repo, 0, stdout="OK: 34 registry callables across 1 registry copy(ies)\n")

    result = run_registry_probe(fake_repo)

    assert result.success is True
    assert result.nothing_probed is False
    assert "34 registry callables" in result.detail
    assert not (fake_repo / PROBE_SENTINEL).exists()


def test_probe_subprocess_forces_launchd_candidate_set(tmp_path, monkeypatch):
    """Step 4.65 must probe the copies the reflection worker resolves, not the vault.

    `/update`'s own deployment vehicle is a launchd agent whose plist exports
    only PATH and HOME, so an inherited environment leaves `VALOR_LAUNCHD`
    unset and the probe reaches for `~/Desktop/Valor/reflections.yaml` — the
    path this repo documents as TCC-blocked (and hang-prone) from launchd. That
    copy is loaded by nothing: the reflection worker runs under
    `VALOR_LAUNCHD=1` and reads `config/reflections.yaml`. Probing it can only
    manufacture a fail-closed verdict about a file nothing imports.
    """
    import subprocess as _subprocess

    monkeypatch.delenv("VALOR_LAUNCHD", raising=False)
    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured.update(kwargs)
        return _subprocess.CompletedProcess(argv, 0, stdout="OK: probed\n", stderr="")

    monkeypatch.setattr("scripts.update.reflections_callables.subprocess.run", _fake_run)

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    result = run_registry_probe(fake_repo)

    assert result.success is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("VALOR_LAUNCHD") == "1"
    assert "PATH" in env, "the override must extend os.environ, not replace it"


_LATCH_START = "# >>> registry-probe-latch"
_LATCH_END = "# <<< registry-probe-latch"


def _latch_fragment() -> str:
    """Slice the live `REGISTRY_PROBE_OK` latch out of `remote-update.sh`.

    Executing the real fragment is the point. A substring assertion over the
    shell source passes just as happily when the comparison is inverted and the
    `stat` fallback is flipped open, which is precisely the direction that
    matters and precisely what this file's other guards are held to.
    """
    shell = (_REPO_ROOT / "scripts" / "remote-update.sh").read_text()
    assert _LATCH_START in shell and _LATCH_END in shell, (
        "the registry-probe latch markers are load-bearing; tests slice the fragment "
        "between them out of remote-update.sh"
    )
    return shell.split(_LATCH_START, 1)[1].split(_LATCH_END, 1)[0]


def _run_latch(project_dir: Path, started_at: int, *, break_stat: bool = False) -> bool:
    """Execute the latch fragment and return the resulting REGISTRY_PROBE_OK."""
    import subprocess

    preamble = "stat() { return 1; }\n" if break_stat else ""
    script = (
        "set -euo pipefail\n"
        f'PROJECT_DIR="{project_dir}"\n'
        f"UPDATE_RUN_STARTED_AT={started_at}\n"
        f"{preamble}"
        f"{_latch_fragment()}\n"
        # Last line only: the stale branch legitimately echoes an operator line
        # to stdout before this one.
        'printf "VERDICT=%s\\n" "$REGISTRY_PROBE_OK"\n'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    verdicts = [ln for ln in proc.stdout.splitlines() if ln.startswith("VERDICT=")]
    assert len(verdicts) == 1, proc.stdout
    return verdicts[0] == "VERDICT=true"


def _stamp_sentinel(project_dir: Path, mtime: int) -> Path:
    import os

    path = project_dir / PROBE_SENTINEL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("registry callables did not import\n")
    os.utime(path, (mtime, mtime))
    return path


def test_shell_latch_blocks_on_a_sentinel_from_this_cycle(tmp_path):
    """The gate's whole purpose: a fresh failing verdict must block the restart."""
    started = 1_700_000_000
    _stamp_sentinel(tmp_path, started + 5)

    assert _run_latch(tmp_path, started) is False


def test_shell_latch_ignores_a_sentinel_older_than_this_cycle(tmp_path):
    """`run.py --verify` also runs Step 4.65 and ignores remote-update.sh's lock.

    Without the mtime bound, a stamp it left behind would block every later
    cycle indefinitely, since nothing clears the sentinel except a passing probe.
    """
    started = 1_700_000_000
    _stamp_sentinel(tmp_path, started - 3600)

    assert _run_latch(tmp_path, started) is True


def test_shell_latch_greenlights_when_no_sentinel_exists(tmp_path):
    """Absence is the pass signal — the direction that makes the write path risky."""
    (tmp_path / "data").mkdir()

    assert _run_latch(tmp_path, 1_700_000_000) is True


def test_shell_latch_fails_closed_when_stat_is_unreadable(tmp_path):
    """An unreadable mtime on a file `[ -f ]` just proved exists must block.

    The earlier `|| echo 0` fell back to a value that can never satisfy `-ge`,
    so an anomalous `stat` silently discarded a real failing verdict as stale.

    Stamped OLDER than this cycle on purpose, so the assertion is discriminating:
    a working `stat` would call this sentinel stale and yield `true`. Only the
    far-future fallback can produce `false` here, which is the exact branch
    under test.
    """
    started = 1_700_000_000
    _stamp_sentinel(tmp_path, started - 3600)

    assert _run_latch(tmp_path, started, break_stat=True) is False


def test_remote_update_shell_consults_the_same_sentinel():
    """Pin the cross-language contract: the shell must read the path Python writes.

    These are two files in two languages with no shared constant, so the only
    thing keeping them in agreement is this assertion.
    """
    shell = (_REPO_ROOT / "scripts" / "remote-update.sh").read_text()

    assert PROBE_SENTINEL in shell, "remote-update.sh must check the sentinel run.py stamps"
    assert "REGISTRY_PROBE_OK" in shell
    assert "RESTART BLOCKED" in shell
