"""Tests for scripts/migrate_completed_plan.py.

Covers Bug 1 fix: README-based display name extraction replacing .title() mangling.
Also covers the path-independent migrate_plan_to_completed() primitive (issue
#1900, Tier 0): guarded git-mv of a root plan into the completed-plan
archive (docs/archive/plans-completed/, #2878).
"""

import contextlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Import the functions under test directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.migrate_completed_plan import (  # noqa: E402
    COMPLETED_PLANS_DIR,
    extract_feature_doc_path,
    extract_feature_name_from_index,
    migrate_plan_to_completed,
    validate_feature_doc,
    validate_feature_index,
)

# --- Fixtures ---

SAMPLE_README = textwrap.dedent("""\
    # Feature Documentation Index

    | Feature | Description | Status |
    |---------|-------------|--------|
    | [PM/Dev Session Architecture](pm-dev-session-architecture.md) | PM/Dev split | Shipped |
    | [SDLC Critique Stage](sdlc-critique-stage.md) | Automated plan validation | Shipped |
    | [AI Evaluator](ai-evaluator.md) | Semantic build evaluation | Shipped |
    | [Bridge Self-Healing](bridge-self-healing.md) | Crash recovery | Shipped |
    | [Do-Build AI Evaluator](do-build-ai-evaluator.md) | AI evaluator step | Shipped |
""")


# --- Tests for extract_feature_name_from_index ---


class TestExtractFeatureNameFromIndex:
    """Test README-based display name extraction using the real function."""

    @pytest.fixture(autouse=True)
    def _setup_readme(self, tmp_path):
        """Create a docs/features/README.md and chdir so the real function finds it."""
        readme_path = tmp_path / "docs" / "features" / "README.md"
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(SAMPLE_README)
        self._tmp = tmp_path

    def _extract(self, filename: str) -> str | None:
        with _chdir(self._tmp):
            return extract_feature_name_from_index(filename)

    def test_acronym_heavy_filename_pm(self):
        """PM in filename should resolve to PM/Dev Session Architecture, not Pm/Dev..."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"

    def test_acronym_heavy_filename_sdlc(self):
        """SDLC in filename should resolve correctly."""
        result = self._extract("sdlc-critique-stage.md")
        assert result == "SDLC Critique Stage"

    def test_acronym_heavy_filename_ai(self):
        """AI in filename should resolve correctly."""
        result = self._extract("ai-evaluator.md")
        assert result == "AI Evaluator"

    def test_display_text_differs_from_filename(self):
        """Display text can contain characters not in filename (e.g., slashes)."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"
        # Verify .title() would have mangled this
        mangled = "pm-dev-session-architecture".replace("-", " ").title()
        assert mangled == "Pm Dev Session Architecture"  # Wrong!
        assert result != mangled

    def test_hyphenated_compound_name(self):
        """Compound names with hyphens (do-build) should resolve correctly."""
        result = self._extract("do-build-ai-evaluator.md")
        assert result == "Do-Build AI Evaluator"

    def test_missing_readme_entry(self):
        """Filename with no matching README row returns None."""
        result = self._extract("nonexistent-feature.md")
        assert result is None

    def test_simple_filename(self):
        """Simple filename without acronyms works fine."""
        result = self._extract("bridge-self-healing.md")
        assert result == "Bridge Self-Healing"


class TestValidateFeatureIndex:
    """Test feature index validation."""

    def test_feature_found_case_insensitive(self, tmp_path):
        """validate_feature_index finds features case-insensitively."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("PM/Dev Session Architecture")
            assert valid is True
            assert error == ""

    def test_feature_not_found(self, tmp_path):
        """validate_feature_index returns error for missing feature."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("Nonexistent Feature XYZ")
            assert valid is False
            assert "Nonexistent Feature XYZ" in error

    def test_no_readme_file(self, tmp_path):
        """validate_feature_index handles missing README gracefully."""
        with _chdir(tmp_path):
            valid, error = validate_feature_index("Any Feature")
            assert valid is False
            assert "not found" in error


class TestValidateFeatureDoc:
    """Test feature doc validation."""

    def test_valid_doc(self, tmp_path):
        """Valid doc with title and content passes."""
        doc = tmp_path / "feature.md"
        doc.write_text("# My Feature\n\nThis is a substantial description of the feature.")
        valid, error = validate_feature_doc(doc)
        assert valid is True

    def test_missing_doc(self, tmp_path):
        """Missing doc fails gracefully."""
        doc = tmp_path / "nonexistent.md"
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "not found" in error

    def test_doc_without_title(self, tmp_path):
        """Doc without title heading fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("Just some text without a heading.")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "missing title" in error

    def test_doc_too_short(self, tmp_path):
        """Doc with only title and no content fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("# Title\n\nShort")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "too short" in error


