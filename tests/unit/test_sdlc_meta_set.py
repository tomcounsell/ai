"""Unit tests for tools.sdlc_meta_set CLI tool (issue #1302; re-pointed at the
issue-keyed PipelineLedger by issue #2012 task 2).

Tests cover:
- Valid key sets stage_states_json["_<key>"] via update_stage_states()
- Unknown key exits with code 2
- Missing/foreign/repo-less lease hard-fails loudly (exit 1) -- there is no
  session left to resolve
- Bool coercion for plan_revising
- String coercion for plan_hash_at_build_start
- Whitelist enforcement
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _lock_result(**kw):
    from models.session_lifecycle import IssueLockResult

    base = dict(acquired=True, owner_session_id="s", owner_run_id="run-test", target_repo="o/r")
    base.update(kw)
    return IssueLockResult(**base)


class TestMetaSetWriteMeta:
    """Tests for the write_meta() function."""

    def test_valid_bool_key_sets_value(self):
        """write_meta with plan_revising=True writes _plan_revising=True."""
        from tools.sdlc_meta_set import write_meta

        def fake_update_stage_states(ledger, update_fn, **kwargs):
            # Simulate applying the update to an empty dict
            result = update_fn({})
            assert result.get("_plan_revising") is True
            return True

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(
                key="plan_revising", value="true", issue_number=1, run_id="run-test"
            )

        assert result == {"key": "plan_revising", "value": True}

    def test_write_revalidates_lease_before_write(self):
        """TOCTOU close (Risk 5): the write must call touch_issue_lock a
        SECOND time (non-peek) with the resolved target_repo, immediately
        before the actual mutation."""
        from tools.sdlc_meta_set import write_meta

        mock_touch = MagicMock(return_value=_lock_result(owner_run_id="run-1954"))

        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            write_meta(key="plan_revising", value="true", issue_number=1954, run_id="run-1954")

        assert mock_touch.call_count == 2
        peek_calls = [c for c in mock_touch.call_args_list if c.kwargs.get("peek")]
        revalidate_calls = [c for c in mock_touch.call_args_list if not c.kwargs.get("peek")]
        assert len(peek_calls) == 1
        assert len(revalidate_calls) == 1
        args, kwargs = revalidate_calls[0]
        assert args[0] == 1954
        assert args[1] == "run-1954"
        assert kwargs.get("target_repo") == "o/r"

    def test_write_meta_foreign_run_returns_issue_locked(self):
        """#2003/#2012: a foreign run holding the issue lock refuses the meta
        write with the ISSUE_LOCKED shape."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_meta_set import write_meta

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )
        write_mock = MagicMock(return_value=True)

        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("tools.stage_states_helpers.update_stage_states", write_mock),
        ):
            result = write_meta(
                key="plan_revising", value="true", issue_number=1954, run_id="intruder-run"
            )

        assert result["reason"] == "ISSUE_LOCKED"
        assert result["owner_run_id"] == "foreign-run"
        assert result["owner_session_id"] == "other-session"
        write_mock.assert_not_called()

    def test_unheld_lease_returns_lease_absent(self):
        """PRESENT_NO_SESSION's replacement: an unheld lock (no established
        lease for this run_id at all) is now LOUD (a returned error shape),
        not a quiet no-op."""
        from tools.sdlc_meta_set import write_meta

        mock_touch = MagicMock(return_value=_lock_result(owner_run_id=None, target_repo=None))
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            result = write_meta(
                key="plan_revising", value="true", issue_number=1959, run_id="run-1959"
            )

        assert result["reason"] == "LEASE_ABSENT"

    def test_missing_issue_number_or_run_id_returns_lease_absent(self):
        from tools.sdlc_meta_set import write_meta

        result = write_meta(key="plan_revising", value="true")
        assert result["reason"] == "LEASE_ABSENT"

    def test_target_repo_missing_returns_error_never_writes(self):
        """Risk 5 (writer side): a valid lease with no pinned target_repo
        must hard-fail and never construct a PipelineLedger key with a None
        component."""
        from tools.sdlc_meta_set import write_meta

        mock_touch = MagicMock(return_value=_lock_result(owner_run_id="run-1", target_repo=None))
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            result = write_meta(
                key="plan_revising", value="true", issue_number=1960, run_id="run-1"
            )

        assert result["reason"] == "TARGET_REPO_MISSING"
        mock_get_or_create.assert_not_called()

    def test_valid_bool_key_false_clears_value(self):
        """write_meta with plan_revising=false writes _plan_revising=False."""
        from tools.sdlc_meta_set import write_meta

        def fake_update_stage_states(ledger, update_fn, **kwargs):
            result = update_fn({"_plan_revising": True})
            assert result.get("_plan_revising") is False
            return True

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(
                key="plan_revising", value="false", issue_number=1, run_id="run-test"
            )

        assert result == {"key": "plan_revising", "value": False}

    def test_valid_str_key_sets_value(self):
        """write_meta with plan_hash_at_build_start sets the string value."""
        from tools.sdlc_meta_set import write_meta

        test_hash = "abc123def456"

        def fake_update_stage_states(ledger, update_fn, **kwargs):
            result = update_fn({})
            assert result.get("_plan_hash_at_build_start") == test_hash
            return True

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(
                key="plan_hash_at_build_start", value=test_hash, issue_number=1, run_id="run-test"
            )

        assert result == {"key": "plan_hash_at_build_start", "value": test_hash}

    def test_unknown_key_returns_empty_dict(self):
        """write_meta with an unknown key returns {} (fail-soft)."""
        from tools.sdlc_meta_set import write_meta

        result = write_meta(key="nonexistent_key", value="anything")
        assert result == {}

    def test_update_stage_states_failure_returns_empty_dict(self):
        """write_meta returns {} when update_stage_states returns False."""
        from tools.sdlc_meta_set import write_meta

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch("tools.stage_states_helpers.update_stage_states", return_value=False),
        ):
            result = write_meta(
                key="plan_revising", value="true", issue_number=1, run_id="run-test"
            )

        assert result == {}

    def test_bool_coercion_numeric_strings(self):
        """write_meta coerces '1'/'0' to True/False for bool keys."""
        from tools.sdlc_meta_set import write_meta

        def fake_update_true(ledger, update_fn, **kwargs):
            result = update_fn({})
            assert result["_plan_revising"] is True
            return True

        def fake_update_false(ledger, update_fn, **kwargs):
            result = update_fn({})
            assert result["_plan_revising"] is False
            return True

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=fake_update_true),
        ):
            r1 = write_meta(key="plan_revising", value="1", issue_number=1, run_id="run-test")
        assert r1["value"] is True

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=fake_update_false),
        ):
            r2 = write_meta(key="plan_revising", value="0", issue_number=1, run_id="run-test")
        assert r2["value"] is False

    def test_invalid_bool_value_returns_empty_dict(self):
        """write_meta with an unrecognized bool value returns {} fail-soft."""
        from tools.sdlc_meta_set import write_meta

        result = write_meta(
            key="plan_revising", value="not_a_bool", issue_number=1, run_id="run-test"
        )
        assert result == {}

    def test_pr_number_writes_ledger_field_not_meta_key(self):
        """#2003 T1.7 / #2012: `--key pr_number` writes the PipelineLedger.pr_number
        FIELD. Single-writer contract: no `_pr_number` meta key is ever
        written to stage_states_json — update_stage_states must not be
        called at all.
        """
        from tools.sdlc_meta_set import write_meta

        mock_ledger = MagicMock()
        mock_ledger.pr_number = None

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=mock_ledger),
            patch("tools.stage_states_helpers.update_stage_states") as update_mock,
        ):
            result = write_meta(key="pr_number", value="42", issue_number=1, run_id="run-test")

        assert result == {"key": "pr_number", "value": 42}
        assert mock_ledger.pr_number == 42
        assert isinstance(mock_ledger.pr_number, int)
        mock_ledger.save.assert_called_once()
        update_mock.assert_not_called()

    def test_pr_number_save_failure_returns_empty_dict(self):
        """Field-write path fails soft: ledger.save() raising returns {}."""
        from tools.sdlc_meta_set import write_meta

        mock_ledger = MagicMock()
        mock_ledger.save.side_effect = RuntimeError("redis down")

        with (
            patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=mock_ledger),
        ):
            result = write_meta(key="pr_number", value="42", issue_number=1, run_id="run-test")

        assert result == {}

    def test_pr_number_invalid_returns_empty_dict(self):
        """D4: non-numeric / non-positive pr_number is rejected by write_meta (fail-soft)."""
        from tools.sdlc_meta_set import write_meta

        for bad in ("0", "-1", "abc", ""):
            assert write_meta(key="pr_number", value=bad, issue_number=1, run_id="run-test") == {}


