"""Unit tests for the params-driven apply activation added to
scripts/memory_consolidation.py::run_consolidation (issue #2203).

Covers:
- params={"apply": True} flips dry_run to False (apply mode engaged) when
  MEMORY_DEDUP_APPLY is unset.
- MEMORY_DEDUP_APPLY, when explicitly set, overrides params in either
  direction (env-as-kill-switch precedence) -- same rule as decay-prune.
- dedup_merge_count increments via the real Redis gate-counter, keyed
  per-record by the record's own project_key, coalescing null/empty to
  DEFAULT_PROJECT_KEY.

Follows the MagicMock-record fixture pattern already established in
tests/unit/test_memory_consolidation.py (_load_active_memories and
_call_haiku are patched; models.memory.Memory.safe_save is patched to avoid
a real merged-record write). Real Redis (POPOTO_REDIS_DB) is used for the
counter assertions -- no mocks on that path, per this repo's testing
philosophy.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config.memory_defaults import DEFAULT_PROJECT_KEY


def _make_record(
    memory_id: str,
    content: str,
    importance: float = 2.0,
    category: str = "correction",
    project_key: str | None = "test-dedup-apply-params",
) -> MagicMock:
    """Create a MagicMock resembling a Memory record."""
    record = MagicMock()
    record.memory_id = memory_id
    record.content = content
    record.importance = importance
    record.superseded_by = ""
    record.superseded_by_rationale = ""
    record.project_key = project_key
    record.metadata = {"category": category, "tags": []}
    record.save.return_value = None  # success
    return record


def _merge_action(ids: list[str]) -> dict:
    return {
        "action": "merge",
        "ids": ids,
        "merged_content": "Merged content",
        "merged_importance": 2.0,
        "merged_category": "correction",
        "merged_tags": [],
        "rationale": "near-duplicate",
    }


def _get_counter(project_key: str, reason: str) -> int:
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    val = _R.get(f"{project_key}:memory-gate:{reason}")
    return int(val) if val else 0


def _clear_env(monkeypatch):
    monkeypatch.delenv("MEMORY_DEDUP_APPLY", raising=False)


# --- _resolve_dry_run unit coverage ----------------------------------------


class TestResolveDryRun:
    def test_no_env_no_params_key_uses_dry_run_arg(self, monkeypatch):
        _clear_env(monkeypatch)
        from scripts.memory_consolidation import _resolve_dry_run

        assert _resolve_dry_run(True, {}) is True
        assert _resolve_dry_run(False, {}) is False

    def test_params_apply_true_forces_apply_when_env_unset(self, monkeypatch):
        _clear_env(monkeypatch)
        from scripts.memory_consolidation import _resolve_dry_run

        assert _resolve_dry_run(True, {"apply": True}) is False

    def test_params_apply_false_forces_dry_run_when_env_unset(self, monkeypatch):
        _clear_env(monkeypatch)
        from scripts.memory_consolidation import _resolve_dry_run

        assert _resolve_dry_run(False, {"apply": False}) is True

    def test_env_true_overrides_params_apply_false(self, monkeypatch):
        monkeypatch.setenv("MEMORY_DEDUP_APPLY", "true")
        from scripts.memory_consolidation import _resolve_dry_run

        assert _resolve_dry_run(True, {"apply": False}) is False

    def test_env_false_overrides_params_apply_true(self, monkeypatch):
        monkeypatch.setenv("MEMORY_DEDUP_APPLY", "false")
        from scripts.memory_consolidation import _resolve_dry_run

        assert _resolve_dry_run(False, {"apply": True}) is True


# --- end-to-end: params engages apply mode ---------------------------------


class TestParamsEngageApply:
    def test_params_apply_true_engages_apply_mode_end_to_end(self, monkeypatch):
        """run_consolidation(dry_run=True default, params={"apply": True}) actually
        calls Memory.safe_save (apply engaged), with MEMORY_DEDUP_APPLY unset."""
        _clear_env(monkeypatch)
        records = [
            _make_record("rec-a", "Content A"),
            _make_record("rec-b", "Content B"),
        ]
        haiku_response = {"actions": [_merge_action(["rec-a", "rec-b"])]}
        merged_record = _make_record("merged-id", "Merged content")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch(
                "models.memory.Memory.safe_save",
                return_value=merged_record,
            ) as mock_save,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", params={"apply": True})

        mock_save.assert_called_once()
        assert result["applied_merges"] == 1
        assert result["proposed_merges"] == 1

    def test_no_params_defaults_to_dry_run_no_safe_save(self, monkeypatch):
        _clear_env(monkeypatch)
        records = [
            _make_record("rec-c", "Content C"),
            _make_record("rec-d", "Content D"),
        ]
        haiku_response = {"actions": [_merge_action(["rec-c", "rec-d"])]}

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("models.memory.Memory.safe_save") as mock_save,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test")

        mock_save.assert_not_called()
        assert result["applied_merges"] == 0
        assert result["proposed_merges"] == 1

    def test_env_kill_switch_forces_dry_run_over_params_apply(self, monkeypatch):
        """MEMORY_DEDUP_APPLY=false wins over params={"apply": True} (emergency brake)."""
        monkeypatch.setenv("MEMORY_DEDUP_APPLY", "false")
        records = [
            _make_record("rec-e", "Content E"),
            _make_record("rec-f", "Content F"),
        ]
        haiku_response = {"actions": [_merge_action(["rec-e", "rec-f"])]}

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("models.memory.Memory.safe_save") as mock_save,
        ):
            from scripts.memory_consolidation import run_consolidation

            result = run_consolidation(project_key="test", params={"apply": True})

        mock_save.assert_not_called()
        assert result["applied_merges"] == 0


# --- dedup_merge_count counter, per-record project_key coalescing ---------


class TestDedupMergeCountCounter:
    def test_counter_increments_for_named_project_key(self, monkeypatch):
        _clear_env(monkeypatch)
        pk = "test-dedup-counter-named"
        before = _get_counter(pk, "dedup_merge_count")

        records = [
            _make_record("rec-g", "Content G", project_key=pk),
            _make_record("rec-h", "Content H", project_key=pk),
        ]
        haiku_response = {"actions": [_merge_action(["rec-g", "rec-h"])]}
        merged_record = _make_record("merged-gh", "Merged GH")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("models.memory.Memory.safe_save", return_value=merged_record),
        ):
            from scripts.memory_consolidation import run_consolidation

            run_consolidation(project_key=pk, params={"apply": True})

        after = _get_counter(pk, "dedup_merge_count")
        # Both originals (rec-g, rec-h) are marked superseded -> +2.
        assert after == before + 2

    def test_counter_coalesces_null_project_key_to_default(self, monkeypatch):
        """A record with project_key=None must increment the DEFAULT_PROJECT_KEY
        counter, not silently vanish under a null-keyed Redis key."""
        _clear_env(monkeypatch)
        before = _get_counter(DEFAULT_PROJECT_KEY, "dedup_merge_count")

        records = [
            _make_record("rec-i", "Content I", project_key=None),
            _make_record("rec-j", "Content J", project_key=None),
        ]
        haiku_response = {"actions": [_merge_action(["rec-i", "rec-j"])]}
        merged_record = _make_record("merged-ij", "Merged IJ")

        with (
            patch(
                "scripts.memory_consolidation._load_active_memories",
                return_value=records,
            ),
            patch(
                "scripts.memory_consolidation._call_haiku",
                return_value=haiku_response,
            ),
            patch("models.memory.Memory.safe_save", return_value=merged_record),
        ):
            from scripts.memory_consolidation import run_consolidation

            run_consolidation(project_key="test", params={"apply": True})

        after = _get_counter(DEFAULT_PROJECT_KEY, "dedup_merge_count")
        assert after == before + 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