class TestExtractFeatureDocPath:
    """Test feature doc path extraction from plan text."""

    def test_extracts_create_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Create `docs/features/my-feature.md` describing the feature
            - [ ] Update README index
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/my-feature.md"

    def test_extracts_update_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Update `docs/features/existing.md` with new section
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/existing.md"

    def test_no_documentation_section(self):
        plan = "## Other Section\nSome content"
        result = extract_feature_doc_path(plan)
        assert result is None


class TestEndToEndMigrationChain:
    """Integration test: full migration validation chain.

    Exercises the specific scenario that triggered the original bug:
    a feature named pm-dev-session-architecture with a README entry that
    says PM/Dev Session Architecture (not Pm Dev Session Architecture).
    """

    def test_full_chain_with_acronym_feature(self, tmp_path):
        """The migration chain works end-to-end with acronym-heavy filenames."""
        # Set up docs/features/ directory
        features_dir = tmp_path / "docs" / "features"
        features_dir.mkdir(parents=True)

        # Create the README index
        readme = features_dir / "README.md"
        readme.write_text(SAMPLE_README)

        # Create the feature doc
        feature_doc = features_dir / "pm-dev-session-architecture.md"
        feature_doc.write_text(
            "# PM/Teammate/Dev Session Architecture\n\n"
            "Session type discriminator splitting orchestration from execution.\n\n"
            "## Overview\nDetailed description of the architecture."
        )

        with _chdir(tmp_path):
            # Step 1: validate feature doc exists
            valid, error = validate_feature_doc(feature_doc)
            assert valid is True, f"Feature doc validation failed: {error}"

            # Step 2: extract name from index (the new way - Bug 1 fix)
            feature_name = extract_feature_name_from_index("pm-dev-session-architecture.md")
            assert feature_name is not None, "Failed to extract feature name"
            assert feature_name == "PM/Dev Session Architecture"

            # Step 3: validate the extracted name is in the index
            valid, error = validate_feature_index(feature_name)
            assert valid is True, f"Feature index validation failed: {error}"

            # Step 4: verify the OLD way (.title()) would have failed
            mangled_name = "pm-dev-session-architecture".replace("-", " ").title()
            assert mangled_name == "Pm Dev Session Architecture"
            # This would have failed because "Pm" != "PM"
            valid_old, _ = validate_feature_index(mangled_name)
            # The old approach fails: "Pm Dev Session Architecture"
            # won't match "PM/Dev Session Architecture" (missing "/")
            assert valid_old is False, "Old .title() approach should fail due to missing /"


