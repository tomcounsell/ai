"""Unit tests for load_persona_prompt substitutions.

Tests cover:
- _apply_substitutions: named placeholders replaced, every other brace verbatim
- load_persona_prompt(substitutions=...) applies substitutions
- load_persona_prompt(substitutions=None) backward-compatible
- the real segment files survive a substituting call (issue #2560)

The ValorAgent._create_options CUSTOMER_ID env-var injection tests that used
to live here were removed (plan #2000 Task 2.2 dead-SDK-path deletion):
CUSTOMER_ID was ValorAgent-only env injection with zero occurrences anywhere
in the codebase outside that (now-deleted) class -- there is no CLI-harness
equivalent to re-test against, so this was genuinely dead functionality, not
relocated functionality.
"""

from unittest.mock import patch

from agent.sdk_client import PERSONAS_SEGMENTS_DIR, _apply_substitutions, load_persona_prompt

# ---------------------------------------------------------------------------
# _apply_substitutions
# ---------------------------------------------------------------------------


def test_apply_substitutions_replaces_known_key():
    assert _apply_substitutions("Hello {customer_id}", {"customer_id": "cust-42"}) == (
        "Hello cust-42"
    )


def test_apply_substitutions_preserves_unknown_key():
    result = _apply_substitutions(
        "Hello {customer_id} and {unknown_key}", {"customer_id": "cust-42"}
    )
    assert result == "Hello cust-42 and {unknown_key}"


def test_apply_substitutions_empty_dict_preserves_all():
    assert _apply_substitutions("{a} {b}", {}) == "{a} {b}"


def test_apply_substitutions_leaves_literal_json_alone():
    """Issue #2560: a JSON literal in prose must not be parsed as a format field."""
    text = 'Pass --target \'{"kind":"node_id","value":...}\' for {customer_id}.'
    result = _apply_substitutions(text, {"customer_id": "cust-42"})
    assert '{"kind":"node_id","value":...}' in result
    assert "cust-42" in result


def test_apply_substitutions_survives_every_shipped_segment():
    """Every real segment file must pass through substitution untouched.

    Segment files are documentation-shaped and keep acquiring JSON and shell
    examples; this is the guard that a new one cannot break persona composition
    for any caller that passes substitutions.
    """
    segments = sorted(PERSONAS_SEGMENTS_DIR.glob("*.md"))
    assert segments, f"no segment files found under {PERSONAS_SEGMENTS_DIR}"
    for seg in segments:
        content = seg.read_text()
        assert _apply_substitutions(content, {"customer_id": "cust-42"}) == content.replace(
            "{customer_id}", "cust-42"
        )


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


def test_load_persona_prompt_substitutes_over_real_segments(tmp_path):
    """Issue #2560: real segment assembly plus substitutions, no mocked segments.

    The other substitution tests stub ``_assemble_segments``, which is exactly
    why the JSON literal in ``segments/tools.md`` never fired in the suite.
    """
    overlay = tmp_path / "customer-service.md"
    overlay.write_text("Customer: {customer_id}.")

    with (
        patch("agent.sdk_client.PERSONAS_OVERLAY_DIR", tmp_path),
        patch("agent.sdk_client.PERSONAS_BASE_DIR", tmp_path),
    ):
        result = load_persona_prompt("customer-service", substitutions={"customer_id": "cust-7"})

    assert "Customer: cust-7." in result
    assert '{"kind":"node_id","value":...}' in result
