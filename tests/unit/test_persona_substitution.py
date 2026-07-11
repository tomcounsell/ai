"""Unit tests for load_persona_prompt substitutions.

Tests cover:
- _SafeFormatDict: missing keys preserved as {key}
- load_persona_prompt(substitutions=...) applies substitutions
- load_persona_prompt(substitutions=None) backward-compatible

The ValorAgent._create_options CUSTOMER_ID env-var injection tests that used
to live here were removed (plan #2000 Task 2.2 dead-SDK-path deletion):
CUSTOMER_ID was ValorAgent-only env injection with zero occurrences anywhere
in the codebase outside that (now-deleted) class -- there is no CLI-harness
equivalent to re-test against, so this was genuinely dead functionality, not
relocated functionality.
"""

from unittest.mock import patch

from agent.sdk_client import _SafeFormatDict, load_persona_prompt

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
