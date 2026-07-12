"""Tests for config/settings.py's TimeoutSettings group (issue #1968).

Covers the centralized timeout/TTL catalog scaffolded to replace the ~179
inline subprocess/HTTP `timeout=` literals scattered across the codebase.
Field-default assertions here are the normalization contract other builders
(subprocess sweep, http/ttl sweep) depend on -- if a default changes here,
every call site that migrated to `settings.timeouts.<field>` changes too.
"""

import pytest
from pydantic import ValidationError

from config.settings import Settings, TimeoutSettings


class TestTimeoutSettingsDefaults:
    """Defaults match the normalized (longest-per-category) values."""

    def test_git_subprocess_default(self):
        assert TimeoutSettings().git_subprocess_s == 30.0

    def test_subprocess_default(self):
        assert TimeoutSettings().subprocess_default_s == 300.0

    def test_http_request_default(self):
        assert TimeoutSettings().http_request_s == 30.0

    def test_smtp_default(self):
        assert TimeoutSettings().smtp_s == 30.0

    def test_redis_socket_default(self):
        assert TimeoutSettings().redis_socket_s == 5.0

    def test_anthropic_sdk_default(self):
        """Must match agent/llm/wrapper.py DEFAULT_SDK_TIMEOUT (issue #1925)."""
        assert TimeoutSettings().anthropic_sdk_s == 30.0

    def test_anthropic_hard_default(self):
        """Must match agent/llm/wrapper.py DEFAULT_HARD_TIMEOUT (issue #1925)."""
        assert TimeoutSettings().anthropic_hard_s == 35.0

    def test_anthropic_hard_exceeds_sdk_timeout(self):
        """The hard cap must stay strictly above the inner SDK timer by default."""
        s = TimeoutSettings()
        assert s.anthropic_hard_s > s.anthropic_sdk_s

    def test_agent_session_retain_ttl_default(self):
        """Must match models/agent_session.py's retain_for_resume Meta.ttl."""
        assert TimeoutSettings().agent_session_retain_ttl_s == 2592000

    def test_last_processed_ttl_default(self):
        """Must match models/last_processed.py's Meta.ttl."""
        assert TimeoutSettings().last_processed_ttl_s == 2592000

    def test_wired_into_settings_via_timeouts_attribute(self):
        """Settings.timeouts must expose the group (import-time smoke check)."""
        from config.settings import settings

        assert isinstance(settings.timeouts, TimeoutSettings)


class TestTimeoutSettingsBounds:
    """ge/le bounds reject out-of-range values so a bad .env override fails loudly."""

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("git_subprocess_s", -1),
            ("git_subprocess_s", 10_000),
            ("subprocess_default_s", -1),
            ("subprocess_default_s", 10_000),
            ("http_request_s", -1),
            ("http_request_s", 10_000),
            ("smtp_s", -1),
            ("smtp_s", 10_000),
            ("redis_socket_s", -1),
            ("redis_socket_s", 10_000),
            ("anthropic_sdk_s", -1),
            ("anthropic_sdk_s", 10_000),
            ("anthropic_hard_s", -1),
            ("anthropic_hard_s", 10_000),
            ("agent_session_retain_ttl_s", -1),
            ("agent_session_retain_ttl_s", 99_999_999),
            ("last_processed_ttl_s", -1),
            ("last_processed_ttl_s", 99_999_999),
        ],
    )
    def test_out_of_bounds_value_raises(self, field, bad_value):
        with pytest.raises(ValidationError):
            TimeoutSettings(**{field: bad_value})

    def test_session_ttl_bound_permits_current_thirty_day_value(self):
        """le=2592000 must NOT reject the live 30-day session TTL (issue #1927 drift)."""
        s = TimeoutSettings(agent_session_retain_ttl_s=2592000, last_processed_ttl_s=2592000)
        assert s.agent_session_retain_ttl_s == 2592000
        assert s.last_processed_ttl_s == 2592000


class TestTimeoutSettingsEnvOverride:
    """TIMEOUTS__* nested env vars actually change the observed value."""

    def test_git_subprocess_env_override(self, monkeypatch):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")  # skip reading the real .env file
        monkeypatch.setenv("TIMEOUTS__GIT_SUBPROCESS_S", "45")

        fresh = Settings()

        assert fresh.timeouts.git_subprocess_s == 45.0

    def test_anthropic_pair_env_override(self, monkeypatch):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        monkeypatch.setenv("TIMEOUTS__ANTHROPIC_SDK_S", "20")
        monkeypatch.setenv("TIMEOUTS__ANTHROPIC_HARD_S", "25")

        fresh = Settings()

        assert fresh.timeouts.anthropic_sdk_s == 20.0
        assert fresh.timeouts.anthropic_hard_s == 25.0

    def test_out_of_bounds_env_override_raises(self, monkeypatch):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        monkeypatch.setenv("TIMEOUTS__GIT_SUBPROCESS_S", "-1")

        with pytest.raises(ValidationError):
            Settings()
