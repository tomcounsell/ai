"""Unit tests for tools.sdlc_meta_set CLI tool (issue #1302).

Tests cover:
- Valid key sets stage_states["_<key>"] via update_stage_states()
- Unknown key exits with code 2
- Missing session is fail-soft (returns {})
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


class TestMetaSetWriteMeta:
    """Tests for the write_meta() function."""

    def test_valid_bool_key_sets_value(self):
        """write_meta with plan_revising=True writes _plan_revising=True."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"
        mock_session.session_type = "eng"

        def fake_update_stage_states(session, update_fn, **kwargs):
            # Simulate applying the update to an empty dict
            result = update_fn({})
            assert result.get("_plan_revising") is True
            return True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(key="plan_revising", value="true")

        assert result == {"key": "plan_revising", "value": True}

    def test_valid_bool_key_false_clears_value(self):
        """write_meta with plan_revising=false writes _plan_revising=False."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = '{"_plan_revising": true}'
        mock_session.session_type = "eng"

        def fake_update_stage_states(session, update_fn, **kwargs):
            result = update_fn({"_plan_revising": True})
            assert result.get("_plan_revising") is False
            return True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(key="plan_revising", value="false")

        assert result == {"key": "plan_revising", "value": False}

    def test_valid_str_key_sets_value(self):
        """write_meta with plan_hash_at_build_start sets the string value."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"
        mock_session.session_type = "eng"
        test_hash = "abc123def456"

        def fake_update_stage_states(session, update_fn, **kwargs):
            result = update_fn({})
            assert result.get("_plan_hash_at_build_start") == test_hash
            return True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch(
                "tools.stage_states_helpers.update_stage_states",
                side_effect=fake_update_stage_states,
            ),
        ):
            result = write_meta(key="plan_hash_at_build_start", value=test_hash)

        assert result == {"key": "plan_hash_at_build_start", "value": test_hash}

    def test_unknown_key_returns_empty_dict(self):
        """write_meta with an unknown key returns {} (fail-soft)."""
        from tools.sdlc_meta_set import write_meta

        result = write_meta(key="nonexistent_key", value="anything")
        assert result == {}

    def test_missing_session_returns_empty_dict(self):
        """write_meta returns {} when no session can be found (fail-soft)."""
        from tools.sdlc_meta_set import write_meta

        with patch("tools.sdlc_meta_set.find_session", return_value=None):
            result = write_meta(key="plan_revising", value="true")

        assert result == {}

    def test_update_stage_states_failure_returns_empty_dict(self):
        """write_meta returns {} when update_stage_states returns False."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch("tools.stage_states_helpers.update_stage_states", return_value=False),
        ):
            result = write_meta(key="plan_revising", value="true")

        assert result == {}

    def test_bool_coercion_numeric_strings(self):
        """write_meta coerces '1'/'0' to True/False for bool keys."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        def fake_update_true(session, update_fn, **kwargs):
            result = update_fn({})
            assert result["_plan_revising"] is True
            return True

        def fake_update_false(session, update_fn, **kwargs):
            result = update_fn({})
            assert result["_plan_revising"] is False
            return True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=fake_update_true),
        ):
            r1 = write_meta(key="plan_revising", value="1")
        assert r1["value"] is True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=fake_update_false),
        ):
            r2 = write_meta(key="plan_revising", value="0")
        assert r2["value"] is False

    def test_invalid_bool_value_returns_empty_dict(self):
        """write_meta with an unrecognized bool value returns {} fail-soft."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        with patch("tools.sdlc_meta_set.find_session", return_value=mock_session):
            result = write_meta(key="plan_revising", value="not_a_bool")

        assert result == {}

    def test_pr_number_coerced_to_int(self):
        """D4: write_meta with pr_number stores _pr_number as an int."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        def fake_update(session, update_fn, **kwargs):
            result = update_fn({})
            assert result["_pr_number"] == 42
            assert isinstance(result["_pr_number"], int)
            return True

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session),
            patch("tools.stage_states_helpers.update_stage_states", side_effect=fake_update),
        ):
            result = write_meta(key="pr_number", value="42")

        assert result == {"key": "pr_number", "value": 42}

    def test_pr_number_invalid_returns_empty_dict(self):
        """D4: non-numeric / non-positive pr_number is rejected by write_meta (fail-soft)."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        with patch("tools.sdlc_meta_set.find_session", return_value=mock_session):
            for bad in ("0", "-1", "abc", ""):
                assert write_meta(key="pr_number", value=bad) == {}

    def test_write_passes_ensure_true_to_resolver(self):
        """write_meta resolves through find_session(..., ensure=True) so a
        sessionless-but-issue-numbered write auto-creates a PM session (#1558)."""
        from tools.sdlc_meta_set import write_meta

        mock_session = MagicMock()
        mock_session.session_type = "eng"

        with (
            patch("tools.sdlc_meta_set.find_session", return_value=mock_session) as find_mock,
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            result = write_meta(key="plan_revising", value="true", issue_number=1558)

        assert result == {"key": "plan_revising", "value": True}
        find_mock.assert_called_once_with(None, issue_number=1558, ensure=True)


class TestMetaSetWhitelist:
    """Tests for the key whitelist enforcement."""

    def test_whitelist_contains_expected_keys(self):
        """_KEY_REGISTRY must contain the whitelisted keys."""
        from tools.sdlc_meta_set import _KEY_REGISTRY

        assert "plan_revising" in _KEY_REGISTRY
        assert "plan_hash_at_build_start" in _KEY_REGISTRY
        assert "pr_number" in _KEY_REGISTRY
        assert _KEY_REGISTRY["pr_number"] == ("_pr_number", int)

    def test_whitelist_maps_to_underscore_internal_keys(self):
        """Internal storage keys must use leading underscore convention."""
        from tools.sdlc_meta_set import _KEY_REGISTRY

        for public_key, (internal_key, _) in _KEY_REGISTRY.items():
            assert internal_key.startswith("_"), (
                f"Internal key for {public_key!r} must start with '_'; got {internal_key!r}"
            )


class TestMetaSetCLI:
    """Subprocess tests for the CLI entry point."""

    def test_unknown_key_exits_2(self):
        """CLI exits 2 when an unknown key is provided."""
        proc = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_meta_set", "--key", "unknown_key", "--value", "x"],
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

    def test_no_session_no_issue_no_env_exits_0_with_empty_json(self):
        """Genuinely sessionless (no --issue-number, no env) CLI write still
        no-ops: exits 0 with {} output. Reads/writes never fabricate a session
        when there is no issue context to attach state to (#1558)."""
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_meta_set",
                "--key",
                "plan_revising",
                "--value",
                "true",
                # NO --issue-number: no issue context → ensure guard returns None.
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={
                **__import__("os").environ,
                # Remove session env vars so there is no resolvable session.
                "VALOR_SESSION_ID": "",
                "AGENT_SESSION_ID": "",
            },
        )
        # Fail-soft: should exit 0 with no fabricated session.
        assert proc.returncode == 0
        output = proc.stdout.strip()
        assert output == "{}", f"Expected '{{}}' but got: {output!r}"

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