class TestMigratePlanToCompleted:
    """Tests for the path-independent migrate_plan_to_completed() primitive.

    Uses a real temp git repo (matches the `docs/plans/{name}.md` layout the
    function derives its repo root from) rather than mocking git -- these
    guards (existence, clean-tree/HEAD==main, git mv) only mean something
    against a real repository.
    """

    def _init_repo(self, tmp_path: Path) -> Path:
        """Create a bare-bones git repo with docs/plans/ + the archive dir."""
        repo = tmp_path / "repo"
        (repo / "docs" / "plans").mkdir(parents=True)
        (repo / COMPLETED_PLANS_DIR).mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        return repo

    def _write_plan(self, repo: Path, name: str, tracking_issue: int = 1900) -> Path:
        plan = repo / "docs" / "plans" / name
        plan.write_text(
            f"---\ntracking: https://github.com/tomcounsell/ai/issues/{tracking_issue}\n"
            f"---\n# {name}\n"
        )
        return plan

    def _commit_all(self, repo: Path, message: str = "init") -> None:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)

    def test_closed_issue_plan_migrates(self, tmp_path):
        """A plan on a clean main branch is git-mv'd into the archive, not unlinked."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "example-plan.md")
        self._commit_all(repo)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "migrated"
        assert not plan.exists()
        completed = repo / COMPLETED_PLANS_DIR / "example-plan.md"
        assert completed.exists()
        assert "example-plan.md" in completed.read_text()
        # Verify it was a tracked git mv, not a bare unlink: git status is clean
        # (the move + commit is fully recorded), and the file shows up under
        # the archive in the git history for HEAD.
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == ""
        log = _git(repo, "log", "--oneline", "-1")
        assert "Migrate completed plan" in log.stdout

    def test_already_migrated_is_idempotent(self, tmp_path):
        """Source absent + dest present -> 'already-migrated', not an error.

        git mv is NOT idempotent -- a second attempt on an already-moved plan
        must not look like a failure.
        """
        repo = self._init_repo(tmp_path)
        completed = repo / COMPLETED_PLANS_DIR / "example-plan.md"
        completed.write_text("# already here\n")
        missing_plan = repo / "docs" / "plans" / "example-plan.md"

        verdict = migrate_plan_to_completed(missing_plan, apply=True)

        assert verdict == "already-migrated"
        assert completed.exists()
        assert completed.read_text() == "# already here\n"

    def test_dirty_tree_preserves_plan(self, tmp_path):
        """A dirty working tree blocks the git mv; the plan is never lost."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "dirty-plan.md")
        self._commit_all(repo)
        # Make the tree dirty.
        plan.write_text(plan.read_text() + "\nuncommitted change\n")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "dirty-tree-skip"
        assert plan.exists(), "plan must be preserved in place, never lost"
        completed = repo / COMPLETED_PLANS_DIR / "dirty-plan.md"
        assert not completed.exists()

    def test_non_main_branch_preserves_plan(self, tmp_path):
        """Migration only runs on main; a feature branch is also a report-only skip."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "branch-plan.md")
        self._commit_all(repo)
        _git(repo, "checkout", "-q", "-b", "session/some-feature")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "dirty-tree-skip"
        assert plan.exists()

    def test_apply_false_reports_without_mutating(self, tmp_path):
        """apply=False (report-only) evaluates eligibility but moves nothing on disk."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "dry-run-plan.md")
        self._commit_all(repo)

        verdict = migrate_plan_to_completed(plan, apply=False)

        assert verdict == "migrated"  # verdict describes what WOULD happen
        assert plan.exists(), "apply=False must not perform the git mv"
        completed = repo / COMPLETED_PLANS_DIR / "dry-run-plan.md"
        assert not completed.exists()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == "", "apply=False must leave the tree untouched"


