"""Tests for config/settings.py's TimeoutSettings group (issue #1968).

Covers the centralized timeout/TTL catalog scaffolded to replace the ~179
inline subprocess/HTTP `timeout=` literals scattered across the codebase.
Field-default assertions here are the normalization contract other builders
(subprocess sweep, http/ttl sweep) depend on -- if a default changes here,
every call site that migrated to `settings.timeouts.<field>` changes too.
"""

import pytest
from pydantic import ValidationError

from config.settings import CodexSettings, ImprovementSettings, Settings, TimeoutSettings


class TestTimeoutSettingsDefaults:
    """Defaults match the normalized (longest-per-category) values."""

    def test_git_subprocess_default(self):
        """60s, not the plan's originally-scaffolded 30s: the Task 2 subprocess
        sweep discovered monitoring/bridge_watchdog.py's `git revert HEAD
        --no-edit` self-healing step already used 60s, the true longest
        pre-existing literal in this category (Decision #1 normalizes to
        the longest value, never a shorter one)."""
        assert TimeoutSettings().git_subprocess_s == 60.0

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


class TestImprovementSettingsBudgetUnits:
    """The three budget units charter v2 reserves separately (#3255).

    Two are dollars on different windows, and the third is not money at all
    (Claude concurrency). The window boundaries are typed rather than free
    strings so a bad override fails at settings load rather than at the first
    window computation.
    """

    def test_budget_defaults(self):
        s = ImprovementSettings()

        assert s.daily_paid_inference_usd == 10.00
        assert s.weekly_infrastructure_usd == 50.00
        assert s.budget_day_boundary == "UTC"
        assert s.budget_week_start == "monday"

    def test_concurrency_is_still_the_third_unit(self):
        assert ImprovementSettings().max_concurrent_research_sessions == 1

    @pytest.mark.parametrize(
        "withdrawn",
        ["daily_external_llm_usd", "portfolio_allocation", "daily_question_ceiling"],
    )
    def test_withdrawn_vocabulary_is_absent(self, withdrawn):
        """Charter v2 replaced all three; a leftover would be read as current."""
        assert withdrawn not in ImprovementSettings.model_fields

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("budget_day_boundary", "PST"),
            ("budget_week_start", "Monday"),
            ("daily_paid_inference_usd", -1.0),
            ("weekly_infrastructure_usd", -1.0),
        ],
    )
    def test_invalid_values_fail_at_load(self, field, bad_value):
        with pytest.raises(ValidationError):
            ImprovementSettings(**{field: bad_value})


class TestCodexSettings:
    """CODEX__* knobs for the opt-in Codex dev lane (plan #2001, Phase 3)."""

    def test_defaults(self):
        s = CodexSettings()

        assert s.install_enabled is False
        assert s.npm_package == "@openai/codex"
        assert s.min_version == "0.144.3"
        assert s.model is None
        assert s.sandbox == "workspace-write"
        assert s.turn_timeout_s == 600.0
        assert s.max_resumed_turns == 10

    def test_wired_into_settings(self):
        assert Settings().codex.sandbox == "workspace-write"

    def test_per_key_env_override(self, monkeypatch):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")  # skip reading the real .env file
        monkeypatch.setenv("CODEX__SANDBOX", "danger-full-access")
        monkeypatch.setenv("CODEX__MAX_RESUMED_TURNS", "20")
        monkeypatch.setenv("CODEX__TURN_TIMEOUT_S", "120")
        monkeypatch.setenv("CODEX__MODEL", "gpt-5-codex")

        fresh = Settings()

        assert fresh.codex.sandbox == "danger-full-access"
        assert fresh.codex.max_resumed_turns == 20
        assert fresh.codex.turn_timeout_s == 120.0
        assert fresh.codex.model == "gpt-5-codex"

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("sandbox", "read-only"),
            ("turn_timeout_s", 0),
            ("max_resumed_turns", 0),
            ("max_resumed_turns", 101),
        ],
    )
    def test_invalid_values_fail_at_load(self, field, bad_value):
        with pytest.raises(ValidationError):
            CodexSettings(**{field: bad_value})
