"""Tests for reflections/housekeeping/merged_branch_cleanup.py (issue #1900, Tier 0).

Covers the plan-migration backstop this reflection was extended with:

- The migration gate is keyed on the plan's OWN ``tracking:`` frontmatter issue,
  not the broader prose ``#N`` / URL scan used by the pre-existing closed_issue
  finding.
- The gate is non-vacuous: only a literal ``"closed"`` state migrates; an
  ``"unknown"`` state (gh outage/timeout) must never migrate (Blocker 2).
- The migration decision no longer hides behind the ``is_complete`` short-circuit
  (Blocker 1): an all-checkboxes-complete plan with a closed tracking issue must
  still migrate.
- The per-run cap is honored.
- ``MIGRATION_APPLY_ENABLED = True`` (armed as of the arm-reflection task):
  run() performs the git mv for real. A forced-off variant is still covered
  to prove report-only mode remains correct if the flag is ever flipped back.

All git/gh subprocess calls are mocked -- no real network, no real git repo --
except the dedicated end-to-end tests that verify apply-on (the shipped,
armed state) and apply-off (forced via monkeypatch) against a real temp git
repo (the strongest guarantee that each mode does exactly what it claims to
disk).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import reflections.housekeeping.merged_branch_cleanup as mbc

PLAN_TEMPLATE = textwrap.dedent(
    """\
    ---
    tracking: https://github.com/tomcounsell/ai/issues/{issue}
    ---
    # {title}

    {checkbox_section}
    """
)


def _write_plan(
    plans_dir: Path,
    name: str,
    issue: int,
    *,
    all_checked: bool = False,
    extra_refs: list[int] | None = None,
) -> Path:
    checkbox_section = "- [x] done\n" if all_checked else "- [ ] not done\n"
    if extra_refs:
        checkbox_section += "\n".join(f"See also #{r}" for r in extra_refs) + "\n"
    text = PLAN_TEMPLATE.format(issue=issue, title=name, checkbox_section=checkbox_section)
    path = plans_dir / f"{name}.md"
    path.write_text(text)
    return path


def _make_proc(stdout: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


def _gh_dispatcher(issue_states: dict[int, str]):
    """Build an async create_subprocess_exec stand-in for git + gh calls.

    - `git branch --merged` / `git branch -d`: no-op, pretend nothing to clean.
    - `gh issue view <N> --json state`: resolve from `issue_states`; a missing
      or "unknown" entry simulates a gh failure (non-zero exit -> "unknown").
    - `gh issue list --state open --search <name>`: pretend no open issue found
      (only reached for plans this test suite doesn't route there).
    """

    async def _dispatch(*args, **kwargs):
        if args[0] == "git":
            return _make_proc(b"", returncode=0)
        if args[0] == "gh" and args[1:3] == ("issue", "view"):
            issue_num = int(args[3])
            state = issue_states.get(issue_num)
            if state is None or state == "unknown":
                return _make_proc(b"", returncode=1)
            return _make_proc(json.dumps({"state": state}).encode(), returncode=0)
        if args[0] == "gh" and args[1:3] == ("issue", "list"):
            return _make_proc(b"", returncode=0)
        return _make_proc(b"", returncode=1)

    return _dispatch


@pytest.fixture
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    return d


def _run(monkeypatch, tmp_path: Path, issue_states: dict[int, str], migrate_mock):
    monkeypatch.setattr(mbc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        mbc,
        "load_local_projects",
        lambda: [{"github": "tomcounsell/ai", "working_directory": str(tmp_path)}],
    )
    monkeypatch.setattr(mbc, "migrate_plan_to_completed", migrate_mock)
    with patch.object(
        mbc.asyncio,
        "create_subprocess_exec",
        new_callable=AsyncMock,
        side_effect=_gh_dispatcher(issue_states),
    ):
        return asyncio.run(mbc.run())


class TestMigrationGate:
    def test_closed_tracking_issue_migrates(self, monkeypatch, tmp_path, plans_dir):
        """A plan whose own tracking issue is closed gets a migration action."""
        _write_plan(plans_dir, "closed-plan", issue=100)
        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append((plan_path, apply))
            return "migrated"

        result = _run(monkeypatch, tmp_path, {100: "closed"}, fake_migrate)

        assert len(calls) == 1
        assert calls[0][0].name == "closed-plan.md"
        assert calls[0][1] is mbc.MIGRATION_APPLY_ENABLED
        assert any("closed-plan.md" in f for f in result["findings"])

    def test_open_tracking_issue_is_skipped(self, monkeypatch, tmp_path, plans_dir):
        """An open tracking issue never triggers migration."""
        _write_plan(plans_dir, "open-plan", issue=200)
        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append(plan_path)
            return "migrated"

        _run(monkeypatch, tmp_path, {200: "open"}, fake_migrate)

        assert calls == []

    def test_closed_sibling_reference_does_not_migrate(self, monkeypatch, tmp_path, plans_dir):
        """A plan whose OWN tracking issue is open is not migrated, even though it
        mentions a different, closed issue in prose. Proves the gate reads the
        frontmatter tracking issue specifically, not the broader `refs` set."""
        _write_plan(plans_dir, "sibling-plan", issue=300, extra_refs=[301])
        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append(plan_path)
            return "migrated"

        _run(monkeypatch, tmp_path, {300: "open", 301: "closed"}, fake_migrate)

        assert calls == []

    def test_all_checkboxes_complete_and_closed_still_migrates(
        self, monkeypatch, tmp_path, plans_dir
    ):
        """Regression test for Blocker 1: an all-checkboxes-complete plan must not
        short-circuit past the tracking-issue-closed migration gate."""
        _write_plan(plans_dir, "complete-plan", issue=400, all_checked=True)
        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append(plan_path)
            return "migrated"

        result = _run(monkeypatch, tmp_path, {400: "closed"}, fake_migrate)

        assert len(calls) == 1
        assert calls[0].name == "complete-plan.md"
        # The redundant "Plan complete ... delete" finding is suppressed in favor
        # of the migration finding -- telling the operator to delete a plan the
        # same run just migrated is contradictory (PR #1903 review Nit).
        assert not any("Plan complete: complete-plan" in f for f in result["findings"])
        assert any("complete-plan.md -> completed/" in f for f in result["findings"])

    def test_unknown_issue_state_never_migrates(self, monkeypatch, tmp_path, plans_dir):
        """Regression test for Blocker 2: a gh outage/timeout (state "unknown")
        must never be treated as an implicit "closed" -- the gate is a single
        literal-equality check, not a vacuously-true `all()` over an empty list."""
        _write_plan(plans_dir, "unknown-plan", issue=500)
        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append(plan_path)
            return "migrated"

        # No entry for 500 in issue_states -> dispatcher simulates gh failure -> "unknown".
        _run(monkeypatch, tmp_path, {}, fake_migrate)

        assert calls == []

    def test_per_run_cap_honored(self, monkeypatch, tmp_path, plans_dir):
        """More eligible plans than the cap -> only the cap migrates, the rest defer."""
        cap = mbc.MIGRATION_PER_RUN_CAP
        n_plans = cap + 3
        issue_states = {}
        for i in range(n_plans):
            issue_num = 1000 + i
            _write_plan(plans_dir, f"capped-plan-{i}", issue=issue_num)
            issue_states[issue_num] = "closed"

        calls = []

        def fake_migrate(plan_path, *, apply):
            calls.append(plan_path)
            return "migrated"

        result = _run(monkeypatch, tmp_path, issue_states, fake_migrate)

        assert len(calls) == cap
        deferred = [f for f in result["findings"] if "Deferred migration" in f]
        assert len(deferred) == n_plans - cap

    def test_report_only_fallback_verdict_does_not_count_as_migrated(
        self, monkeypatch, tmp_path, plans_dir
    ):
        """Tech Debt fix (PR #1903 review): when migrate_plan_to_completed()
        returns a report-only fallback verdict (dirty-tree-skip, here) rather
        than an actual "migrated" verdict, the reflection must not count it
        towards stats["migrated"] or consume the per-run migration cap -- the
        common case is the reflection worker's checkout not being a clean
        `main`, and a run that moved nothing must not report "N migrated"."""
        _write_plan(plans_dir, "dirty-tree-plan", issue=700)

        def fake_migrate(plan_path, *, apply):
            return "dirty-tree-skip"

        result = _run(monkeypatch, tmp_path, {700: "closed"}, fake_migrate)

        assert "0 migrated" in result["summary"]
        # The operator-facing finding still reports the actual verdict (never
        # silent), it just doesn't count as a real migration.
        assert any(
            "Migrated (dirty-tree-skip): dirty-tree-plan.md" in f for f in result["findings"]
        )

    def test_complete_and_migrated_plan_emits_only_migration_finding(
        self, monkeypatch, tmp_path, plans_dir
    ):
        """Nit fix (PR #1903 review): a plan that is both all-checkboxes-complete
        AND has a closed tracking issue must emit only the migration finding in
        a given run, not also the stale "Plan complete ... delete" finding --
        the two are contradictory (delete vs. move) for the same plan/run."""
        _write_plan(plans_dir, "complete-and-closed", issue=701, all_checked=True)

        def fake_migrate(plan_path, *, apply):
            return "migrated"

        result = _run(monkeypatch, tmp_path, {701: "closed"}, fake_migrate)

        complete_findings = [f for f in result["findings"] if "Plan complete" in f]
        migration_findings = [f for f in result["findings"] if "complete-and-closed.md ->" in f]
        assert complete_findings == []
        assert len(migration_findings) == 1


class TestApplyOnMigrates:
    def test_apply_on_moves_plan_on_disk(self, monkeypatch, tmp_path):
        """End-to-end: with MIGRATION_APPLY_ENABLED armed (the shipped state as
        of the arm-reflection task), run() against a REAL git repo performs
        the git mv -- the plan file actually lands in docs/plans/completed/.
        """
        assert mbc.MIGRATION_APPLY_ENABLED is True, (
            "arm-reflection armed this permanently; the reflection registry "
            "entry (config/reflections.yaml) is enabled: true to match"
        )

        repo = tmp_path / "repo"
        plans_dir = repo / "docs" / "plans"
        (plans_dir / "completed").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        plan = _write_plan(plans_dir, "e2e-plan", issue=600)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")

        monkeypatch.setattr(mbc, "PROJECT_ROOT", repo)
        monkeypatch.setattr(
            mbc,
            "load_local_projects",
            lambda: [{"github": "tomcounsell/ai", "working_directory": str(repo)}],
        )
        # migrate_plan_to_completed is NOT mocked here -- exercise the real
        # primitive end-to-end against MIGRATION_APPLY_ENABLED=True.

        with patch.object(
            mbc.asyncio,
            "create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=_gh_dispatcher({600: "closed"}),
        ):
            result = asyncio.run(mbc.run())

        assert not plan.exists(), "armed mode must perform the git mv"
        assert (plans_dir / "completed" / "e2e-plan.md").exists()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == "", "the primitive commits its own git mv"
        assert any("e2e-plan.md" in f for f in result["findings"])
        assert "migrated" in result["summary"]

    def test_apply_off_moves_nothing_on_disk(self, monkeypatch, tmp_path):
        """Report-only mode remains available and correct: forcing
        MIGRATION_APPLY_ENABLED off (regardless of the armed default) still
        reports eligibility without performing any git mv -- the plan file
        stays exactly where it was.
        """
        monkeypatch.setattr(mbc, "MIGRATION_APPLY_ENABLED", False)

        repo = tmp_path / "repo"
        plans_dir = repo / "docs" / "plans"
        (plans_dir / "completed").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        plan = _write_plan(plans_dir, "e2e-plan-off", issue=601)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")

        monkeypatch.setattr(mbc, "PROJECT_ROOT", repo)
        monkeypatch.setattr(
            mbc,
            "load_local_projects",
            lambda: [{"github": "tomcounsell/ai", "working_directory": str(repo)}],
        )
        # migrate_plan_to_completed is NOT mocked here -- exercise the real
        # primitive end-to-end against a forced MIGRATION_APPLY_ENABLED=False.

        with patch.object(
            mbc.asyncio,
            "create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=_gh_dispatcher({601: "closed"}),
        ):
            result = asyncio.run(mbc.run())

        assert plan.exists(), "report-only mode must never perform the git mv"
        assert not (plans_dir / "completed" / "e2e-plan-off.md").exists()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == "", "report-only mode must leave the tree clean"
        assert any("e2e-plan-off.md" in f for f in result["findings"])
        assert "would-migrate" in result["summary"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result
