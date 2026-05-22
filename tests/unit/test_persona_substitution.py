"""Unit tests for load_persona_prompt substitutions and CUSTOMER_ID env injection.

Tests cover:
- _SafeFormatDict: missing keys preserved as {key}
- load_persona_prompt(substitutions=...) applies substitutions
- load_persona_prompt(substitutions=None) backward-compatible
- ValorAgent._create_options injects CUSTOMER_ID when set
- ValorAgent._create_options does NOT inject CUSTOMER_ID when None
"""

from unittest.mock import patch

from agent.sdk_client import ValorAgent, _SafeFormatDict, load_persona_prompt

# ---------------------------------------------------------------------------
# _SafeFormatDict
# ---------------------------------------------------------------------------


def test_safe_format_dict_replaces_known_key():
    d = _SafeFormatDict({"customer_id": "cust-42"})
    assert "Hello {customer_id}".format_map(d) == "Hello cust-42"


def test_safe_format_dict_preserves_unknown_key():
    d = _SafeFormatDict({"customer_id": "cust-42"})
    result = "Hello {customer_id} and {unknown_key}".format_map(d)
    assert result == "Hello cust-42 and {unknown_key}"


def test_safe_format_dict_empty_dict_preserves_all():
    d = _SafeFormatDict({})
    result = "{a} {b}".format_map(d)
    assert result == "{a} {b}"


# ---------------------------------------------------------------------------
# load_persona_prompt with substitutions
# ---------------------------------------------------------------------------


def test_load_persona_prompt_customer_service_substitution(tmp_path):
    """customer_id placeholder in the persona file is substituted."""
    overlay = tmp_path / "customer-service.md"
    overlay.write_text("Hello customer {customer_id}, how can I help?")

    with (
        patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
        patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
        patch("agent.sdk_client.load_identity", return_value={}),
        patch("agent.sdk_client._assemble_segments", return_value="base content\n"),
    ):
        result = load_persona_prompt("customer-service", substitutions={"customer_id": "cust-42"})

    assert "cust-42" in result
    assert "{customer_id}" not in result


def test_load_persona_prompt_no_substitutions_backward_compat(tmp_path):
    """load_persona_prompt without substitutions works as before."""
    overlay = tmp_path / "teammate.md"
    overlay.write_text("Teammate persona content.")

    with (
        patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
        patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
        patch("agent.sdk_client.load_identity", return_value={}),
        patch("agent.sdk_client._assemble_segments", return_value="base content\n"),
    ):
        result = load_persona_prompt("teammate")

    assert "Teammate persona content." in result


def test_load_persona_prompt_substitutions_none_is_safe(tmp_path):
    """Passing substitutions=None is equivalent to not passing substitutions."""
    overlay = tmp_path / "teammate.md"
    overlay.write_text("Hello {customer_id} placeholder preserved.")

    with (
        patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
        patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
        patch("agent.sdk_client.load_identity", return_value={}),
        patch("agent.sdk_client._assemble_segments", return_value=""),
    ):
        result = load_persona_prompt("teammate", substitutions=None)

    # No substitutions: placeholder preserved
    assert "{customer_id}" in result


def test_load_persona_prompt_unreferenced_braces_preserved(tmp_path):
    """Braces not in substitutions dict are preserved, not raised as errors."""
    overlay = tmp_path / "customer-service.md"
    overlay.write_text("Customer: {customer_id}. Other: {other_key}.")

    with (
        patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
        patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
        patch("agent.sdk_client.load_identity", return_value={}),
        patch("agent.sdk_client._assemble_segments", return_value=""),
    ):
        result = load_persona_prompt("customer-service", substitutions={"customer_id": "cust-99"})

    assert "cust-99" in result
    assert "{other_key}" in result


# ---------------------------------------------------------------------------
# ValorAgent._create_options CUSTOMER_ID injection
# ---------------------------------------------------------------------------


def _make_agent(customer_id=None):
    """Create a ValorAgent with customer_id set, mocking filesystem checks."""
    with patch("agent.sdk_client.validate_workspace", side_effect=lambda p, *a, **kw: p):
        agent = ValorAgent(
            working_dir="/tmp",
            system_prompt="test prompt",
            customer_id=customer_id,
        )
    return agent


def test_valor_agent_customer_id_injected_in_env():
    """CUSTOMER_ID env var is set when customer_id is provided."""
    agent = _make_agent(customer_id="cust-42")
    with patch("agent.sdk_client._get_prior_session_uuid", return_value=None):
        with patch("agent.sdk_client.build_hooks_config", return_value={}):
            with patch("agent.sdk_client.get_agent_definitions", return_value={}):
                options = agent._create_options(session_id=None)
    assert options.env.get("CUSTOMER_ID") == "cust-42"


def test_valor_agent_no_customer_id_no_env_var():
    """CUSTOMER_ID env var is absent when customer_id is None."""
    agent = _make_agent(customer_id=None)
    with patch("agent.sdk_client._get_prior_session_uuid", return_value=None):
        with patch("agent.sdk_client.build_hooks_config", return_value={}):
            with patch("agent.sdk_client.get_agent_definitions", return_value={}):
                options = agent._create_options(session_id=None)
    assert "CUSTOMER_ID" not in options.env