class TestMigratePlanToCompletedFreshness:
    """Freshness precondition + rollback against a real 'origin' remote (#3530).

    Regression coverage for the drift mechanism that let one machine
    accumulate 12 permanently-unpushed "Migrate completed plan" commits on
    local main: the primitive used to commit before checking freshness, and
    left the commit stranded whenever the push loop couldn't land it. These
    tests use a real bare 'origin' repo and a second 'racer' clone to
    reproduce genuine git races rather than mocking git's behavior.
    """

    def _init_repo_with_origin(self, tmp_path: Path) -> tuple[Path, Path]:
        """Bare 'origin.git' + a clone at 'repo' wired to it as `origin`."""
        origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        repo = tmp_path / "repo"
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(repo)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "docs" / "plans").mkdir(parents=True)
        (repo / COMPLETED_PLANS_DIR).mkdir(parents=True)
        return origin, repo

    def _clone(self, origin: Path, dest: Path, *, name: str) -> Path:
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        _git(dest, "config", "user.email", f"{name}@example.com")
        _git(dest, "config", "user.name", name)
        return dest

    def _write_plan(self, repo: Path, name: str, tracking_issue: int = 1900) -> Path:
        plan = repo / "docs" / "plans" / name
        plan.write_text(
            f"---\ntracking: https://github.com/tomcounsell/ai/issues/{tracking_issue}\n"
            f"---\n# {name}\n"
        )
        return plan

    def test_stale_main_refuses_and_preserves_plan(self, tmp_path):
        """Local main already ahead of origin/main -> refuse, mutate nothing."""
        origin, repo = self._init_repo_with_origin(tmp_path)
        plan = self._write_plan(repo, "example-plan.md")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "push", "-q", "origin", "main")

        # An unrelated local-only commit -- exactly the shape a prior stranded
        # migration commit would leave behind.
        (repo / "local-only.txt").write_text("never pushed\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "local-only change")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "stale-main-skip"
        assert plan.exists(), "refusal must never touch the plan"
        completed = repo / COMPLETED_PLANS_DIR / "example-plan.md"
        assert not completed.exists()
        # The pre-existing local-only commit must survive untouched -- a
        # refusal never resets anything out from under the caller.
        log = _git(repo, "log", "--oneline", "-1")
        assert "local-only change" in log.stdout
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == ""

    def test_rebase_conflict_rolls_back_local_commit(self, tmp_path, monkeypatch):
        """A genuine rebase conflict rolls local main back to origin/main (#3530)."""
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo = self._init_repo_with_origin(tmp_path)
        plan = self._write_plan(repo, "conflict-plan.md")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "push", "-q", "origin", "main")

        racer = self._clone(origin, tmp_path / "racer", name="Racer")
        racer_completed_dir = racer / COMPLETED_PLANS_DIR
        racer_completed = racer_completed_dir / "conflict-plan.md"

        injected = {"done": False}

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            # Land a racer commit that plants an UNRELATED file directly at
            # the destination path (no `git mv`, source plan left untouched)
            # right after our local commit lands, mirroring the real race
            # window: another process pushes between our commit and our push.
            # git's rebase treats our side as a clean rename (A deleted, B
            # added, content unchanged) and would silently ride along with
            # ANY unilateral edit on the other side -- a bare content tweak at
            # either path never conflicts there. What it cannot auto-resolve
            # is our rename product colliding with an independent add at the
            # same destination path with different content: a genuine
            # rename/add conflict.
            if not injected["done"] and args[:1] == ["commit"] and result.returncode == 0:
                injected["done"] = True
                racer_completed_dir.mkdir(parents=True, exist_ok=True)
                racer_completed.write_text("racer already put something else here\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", "racer migrates the same plan concurrently")
                _git(racer, "push", "-q", "origin", "main")
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip"
        # Local main must land exactly on origin/main's tip -- never ahead.
        local_log = _git(repo, "log", "--oneline", "-1")
        origin_log = subprocess.run(
            ["git", "log", "--oneline", "-1", "main"],
            cwd=str(origin),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert local_log.stdout.strip() == origin_log.stdout.strip()
        assert "racer migrates the same plan concurrently" in local_log.stdout
        # Nothing lost: the racer never touched the source plan, and the
        # rollback lands local main exactly on origin/main -- so the plan is
        # still in root, untouched, ready for the next run to redo the
        # migration. Our own rolled-back copy never touched the archive.
        assert plan.exists()
        completed = repo / COMPLETED_PLANS_DIR / "conflict-plan.md"
        assert completed.exists()
        assert "racer already put something else here" in completed.read_text()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == ""

    def test_push_exhaustion_rolls_back_local_commit(self, tmp_path, monkeypatch):
        """Losing the push race 3 times straight rolls back, never stacks (#3530)."""
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo = self._init_repo_with_origin(tmp_path)
        plan = self._write_plan(repo, "exhaust-plan.md")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "push", "-q", "origin", "main")

        racer = self._clone(origin, tmp_path / "racer", name="Racer")
        racer_counter = {"n": 0}

        def wrapper(args, cwd, timeout=30):
            if args == ["push", "origin", "main"]:
                # Always win the race with an unrelated-file commit, so our
                # push keeps losing but the eventual rebase never conflicts.
                racer_counter["n"] += 1
                n = racer_counter["n"]
                (racer / f"racer-file-{n}.txt").write_text(f"racer commit {n}\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", f"racer commit {n}")
                _git(racer, "push", "-q", "origin", "main")
            return real_run_git(args, cwd, timeout)

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip"
        assert racer_counter["n"] == 3, "must have retried the full budget before giving up"
        local_log = _git(repo, "log", "--oneline", "-1")
        origin_log = subprocess.run(
            ["git", "log", "--oneline", "-1", "main"],
            cwd=str(origin),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert local_log.stdout.strip() == origin_log.stdout.strip()
        assert "racer commit 3" in local_log.stdout
        # Nothing lost: the plan is back in root, untouched by the racer's
        # unrelated commits, ready for the next run to redo the migration.
        assert plan.exists()
        completed = repo / COMPLETED_PLANS_DIR / "exhaust-plan.md"
        assert not completed.exists()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == ""


class TestMigrationRollbackSafety:
    """The rollback must excise our own migration commit and nothing else.

    These are the regression tests for the #3530 follow-up: the rollback used
    to be an unconditional `git reset --hard origin/main` on the *shared* main
    checkout, which silently destroyed a peer session's uncommitted tracked
    edits. Safety is now enforced by git itself (`reset --keep`, `rebase
    --onto`), so these tests drive real git races -- a real bare origin, a real
    racer clone, a real dirty tree -- rather than mocking git's semantics.
    """

    def _setup(self, tmp_path: Path, plan_name: str) -> tuple[Path, Path, Path]:
        """Bare origin + our clone (plan + a shared tracked file) + a racer clone."""
        origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        repo = tmp_path / "repo"
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(repo)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "docs" / "plans").mkdir(parents=True)
        (repo / COMPLETED_PLANS_DIR).mkdir(parents=True)
        plan = repo / "docs" / "plans" / plan_name
        plan.write_text(
            "---\ntracking: https://github.com/tomcounsell/ai/issues/3530\n---\n# plan\n"
        )
        (repo / "shared.txt").write_text("peer work in progress\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "push", "-q", "origin", "main")

        racer = tmp_path / "racer"
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(racer)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        _git(racer, "config", "user.email", "racer@example.com")
        _git(racer, "config", "user.name", "Racer")
        return origin, repo, plan

    def test_peer_uncommitted_edits_survive_the_rollback(self, tmp_path, monkeypatch):
        """A peer's uncommitted tracked edit must survive a rolled-back migration.

        The traced interleaving from the review: we commit, a peer dirties a
        tracked file in this shared checkout, every `git rebase origin/main`
        then fails on unstaged changes (no conflict, so the conflict branch is
        skipped), the retry budget burns, and the rollback fires. Under
        `reset --hard` the peer's edit was destroyed with no reflog and no
        warning; under `reset --keep` it survives.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "peer-dirty-plan.md")
        racer = tmp_path / "racer"
        injected = {"done": False}

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            if not injected["done"] and args[:1] == ["commit"] and result.returncode == 0:
                injected["done"] = True
                # The racer wins the push race on an unrelated file...
                (racer / "racer.txt").write_text("racer work\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", "racer commit")
                _git(racer, "push", "-q", "origin", "main")
                # ...and a peer session dirties a tracked file right here in
                # the shared checkout, without committing it.
                (repo / "shared.txt").write_text("PEER UNCOMMITTED EDIT\n")
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip"
        assert (repo / "shared.txt").read_text() == "PEER UNCOMMITTED EDIT\n", (
            "the rollback destroyed a peer session's uncommitted tracked work"
        )
        # Our migration commit is gone and local main is not ahead of origin.
        assert _git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"
        assert "Migrate completed plan" not in _git(repo, "log", "--oneline", "-5").stdout
        assert plan.exists(), "the plan returns to root for the next run to redo"

    def test_rollback_refused_when_git_declines_to_reset(self, tmp_path, monkeypatch):
        """If the reset would overwrite a peer's local modification, git refuses.

        `reset --keep` aborts when a locally-modified file differs between HEAD
        and the reset target. The primitive must report that honestly
        (`rollback-refused-skip`) rather than claiming a rollback it did not
        perform.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "refused-plan.md")
        racer = tmp_path / "racer"
        injected = {"done": False}

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            if not injected["done"] and args[:1] == ["commit"] and result.returncode == 0:
                injected["done"] = True
                # The racer changes the SAME tracked file the peer is editing
                # locally, so resetting onto origin/main would have to clobber
                # the peer's uncommitted version of it.
                (racer / "shared.txt").write_text("racer's committed version\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", "racer edits shared.txt")
                _git(racer, "push", "-q", "origin", "main")
                (repo / "shared.txt").write_text("PEER UNCOMMITTED EDIT\n")
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rollback-refused-skip"
        assert (repo / "shared.txt").read_text() == "PEER UNCOMMITTED EDIT\n"
        # Refusing means leaving main exactly as it was, for manual recovery.
        assert "Migrate completed plan" in _git(repo, "log", "--oneline", "-1").stdout

    def test_peer_commit_is_replayed_while_only_ours_is_dropped(self, tmp_path, monkeypatch):
        """A peer's local-only commit survives; only our commit is excised.

        Refusing outright would leave our commit stranded on local main, which
        is the very condition #3530 forbids. A surgical `rebase --onto` drops
        ours and replays theirs.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "peer-commit-plan.md")
        racer = tmp_path / "racer"
        injected = {"done": False}
        races = {"n": 0}

        def wrapper(args, cwd, timeout=30):
            if args == ["push", "origin", "main"]:
                # The racer wins every push race on unrelated files, so our
                # push keeps losing without ever producing a conflict.
                races["n"] += 1
                (racer / f"racer-{races['n']}.txt").write_text("racer work\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", f"racer commit {races['n']}")
                _git(racer, "push", "-q", "origin", "main")
            result = real_run_git(args, cwd, timeout)
            if not injected["done"] and args[:1] == ["commit"] and result.returncode == 0:
                injected["done"] = True
                # A peer session commits locally on the shared checkout, on
                # top of our migration commit, and has not pushed it.
                (repo / "peer.txt").write_text("peer's unpushed work\n")
                _git(repo, "add", "peer.txt")
                _git(repo, "commit", "-q", "-m", "peer local commit")
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip"
        log = _git(repo, "log", "--oneline", "-5").stdout
        assert "peer local commit" in log, "the peer's commit must survive the rollback"
        assert "Migrate completed plan" not in log, "our commit must be gone"
        assert (repo / "peer.txt").read_text() == "peer's unpushed work\n"
        assert plan.exists()

    def test_reset_failure_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        """A rollback `reset` that fails must not yield a false all-clear.

        A contended `.git/index.lock` in the shared checkout makes the reset
        fail; reporting `rolled-back-skip` over a still-stranded commit would
        be a false all-clear on exactly the bug this primitive exists to fix.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "locked-plan.md")
        racer = tmp_path / "racer"
        races = {"n": 0}
        lock = repo / ".git" / "index.lock"

        def wrapper(args, cwd, timeout=30):
            if args == ["push", "origin", "main"]:
                races["n"] += 1
                (racer / f"racer-{races['n']}.txt").write_text("racer work\n")
                _git(racer, "add", "-A")
                _git(racer, "commit", "-q", "-m", f"racer commit {races['n']}")
                _git(racer, "push", "-q", "origin", "main")
            if args[:2] == ["reset", "--keep"]:
                lock.write_text("")  # another git process holds the index
            return real_run_git(args, cwd, timeout)

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        try:
            verdict = migrate_plan_to_completed(plan, apply=True)
        finally:
            lock.unlink(missing_ok=True)

        assert verdict == "rollback-refused-skip"
        assert "Migrate completed plan" in _git(repo, "log", "--oneline", "-1").stdout, (
            "the commit is genuinely still stranded -- the verdict must say so"
        )

    def test_push_that_landed_is_not_escalated_to_a_human(self, tmp_path, monkeypatch):
        """A push that succeeded server-side but reported failure is `migrated`.

        Nothing is ahead of origin/main and the rename is present there, so
        there is nothing to roll back. Returning `rollback-refused-skip` would
        fabricate a human escalation for a migration that actually landed.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "landed-plan.md")

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            if args == ["push", "origin", "main"]:
                # The ref update lands, then the connection drops before the
                # client sees the acknowledgement.
                return subprocess.CompletedProcess(
                    args=["git", *args], returncode=1, stdout="", stderr="connection reset"
                )
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "migrated"
        assert _git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"
        assert f"{COMPLETED_PLANS_DIR}/landed-plan.md" in self._origin_files(origin)

    @staticmethod
    def _origin_files(origin: Path) -> str:
        return subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "main"],
            cwd=str(origin),
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout

    def test_commit_does_not_sweep_a_peers_staged_work(self, tmp_path, monkeypatch):
        """The migration commit carries the rename and nothing else.

        The shared checkout has one index. A peer's `git add` between the
        clean-tree precondition and our `git commit` must not be published to
        main under our migration subject.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "pathspec-plan.md")
        injected = {"done": False}

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            if not injected["done"] and args[:1] == ["mv"] and result.returncode == 0:
                injected["done"] = True
                (repo / "peer-staged.txt").write_text("peer's half-finished work\n")
                _git(repo, "add", "peer-staged.txt")
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "migrated"
        committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
        assert "peer-staged.txt" not in committed, "our commit swept a peer's staged file"
        assert "pathspec-plan.md" in committed
        # The peer's work is still staged, untouched, where they left it.
        assert (repo / "peer-staged.txt").exists()
        assert "peer-staged.txt" in _git(repo, "diff", "--cached", "--name-only").stdout

    def test_fetch_failure_mutates_nothing(self, tmp_path):
        """An unreachable origin returns fetch-failed-skip and touches nothing."""
        origin, repo, plan = self._setup(tmp_path, "unreachable-plan.md")
        _git(repo, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "fetch-failed-skip"
        assert plan.exists()
        assert not (repo / COMPLETED_PLANS_DIR / "unreachable-plan.md").exists()
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
        assert _git(repo, "status", "--porcelain").stdout.strip() == ""

    def test_run_git_reports_a_timeout_instead_of_raising(self, tmp_path):
        """_run_git turns subprocess.TimeoutExpired into a non-zero result.

        Letting it escape would bypass every rollback path and abort the
        reflection sweep, which has no handler for it.
        """
        from scripts.migrate_completed_plan import _run_git as real_run_git

        _origin, repo, _plan = self._setup(tmp_path, "timeout-probe-plan.md")

        result = real_run_git(["-c", "alias.stall=!sleep 5", "stall"], repo, timeout=1)

        assert result.returncode != 0
        assert "timed out" in result.stderr

    def test_landed_but_timed_out_commit_is_rolled_back_not_reverse_renamed(
        self, tmp_path, monkeypatch
    ):
        """A `git commit` that LANDS but blows its timeout must not be treated as a no-op.

        The hook chain can push a commit past the timeout, and a kill after
        git's ref update but before process exit reports non-zero for a commit
        that exists. Reverse-renaming there strands the migration commit on the
        shared main AND leaves its index dirty with a staged reverse-rename --
        strictly worse than #3530. Real git throughout: the commit really runs
        and really succeeds; only its reported returncode is replaced.
        """
        from scripts.migrate_completed_plan import GIT_TIMEOUT_RETURNCODE
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "timeout-commit-plan.md")
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

        def wrapper(args, cwd, timeout=30):
            result = real_run_git(args, cwd, timeout)
            if args and args[0] == "commit":
                # The commit really happened; git was killed before it could say so.
                return subprocess.CompletedProcess(
                    args=result.args,
                    returncode=GIT_TIMEOUT_RETURNCODE,
                    stdout=result.stdout,
                    stderr=result.stderr + "git commit timed out after 30s",
                )
            return result

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip", (
            "a landed-but-timed-out commit was reported as a no-op; "
            "the migration commit is stranded on the shared main"
        )
        assert _git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0", (
            "the migration commit was left stranded ahead of origin/main"
        )
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
        assert _git(repo, "status", "--porcelain").stdout.strip() == "", (
            "the shared checkout was left dirty (staged reverse-rename), which breaks "
            "the clean-tree precondition for every peer session"
        )
        assert plan.exists()
        assert not (repo / COMPLETED_PLANS_DIR / "timeout-commit-plan.md").exists()

    def test_genuinely_failed_commit_undoes_only_its_own_rename(self, tmp_path):
        """A commit git really refused leaves no stranded commit and no staged rename.

        Real refusal via a real failing pre-commit hook -- no mocking of git
        semantics. This is the other half of the landed/not-landed fork, and
        the only coverage of the `mutation-failed-skip` undo path.
        """
        origin, repo, plan = self._setup(tmp_path, "refused-commit-plan.md")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "mutation-failed-skip"
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
        assert _git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"
        assert _git(repo, "status", "--porcelain").stdout.strip() == "", (
            "the rename was not undone; the shared checkout is left dirty"
        )
        assert plan.exists()
        assert not (repo / COMPLETED_PLANS_DIR / "refused-commit-plan.md").exists()

    def test_hanging_push_still_rolls_back(self, tmp_path, monkeypatch):
        """A git push that blows its timeout must not strand the commit."""
        from scripts.migrate_completed_plan import _run_git as real_run_git

        origin, repo, plan = self._setup(tmp_path, "hanging-push-plan.md")

        def wrapper(args, cwd, timeout=30):
            if args == ["push", "origin", "main"]:
                # A real git invocation that really blows a real timeout.
                return real_run_git(["-c", "alias.stall=!sleep 5", "stall"], cwd, timeout=1)
            return real_run_git(args, cwd, timeout)

        monkeypatch.setattr("scripts.migrate_completed_plan._run_git", wrapper)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "rolled-back-skip"
        assert _git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"
        assert plan.exists()