class TestMetaSetWhitelist:
    """Tests for the key whitelist enforcement."""

    def test_whitelist_contains_expected_keys(self):
        """_KEY_REGISTRY must contain the whitelisted keys."""
        from tools.sdlc_meta_set import _KEY_REGISTRY

        assert "plan_revising" in _KEY_REGISTRY
        assert "plan_hash_at_build_start" in _KEY_REGISTRY
        assert "pr_number" in _KEY_REGISTRY
        # #2003 T1.7: pr_number is FIELD-backed (PipelineLedger.pr_number), not
        # a stage_states meta key — no leading underscore on the target.
        assert _KEY_REGISTRY["pr_number"] == ("pr_number", int)

    def test_whitelist_storage_targets_follow_convention(self):
        """Meta keys use the leading-underscore convention; field-backed keys
        (currently only pr_number) name a real PipelineLedger attribute."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_meta_set import _KEY_REGISTRY

        for public_key, (target, _) in _KEY_REGISTRY.items():
            if target.startswith("_"):
                continue  # stage_states meta key
            assert hasattr(PipelineLedger, target), (
                f"Field-backed key {public_key!r} targets {target!r}, "
                f"which is not a PipelineLedger attribute"
            )

    def test_do_build_addendum_documents_pr_number_writer(self):
        """Build-path pr_number contract (#2003): docs/sdlc/do-build.md must
        instruct writing the PR number via the single-writer command shape
        (`meta-set --key pr_number` with `--run-id`) after PR creation."""
        addendum = (REPO_ROOT / "docs" / "sdlc" / "do-build.md").read_text(encoding="utf-8")

        assert "meta-set --key pr_number" in addendum, (
            "do-build.md must document the pr_number single-writer command"
        )
        pr_line = next(line for line in addendum.splitlines() if "meta-set --key pr_number" in line)
        assert "--run-id" in pr_line, (
            "the documented pr_number writer command must carry --run-id (state-mutating)"
        )


class TestMetaSetCLI:
    """Subprocess tests for the CLI entry point."""

    def test_unknown_key_exits_2(self):
        """CLI exits 2 when an unknown key is provided."""
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_meta_set",
                "--key",
                "unknown_key",
                "--value",
                "x",
                "--run-id",
                "run-cli-test",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 2

    def test_missing_key_arg_exits_nonzero(self):
        """CLI exits nonzero when --key is missing."""
        proc = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_meta_set", "--value", "true"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode != 0

    def test_missing_value_arg_exits_nonzero(self):
        """CLI exits nonzero when --value is missing."""
        proc = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_meta_set", "--key", "plan_revising"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode != 0

    def test_no_issue_number_exits_1_with_lease_absent(self):
        """Issue #2012 task 2: this REPLACES the old fail-soft no-session
        exit-0 contract. Genuinely lease-less (no --issue-number, no
        established lease) CLI write now hard-fails LOUD: exit 1,
        LEASE_ABSENT."""
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_meta_set",
                "--key",
                "plan_revising",
                "--value",
                "true",
                "--run-id",
                "run-cli-test",
                # NO --issue-number: no issue context → no ledger key.
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 1
        assert "LEASE_ABSENT" in proc.stderr

    def test_pr_number_invalid_value_exits_2(self):
        """D4: CLI exits 2 for an invalid pr_number value (known key, bad value)."""
        for bad in ("0", "-5", "notanumber"):
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.sdlc_meta_set",
                    "--key",
                    "pr_number",
                    "--value",
                    bad,
                    "--run-id",
                    "run-cli-test",
                ],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            assert proc.returncode == 2, f"value {bad!r} should exit 2, got {proc.returncode}"

    def test_help_exits_0(self):
        """CLI --help exits 0."""
        proc = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_meta_set", "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 0
