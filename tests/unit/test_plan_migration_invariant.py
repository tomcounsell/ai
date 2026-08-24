"""Tier 0 regression test: the plan-migration invariant (issue #1900).

Guards against exactly the failure mode that regressed #1394's earlier fix: a
future prose-only "fix" (hand-written doc instructions instead of a deterministic
script call) silently dropping enforcement during a command->skill refactor.

Two layers:

1. A behavioral regression test: a plan whose tracking issue is CLOSED must no
   longer live in docs/plans/ root after migrate_plan_to_completed() runs.
2. Static assertions (parsed YAML / AST-ish substring checks, no live network
   calls) that the two enforcement call sites still exist:
   - config/reflections.yaml registers `merged-branch-cleanup` with the
     expected callable (its `enabled` value is asserted separately by the
     follow-up arm-reflection task -- this task ships it report-only).
   - reflections/housekeeping/merged_branch_cleanup.py actually calls
     migrate_plan_to_completed (not just a comment referencing it).
   - docs/sdlc/do-merge.md documents the deterministic --issue call, not a
     hand `git mv` instruction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from agent.reflection_scheduler import _resolve_registry_path
from scripts.migrate_completed_plan import COMPLETED_PLANS_DIR, migrate_plan_to_completed

REPO_ROOT = Path(__file__).resolve().parents[2]


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


class TestMigrationInvariantBehavior:
    """A plan with a CLOSED tracking issue must leave docs/plans/ root."""

    def test_closed_issue_plan_leaves_root(self, tmp_path):
        repo = tmp_path / "repo"
        plans_dir = repo / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        archive_dir = repo / COMPLETED_PLANS_DIR
        archive_dir.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")

        plan = plans_dir / "some-finished-plan.md"
        plan.write_text(
            "---\ntracking: https://github.com/tomcounsell/ai/issues/1900\n---\n"
            "# Some Finished Plan\n"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "migrated"
        assert not plan.exists(), "plan must no longer live in docs/plans/ root"
        assert (archive_dir / "some-finished-plan.md").exists()


class TestArchiveLocationCouplings:
    """The archive destination has three consumers that must not drift (#2878).

    `COMPLETED_PLANS_DIR` is the single definition of where a shipped plan
    lands. Moving it without moving these three together produces failures far
    from the edit: a blocked mover commit, or archived plans silently
    re-entering the doc-audit surface the move exists to clear.
    """

    def test_archive_is_outside_the_live_plan_prefix(self):
        """The whole point of the move: archived plans stop matching the
        `docs/plans` prefix that four hook validators and the docs auditor use
        to mean "live plan". If the archive slid back under that prefix the
        move would be cosmetic.
        """
        assert not COMPLETED_PLANS_DIR.startswith("docs/plans"), (
            f"COMPLETED_PLANS_DIR is {COMPLETED_PLANS_DIR!r}, which is back under "
            "the live-plan prefix -- archived plans would again be treated as "
            "live plans by the plan-structure validators and the docs auditor"
        )

    def test_mover_commit_survives_the_issue_disposition_gate(self):
        """Both endpoints of the mover's rename must be disposition-exempt.

        The mover commits on `main` with a fixed message carrying no
        disposition. Git stages the deletion under `docs/plans/` and the
        addition under the archive; either one missing from EXEMPT_PREFIXES
        blocks the commit and strands the move.
        """
        from scripts.check_issue_disposition import EXEMPT_PREFIXES

        assert (COMPLETED_PLANS_DIR + "/").startswith(EXEMPT_PREFIXES), (
            f"{COMPLETED_PLANS_DIR}/ is not in check_issue_disposition."
            f"EXEMPT_PREFIXES {EXEMPT_PREFIXES} -- the mover's own commit would "
            "be refused by the commit-msg hook"
        )
        assert "docs/plans/" in EXEMPT_PREFIXES, "the rename's source side must stay exempt too"

    def test_docs_auditor_excludes_the_archive(self):
        """The auditor's two plan-exclusion filters must name the archive.

        Both are literal prefix comparisons, not glob walks, so an archive
        outside `docs/plans/` is invisible to them unless listed explicitly --
        which would pull 547 historical files back into the audit surface.
        """
        source = (REPO_ROOT / "reflections" / "docs_auditor.py").read_text()
        assert COMPLETED_PLANS_DIR in source, (
            f"reflections/docs_auditor.py does not mention {COMPLETED_PLANS_DIR} -- "
            "its plan-exclusion filters would let archived plans back into the "
            "neighborhood grep and the PR-changed-files scan"
        )


class TestStaticEnforcementAssertions:
    """Guards against a future prose-only regression of the enforcement sites."""

    def test_reflections_yaml_registers_merged_branch_cleanup(self):
        registry_path = _resolve_registry_path()
        assert registry_path.exists(), (
            f"reflections registry not found at {registry_path} -- "
            "cannot verify the merged-branch-cleanup backstop is registered"
        )
        with open(registry_path) as f:
            data = yaml.safe_load(f)
        entries = {r["name"]: r for r in data["reflections"]}

        assert "merged-branch-cleanup" in entries, (
            "merged-branch-cleanup entry missing from the reflections registry -- "
            "the plan-migration backstop is unregistered dead code without it"
        )
        entry = entries["merged-branch-cleanup"]
        assert entry["callable"] == "reflections.maintenance.run_branch_plan_cleanup", (
            "merged-branch-cleanup callable drifted from the expected module path"
        )
        # The registry `enabled` flag is asserted PRESENT, deliberately not
        # `== True`: the flag is per-machine mutable (the vault copy is the
        # source of truth, and a human disarm must stick -- see the one-shot
        # marker in scripts/update/reflection_arm.py). The durable code-level
        # arm is asserted separately via MIGRATION_APPLY_ENABLED in
        # tests/unit/reflections/test_merged_branch_cleanup.py.
        assert "enabled" in entry

    def test_reflection_source_calls_migrate_plan_to_completed(self):
        source_path = REPO_ROOT / "reflections" / "housekeeping" / "merged_branch_cleanup.py"
        assert source_path.exists()
        source = source_path.read_text()

        assert "from scripts.migrate_completed_plan import" in source
        assert "migrate_plan_to_completed" in source
        # A real call site, not merely an import: the name must appear applied
        # as a function call (parenthesized), not just imported/mentioned.
        assert "migrate_plan_to_completed(" in source, (
            "merged_branch_cleanup.py imports migrate_plan_to_completed but never "
            "calls it -- the backstop would be dead code"
        )

    def test_do_merge_doc_uses_deterministic_issue_call(self):
        doc_path = REPO_ROOT / "docs" / "sdlc" / "do-merge.md"
        assert doc_path.exists()
        text = doc_path.read_text()

        assert "migrate_completed_plan.py --issue" in text, (
            "do-merge.md must document the deterministic --issue call, not a "
            "hand-written `git mv` instruction (the exact regression that hit #1394)"
        )
        # Guard against the phantom function reference regressing back in.
        assert "_handle_merge_completion" not in text


class TestNoPhantomFunctionReference:
    """`_handle_merge_completion()` does not exist anywhere in the codebase --
    do-merge.md must not claim otherwise."""

    def test_handle_merge_completion_has_zero_python_definitions(self):
        this_file = Path(__file__).resolve()
        # Use `git grep` (tracked *.py, atomic index snapshot) rather than a
        # recursive `grep -r REPO_ROOT`. The latter walks volatile runtime trees
        # (.venv/.git/data/logs/__pycache__) that concurrent xdist siblings
        # create/delete; a directory vanishing mid-walk makes grep exit 2 and
        # trips the returncode assertion below. `git grep` reads the index, so it
        # is race-free and never scans untracked runtime artifacts (#2093). The
        # pathspec excludes this test file (the only place the string legitimately
        # appears).
        result = subprocess.run(
            [
                "git",
                "grep",
                "-n",
                "_handle_merge_completion",
                "--",
                "*.py",
                f":!{this_file.relative_to(REPO_ROOT)}",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        # git grep exit code 1 == no matches found (expected). Exit 2 == real error.
        assert result.returncode in (0, 1), f"git grep failed: {result.stderr}"
        assert result.stdout.strip() == "", (
            "Found references to the nonexistent _handle_merge_completion() -- "
            f"either it now exists (update do-merge.md's wording) or these are "
            f"stray phantom references to remove:\n{result.stdout}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
