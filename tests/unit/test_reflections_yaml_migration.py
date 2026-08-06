"""Unit tests for the reflections.yaml migration script (issue #1273 Q3).

Covers:

- ``interval: N`` rewrite to ``every: Ns`` (atomic).
- Idempotence — running on already-migrated YAML is a no-op.
- Pre-flight rejection — malformed entries abort before writing.
- Atomic temp-file + rename — partial files never appear on disk.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> Path:
    return tmp_path / "reflections.yaml"


def _write(p: Path, content: str) -> None:
    p.write_text(textwrap.dedent(content).lstrip("\n"))


def _read(p: Path) -> str:
    return p.read_text()


class TestInterval:
    def test_rewrites_interval_to_every(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
              - name: b
                interval: 3600
                priority: high
                execution_type: function
                callable: x.z
            """,
        )

        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is True

        text = _read(tmp_yaml)
        # Both interval lines are gone; replaced by every:.
        assert "interval:" not in text
        assert "every: 60s" in text
        assert "every: 3600s" in text

    def test_idempotent_on_already_migrated(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                every: 60s
                priority: low
                execution_type: function
                callable: x.y
            """,
        )

        before = _read(tmp_yaml)
        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is False  # nothing to do
        after = _read(tmp_yaml)
        assert before == after  # exact byte-equality

    def test_passes_through_cron_at(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                cron: "0 9 * * *"
                priority: low
                execution_type: function
                callable: x.y
              - name: b
                at: "2099-01-01T00:00:00+00:00"
                priority: high
                execution_type: function
                callable: x.z
            """,
        )

        result = migrate_yaml(tmp_yaml, dry_run=False)
        assert result.rewrote is False
        text = _read(tmp_yaml)
        assert "cron:" in text
        assert "at:" in text


