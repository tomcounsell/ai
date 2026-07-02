"""Validator matrix for the per-role transport block (plan #1842).

Covers ``bridge/config_validation.py::validate_transport`` and its registration
in the ``validate_projects_config`` aggregator: valid shapes pass, and every
malformed shape (non-dict block, unknown role key, out-of-vocabulary value)
raises a ``ConfigValidationError`` naming the offending project key.
"""

from __future__ import annotations

import pytest

from bridge.config_validation import (
    ConfigValidationError,
    validate_projects_config,
    validate_transport,
)


class TestValidateTransportAccepts:
    """Well-formed transport blocks (and absence) must pass."""

    @pytest.mark.parametrize(
        "transport",
        [
            {"pm": "pty", "dev": "headless"},
            {"pm": "pty", "dev": "pty"},
            {"dev": "headless"},  # pm omitted → defaults to pty downstream
            {"dev": "pty"},  # pm omitted → defaults downstream
            {},  # empty dict is valid
        ],
    )
    def test_valid_transport_blocks_pass(self, transport):
        validate_transport({"projects": {"proj": {"transport": transport}}})

    def test_absent_transport_block_passes(self):
        validate_transport({"projects": {"proj": {"machine": "m1"}}})

    def test_no_projects_passes(self):
        validate_transport({})

    def test_non_dict_project_config_skipped(self):
        # A project whose config is not a dict is skipped, not an error here.
        validate_transport({"projects": {"proj": "not-a-dict"}})


class TestValidateTransportRejects:
    """Malformed transport blocks must raise, naming the project key."""

    def test_non_dict_transport_block(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": "pty"}}})
        assert "proj" in str(exc.value)
        assert "non-dict" in str(exc.value)

    def test_transport_block_is_list(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": ["pty"]}}})
        assert "proj" in str(exc.value)

    def test_unknown_role_key(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": {"qa": "pty"}}}})
        assert "proj" in str(exc.value)
        assert "qa" in str(exc.value)

    def test_out_of_vocabulary_value(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": {"pm": "tmux"}}}})
        assert "proj" in str(exc.value)
        assert "tmux" in str(exc.value)

    def test_non_string_value(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": {"pm": 123}}}})
        assert "proj" in str(exc.value)

    @pytest.mark.parametrize(
        "transport",
        [
            {"pm": "headless", "dev": "pty"},
            {"pm": "headless", "dev": "headless"},
            {"pm": "headless"},
        ],
    )
    def test_pm_headless_rejected(self, transport):
        # PM headless is not yet supported (plan #1842 v1) — the PM startup /
        # login / plateau machinery is PTY-coupled. Reject with a clear message.
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport({"projects": {"proj": {"transport": transport}}})
        msg = str(exc.value)
        assert "proj" in msg
        assert "PM headless not yet supported" in msg

    def test_aggregates_multiple_errors(self):
        # Two problems across two projects — both must appear in one raise.
        with pytest.raises(ConfigValidationError) as exc:
            validate_transport(
                {
                    "projects": {
                        "a": {"transport": {"pm": "bogus"}},
                        "b": {"transport": {"unknown": "pty"}},
                    }
                }
            )
        msg = str(exc.value)
        assert "a" in msg and "b" in msg


class TestAggregatorRegistration:
    """validate_transport must run inside validate_projects_config."""

    def test_transport_error_surfaces_through_aggregator(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_projects_config({"projects": {"proj": {"transport": {"pm": "nope"}}}})
        assert "transport" in str(exc.value).lower()
        assert "proj" in str(exc.value)

    def test_valid_transport_passes_aggregator(self):
        # A config with a valid transport block and no other violations passes.
        validate_projects_config({"projects": {"proj": {"transport": {"dev": "headless"}}}})

    def test_pm_headless_surfaces_through_aggregator(self):
        with pytest.raises(ConfigValidationError) as exc:
            validate_projects_config({"projects": {"proj": {"transport": {"pm": "headless"}}}})
        assert "PM headless not yet supported" in str(exc.value)
