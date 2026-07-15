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
from scripts.migrate_completed_plan import migrate_plan_to_completed

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
        (plans_dir / "completed").mkdir(parents=True)
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
        assert (plans_dir / "completed" / "some-finished-plan.md").exists()


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