class TestPreflight:
    def test_aborts_when_entry_has_no_schedule_after_rewrite(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import MigrationError, migrate_yaml

        # Malformed: no interval, no every/cron/at — entry can't be migrated.
        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        with pytest.raises(MigrationError, match="no schedule|missing"):
            migrate_yaml(tmp_yaml, dry_run=False)

        # File remains untouched (no temp-file leak).
        assert "schedule" not in _read(tmp_yaml)

    def test_aborts_when_interval_is_zero(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import MigrationError, migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 0
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        with pytest.raises(MigrationError):
            migrate_yaml(tmp_yaml, dry_run=False)


class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_yaml: Path):
        from scripts.migrate_reflections_yaml import migrate_yaml

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
            """,
        )
        before = _read(tmp_yaml)
        result = migrate_yaml(tmp_yaml, dry_run=True)
        # dry_run reports the rewrite would have occurred but does not write.
        assert result.rewrote is True
        assert _read(tmp_yaml) == before


def _sentry_triage_cutover_pending() -> bool:
    """True until the local machine's config/reflections.yaml has been cut
    over (pointer-comment present, sentry-issue-triage entry absent).

    Defensive by design: any missing file, parse failure, or unexpected shape
    is treated as "not yet cut over" (skip), never as a collection-time error.
    """
    registry_path = Path(__file__).resolve().parent.parent.parent / "config" / "reflections.yaml"
    if not registry_path.exists():
        return True
    try:
        data = yaml.safe_load(registry_path.read_text())
        names = [r["name"] for r in data["reflections"]]
    except Exception:
        return True
    return "sentry-issue-triage" in names


class TestSentryTriageCutover:
    """Guard against reintroducing the local sentry-issue-triage reflection
    entry now that it has migrated to a Claude Code Routine (cloud) — see
    docs/features/cowork-tasks.md. config/reflections.yaml carries only a
    pointer comment where the block used to live; a re-add here (e.g. by a
    parallel/concurrent agent run or a stale merge) would silently double-run
    the triage.

    The regression assertions only run once the local machine has actually
    completed the cutover; until then (file absent, or entry still live) the
    test skips cleanly rather than failing on expected pre-cutover state."""

    @pytest.mark.skipif(
        _sentry_triage_cutover_pending(),
        reason=(
            "config/reflections.yaml is machine-local (gitignored) and either "
            "absent or not yet cut over — the vault-copy cutover is an ORDERED "
            "post-merge operator action"
        ),
    )
    def test_sentry_issue_triage_absent_from_repo_registry(self):
        # config/reflections.yaml is gitignored and materialized per-machine from
        # ~/Desktop/Valor/reflections.yaml (see scripts/update/env_sync.py and
        # tests/unit/test_reflections_local_copy.py for the established pattern of
        # not asserting against real machine-local paths). This test opportunistically
        # verifies the actual cutover on machines where the file happens to exist
        # and has already been cut over, and skips cleanly everywhere else
        # (fresh clones, CI, other worktrees, pre-cutover machines) rather than
        # failing on unrelated or expected pre-cutover state.
        repo_root = Path(__file__).resolve().parent.parent.parent
        registry_path = repo_root / "config" / "reflections.yaml"

        # The contract under guard is *absence of the reflection entry from the
        # registry*, asserted against parsed YAML — the only signal that survives
        # tools/reflection_machine_filter.py's safe_load/safe_dump round-trip.
        # (A comment-substring assertion was deliberately removed here: comments
        # are destroyed by that round-trip. See TestFilterRoundTripSignalDurability.)
        data = yaml.safe_load(registry_path.read_text())
        names = [r["name"] for r in data["reflections"]]
        assert "sentry-issue-triage" not in names, (
            "sentry-issue-triage was reintroduced into the local reflections "
            f"registry at {registry_path}; it has migrated to a Claude Code "
            "Routine (cloud) and a local entry would double-run the triage. "
            f"Registry entries: {sorted(names)}"
        )


def _pr_review_audit_cutover_pending() -> bool:
    """True until the local machine's config/reflections.yaml has been cut
    over (pointer-comment present, pr-review-audit entry absent).

    Defensive by design: any missing file, parse failure, or unexpected shape
    is treated as "not yet cut over" (skip), never as a collection-time error.
    """
    registry_path = Path(__file__).resolve().parent.parent.parent / "config" / "reflections.yaml"
    if not registry_path.exists():
        return True
    try:
        data = yaml.safe_load(registry_path.read_text())
        names = [r["name"] for r in data["reflections"]]
    except Exception:
        return True
    return "pr-review-audit" in names


class TestPrReviewAuditCutover:
    """Guard against reintroducing the local pr-review-audit reflection entry
    as part of the ordered cutover to a Claude Code Routine (cloud) recipe --
    see docs/plans/cowork-remaining-reflections.md. config/reflections.yaml
    carries only a pointer comment where the block used to live; a re-add here
    (e.g. by a parallel/concurrent agent run or a stale merge) would silently
    double-run the audit once cloud coverage is live.

    NOTE: unlike the sentry-issue-triage precedent this mirrors, the cloud
    recipe for pr-review-audit has NOT been verified to file a real issue via
    a live CMA run as of this test's authorship -- the local registry entry
    was removed ahead of that verification per the plan's explicit build
    instructions, and the pointer comment says so. This test only guards
    against reintroduction of the local entry; it makes no claim about cloud
    coverage being live.

    The regression assertions only run once the local machine's registry has
    actually been cut over; until then (file absent, or entry still live) the
    test skips cleanly rather than failing on expected pre-cutover state."""

    @pytest.mark.skipif(
        _pr_review_audit_cutover_pending(),
        reason=(
            "config/reflections.yaml is machine-local (gitignored) and either "
            "absent or not yet cut over -- the vault-copy cutover is an ORDERED "
            "post-merge operator action"
        ),
    )
    def test_pr_review_audit_absent_from_repo_registry(self):
        # config/reflections.yaml is gitignored and materialized per-machine from
        # ~/Desktop/Valor/reflections.yaml (see scripts/update/env_sync.py and
        # tests/unit/test_reflections_local_copy.py for the established pattern of
        # not asserting against real machine-local paths). This test opportunistically
        # verifies the actual cutover on machines where the file happens to exist
        # and has already been cut over, and skips cleanly everywhere else
        # (fresh clones, CI, other worktrees, pre-cutover machines) rather than
        # failing on unrelated or expected pre-cutover state.
        repo_root = Path(__file__).resolve().parent.parent.parent
        registry_path = repo_root / "config" / "reflections.yaml"

        # The contract under guard is *absence of the reflection entry from the
        # registry*, asserted against parsed YAML — the only signal that survives
        # tools/reflection_machine_filter.py's safe_load/safe_dump round-trip.
        # (A comment-substring assertion was deliberately removed here: comments
        # are destroyed by that round-trip. See TestFilterRoundTripSignalDurability.)
        data = yaml.safe_load(registry_path.read_text())
        names = [r["name"] for r in data["reflections"]]
        assert "pr-review-audit" not in names, (
            "pr-review-audit was reintroduced into the local reflections registry "
            f"at {registry_path}; it has been cut over to a Claude Code Routine "
            "(cloud) and a local entry would double-run the audit. "
            f"Registry entries: {sorted(names)}"
        )


class TestFilterRoundTripSignalDurability:
    """Why the cutover guards above assert on parsed YAML and never on comments.

    ``tools/reflection_machine_filter.py`` rewrites ``config/reflections.yaml``
    in place with ``yaml.safe_load`` → ``yaml.safe_dump`` (``:110`` / ``:146``).
    That round-trip discards comments by construction, so a comment-substring
    assertion is a latent red: it passes only until the next ``install_worker.sh``
    run and then fails for a reason unrelated to any live contract.

    This test demonstrates the destruction rather than citing it, and proves the
    replacement assertion style survives the same round-trip. It operates
    entirely on ``tmp_path`` and never reads or writes the real machine-local
    ``config/reflections.yaml``.
    """

    def test_filter_destroys_comments_but_preserves_structure(self, tmp_path: Path):
        registry = tmp_path / "reflections.yaml"
        projects = tmp_path / "projects.json"

        pointer_comment = "# sentry-issue-triage migrated to a Claude Code Routine (cloud)"

        # The synthetic registry carries three things:
        #   (a) a pointer comment, the substrate the deleted assertions relied on;
        #   (b) the structural state the cutover tests assert on — a live entry
        #       list from which "sentry-issue-triage" is absent;
        #   (c) a reflection whose project_key maps to a machine OTHER than the
        #       one we pass in. (c) is mandatory: filter_reflections_for_machine
        #       only writes the file back when disabled_names is non-empty
        #       (tools/reflection_machine_filter.py:145-146), so without it the
        #       round-trip silently no-ops and this control would prove nothing.
        _write(
            registry,
            f"""
            {pointer_comment}
            reflections:
              - name: cutover-marker
                every: 60s
                priority: low
                execution_type: function
                callable: x.y
                enabled: true
              - name: other-machine-audit
                every: 3600s
                priority: high
                execution_type: function
                callable: x.z
                project_key: otherproj
                enabled: true
            """,
        )
        projects.write_text('{"projects": {"otherproj": {"machine": "Some Other Machine"}}}')

        assert pointer_comment in _read(registry)  # substrate present before the filter

        from tools.reflection_machine_filter import filter_reflections_for_machine

        count, disabled_names = filter_reflections_for_machine(
            registry, projects, machine_name="This Machine"
        )

        # Success Criterion 7a — the round-trip actually wrote. Guards against a
        # silent no-op control, which would make the two assertions below vacuous.
        assert disabled_names, (
            "filter_reflections_for_machine disabled nothing, so it never wrote the "
            "file and the round-trip below is vacuous; the synthetic registry must "
            "contain a reflection whose project_key maps to another machine"
        )
        assert count == len(disabled_names) == 1
        assert disabled_names == ["other-machine-audit"]

        round_tripped = _read(registry)

        # Success Criterion 6 — negative control for the DELETED assertion style.
        # A comment-substring assertion placed here would fail.
        assert pointer_comment not in round_tripped, (
            "the safe_load/safe_dump round-trip was expected to destroy the pointer "
            "comment; if it now survives, the comment-based assertions removed from "
            "the cutover tests could be restored"
        )
        assert "#" not in round_tripped  # no comment of any kind survives

        # Success Criterion 7 — positive control for the REPLACEMENT style, against
        # the SAME round-tripped artifact: parsed structure survives intact.
        data = yaml.safe_load(round_tripped)
        names = [r["name"] for r in data["reflections"]]
        assert names == ["cutover-marker", "other-machine-audit"]
        assert "sentry-issue-triage" not in names
        by_name = {r["name"]: r for r in data["reflections"]}
        assert by_name["cutover-marker"]["enabled"] is True
        assert by_name["other-machine-audit"]["enabled"] is False


class TestNoTempFileLeak:
    def test_no_partial_temp_file_on_failure(self, tmp_yaml: Path, monkeypatch):
        """If the rename step fails, the original file must remain unchanged
        and no ``.migrate.tmp`` sibling can be left behind."""
        from scripts import migrate_reflections_yaml as mod

        _write(
            tmp_yaml,
            """
            reflections:
              - name: a
                interval: 60
                priority: low
                execution_type: function
                callable: x.y
            """,
        )

        original = _read(tmp_yaml)

        def _boom(src, dst):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod.os, "replace", _boom)
        with pytest.raises(OSError):
            mod.migrate_yaml(tmp_yaml, dry_run=False)

        # Original is intact.
        assert _read(tmp_yaml) == original
        # No leftover temp file.
        siblings = list(tmp_yaml.parent.glob("*.migrate.tmp"))
        assert siblings == []
        # Cleanup any stray (defensive)
        for s in siblings:
            os.unlink(s)