# --- Helpers ---


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git subcommand rooted at `repo`, raising on unexpected failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


@contextlib.contextmanager
def _chdir(path):
    """Context manager to temporarily change directory."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class TestRunIssueEvidenceGate:
    """The --issue CLI (Site D primary path) is evidence-gated like the sweep
    and the reflection: only a literally "closed" tracking issue migrates.

    PR #1903 review blocker: a multi-PR issue (PR 1 merged, issue open for
    PR 2) must keep its plan in root; a gh outage ("unknown") must defer.
    """

    def _setup(self, monkeypatch, tmp_path, state):
        import scripts.migrate_completed_plan as mcp

        plan = tmp_path / "docs" / "plans" / "some-plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("---\ntracking: https://github.com/o/r/issues/42\n---\n# Plan\n")
        monkeypatch.setattr(mcp, "find_plan_by_issue", lambda n: plan)
        monkeypatch.setattr(mcp, "_gh_issue_state", lambda n: state)
        calls = []

        def fake_migrate(p, *, apply):
            calls.append((p, apply))
            return "migrated"

        monkeypatch.setattr(mcp, "migrate_plan_to_completed", fake_migrate)
        return mcp, calls

    def test_open_issue_skips_and_never_migrates(self, monkeypatch, tmp_path, capsys):
        mcp, calls = self._setup(monkeypatch, tmp_path, "open")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 1
        assert calls == []
        assert "skipped-open" in capsys.readouterr().out

    def test_unknown_state_defers_never_migrates(self, monkeypatch, tmp_path, capsys):
        """A gh outage reads as "unknown" -- deferral, never a migration."""
        mcp, calls = self._setup(monkeypatch, tmp_path, "unknown")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 1
        assert calls == []
        assert "skipped-open" in capsys.readouterr().out

    def test_closed_issue_migrates(self, monkeypatch, tmp_path):
        mcp, calls = self._setup(monkeypatch, tmp_path, "closed")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][1] is True

    def test_no_plan_found_exits_2(self, monkeypatch, tmp_path):
        import scripts.migrate_completed_plan as mcp

        monkeypatch.setattr(mcp, "find_plan_by_issue", lambda n: None)
        gate_calls = []
        monkeypatch.setattr(mcp, "_gh_issue_state", lambda n: gate_calls.append(n) or "closed")
        assert mcp.run_issue("999", apply=True) == 2


class TestGhIssueStateRepoScoping:
    """Issue #2889: `_gh_issue_state` must scope `gh issue view` with --repo.

    A bare ``gh issue view N`` resolves GH_REPO from the environment before
    cwd, so under a foreign GH_REPO it answers about a *different*
    repository's issue #N and exits 0. The argv must carry
    ``--repo <resolved-slug>`` when a repo resolves (mirroring the
    tools/sdlc_stage_query.py ladder: GH_REPO env first, else
    ``gh repo view --json nameWithOwner`` from the working-tree root); when
    nothing resolves, the argv degrades to the prior unscoped shape and the
    ``"unknown"``-on-failure contract is preserved.
    """

    def test_argv_scoped_from_gh_repo_env(self, monkeypatch):
        import scripts.migrate_completed_plan as mcp

        captured: dict = {}

        class FakeResult:
            returncode = 0
            stdout = '{"state": "closed"}'

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return FakeResult()

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(mcp.subprocess, "run", fake_run)
        assert mcp._gh_issue_state("42") == "closed"
        assert captured["argv"] == [
            "gh",
            "issue",
            "view",
            "42",
            "--repo",
            "tomcounsell/ai",
            "--json",
            "state",
        ]

    def test_argv_scoped_from_derived_repo(self, monkeypatch):
        """GH_REPO unset: slug derived via gh repo view from the git root."""
        import scripts.migrate_completed_plan as mcp

        captured: list = []

        class IssueResult:
            returncode = 0
            stdout = '{"state": "open"}'

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if argv[:2] == ["git", "rev-parse"]:
                return type("GitResult", (), {"returncode": 0, "stdout": "/repo/root"})()
            if argv[:3] == ["gh", "repo", "view"]:
                return type("RepoResult", (), {"returncode": 0, "stdout": "tomcounsell/ai"})()
            return IssueResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setattr(mcp.subprocess, "run", fake_run)
        assert mcp._gh_issue_state("42") == "open"
        assert captured[-1] == [
            "gh",
            "issue",
            "view",
            "42",
            "--repo",
            "tomcounsell/ai",
            "--json",
            "state",
        ]

    def test_argv_unscoped_when_repo_resolution_fails(self, monkeypatch):
        """No GH_REPO and gh repo view fails: degrade to the unscoped argv."""
        import scripts.migrate_completed_plan as mcp

        captured: list = []

        class IssueResult:
            returncode = 0
            stdout = '{"state": "closed"}'

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if argv[:2] == ["git", "rev-parse"] or argv[:3] == ["gh", "repo", "view"]:
                return type("FailResult", (), {"returncode": 1, "stdout": ""})()
            return IssueResult()

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setattr(mcp.subprocess, "run", fake_run)
        assert mcp._gh_issue_state("42") == "closed"
        assert captured[-1] == ["gh", "issue", "view", "42", "--json", "state"]

    def test_gh_failure_returns_unknown(self, monkeypatch):
        """The fail-soft contract: any gh failure reads as "unknown"."""
        import scripts.migrate_completed_plan as mcp

        def fake_run(argv, **kwargs):
            raise OSError("gh missing")

        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")
        monkeypatch.setattr(mcp.subprocess, "run", fake_run)
        assert mcp._gh_issue_state("42") == "unknown"
