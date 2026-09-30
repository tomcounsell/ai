"""Tests for happy path trace JSON schema validation."""

import pytest

from tools.happy_path_schema import (
    Trace,
    TraceStep,
    parse_trace,
    validate_trace_file,
)

# ── TraceStep validation ─────────────────────────────────────────────


class TestTraceStepValidation:
    def test_valid_navigate_step(self):
        step = TraceStep(action="navigate", url="https://example.com")
        assert step.validate() == []

    def test_valid_click_step(self):
        step = TraceStep(action="click", selector="#submit")
        assert step.validate() == []

    def test_valid_input_step(self):
        step = TraceStep(action="input", selector="#email", value="test@test.com")
        assert step.validate() == []

    def test_valid_wait_step(self):
        step = TraceStep(action="wait", selector=".loaded")
        assert step.validate() == []

    def test_valid_assert_step(self):
        step = TraceStep(action="assert", type="url_contains", value="/dashboard")
        assert step.validate() == []

    def test_valid_screenshot_step(self):
        step = TraceStep(action="screenshot", path="evidence/shot.png")
        assert step.validate() == []

    def test_valid_exists_step(self):
        step = TraceStep(action="exists", selector=".avatar")
        assert step.validate() == []

    def test_missing_action(self):
        step = TraceStep(action="")
        errors = step.validate()
        assert len(errors) == 1
        assert "missing required 'action'" in errors[0]

    def test_invalid_action(self):
        step = TraceStep(action="hover")
        errors = step.validate()
        assert len(errors) == 1
        assert "Invalid action" in errors[0]

    def test_navigate_missing_url(self):
        step = TraceStep(action="navigate")
        errors = step.validate()
        assert any("requires 'url'" in e for e in errors)

    def test_click_missing_selector(self):
        step = TraceStep(action="click")
        errors = step.validate()
        assert any("requires 'selector'" in e for e in errors)

    def test_input_missing_selector(self):
        step = TraceStep(action="input", value="text")
        errors = step.validate()
        assert any("requires 'selector'" in e for e in errors)

    def test_input_missing_value(self):
        step = TraceStep(action="input", selector="#field")
        errors = step.validate()
        assert any("requires 'value'" in e for e in errors)

    def test_assert_missing_type(self):
        step = TraceStep(action="assert", value="something")
        errors = step.validate()
        assert any("requires 'type'" in e for e in errors)

    def test_assert_invalid_type(self):
        step = TraceStep(action="assert", type="invalid_type", value="x")
        errors = step.validate()
        assert any("Invalid assert type" in e for e in errors)

    def test_assert_missing_value(self):
        step = TraceStep(action="assert", type="url_contains")
        errors = step.validate()
        assert any("requires 'value'" in e for e in errors)


# ── Trace validation ─────────────────────────────────────────────────


class TestTraceValidation:
    def test_valid_trace(self):
        trace = Trace(
            name="login",
            url="https://example.com",
            steps=[TraceStep(action="navigate", url="https://example.com")],
        )
        assert trace.validate() == []

    def test_missing_name(self):
        trace = Trace(name="", url="https://example.com", steps=[])
        errors = trace.validate()
        assert any("missing required 'name'" in e for e in errors)

    def test_missing_url(self):
        trace = Trace(name="test", url="", steps=[])
        errors = trace.validate()
        assert any("missing required 'url'" in e for e in errors)

    def test_empty_steps(self):
        trace = Trace(name="test", url="https://example.com", steps=[])
        errors = trace.validate()
        assert any("no steps" in e for e in errors)

    def test_step_errors_propagate(self):
        trace = Trace(
            name="test",
            url="https://example.com",
            steps=[TraceStep(action="click")],  # missing selector
        )
        errors = trace.validate()
        assert any("Step 0" in e for e in errors)


# ── parse_trace ──────────────────────────────────────────────────────


class TestParseTrace:
    def test_parse_valid_trace(self):
        data = {
            "name": "login-flow",
            "url": "https://app.com/login",
            "steps": [
                {"action": "navigate", "url": "https://app.com/login"},
                {"action": "input", "selector": "#email", "value": "user@test.com"},
                {"action": "click", "selector": "#submit"},
            ],
            "expected_final_url": "/dashboard",
            "expected_text": ["Welcome"],
        }
        trace = parse_trace(data)
        assert trace.name == "login-flow"
        assert trace.url == "https://app.com/login"
        assert len(trace.steps) == 3
        assert trace.steps[0].action == "navigate"
        assert trace.steps[1].selector == "#email"
        assert trace.expected_final_url == "/dashboard"
        assert trace.expected_text == ["Welcome"]

    def test_parse_minimal_trace(self):
        data = {
            "name": "simple",
            "url": "https://example.com",
            "steps": [{"action": "navigate", "url": "https://example.com"}],
        }
        trace = parse_trace(data)
        assert trace.name == "simple"
        assert trace.expected_final_url is None
        assert trace.expected_text == []

    def test_parse_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            parse_trace("not a dict")

    def test_parse_non_list_steps_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            parse_trace({"name": "x", "url": "y", "steps": "not a list"})

    def test_parse_non_dict_step_raises(self):
        with pytest.raises(ValueError, match="Each step must be a dict"):
            parse_trace({"name": "x", "url": "y", "steps": ["not a dict"]})

    def test_parse_expected_text_string_coerced(self):
        data = {
            "name": "test",
            "url": "https://example.com",
            "steps": [{"action": "navigate", "url": "https://example.com"}],
            "expected_text": "single string",
        }
        trace = parse_trace(data)
        assert trace.expected_text == ["single string"]


# ── validate_trace_file ──────────────────────────────────────────────


class TestValidateTraceFile:
    def test_valid_file(self):
        data = {
            "name": "test",
            "url": "https://example.com",
            "steps": [{"action": "navigate", "url": "https://example.com"}],
        }
        is_valid, errors = validate_trace_file(data)
        assert is_valid
        assert errors == []

    def test_invalid_structure(self):
        is_valid, errors = validate_trace_file("not a dict")
        assert not is_valid
        assert len(errors) > 0

    def test_invalid_content(self):
        data = {"name": "", "url": "", "steps": []}
        is_valid, errors = validate_trace_file(data)
        assert not is_valid
        assert len(errors) > 0

    def test_full_example_trace(self):
        """Validate the example trace from the plan document."""
        data = {
            "name": "login-to-dashboard",
            "url": "https://myapp.com/login",
            "steps": [
                {"action": "navigate", "url": "https://myapp.com/login"},
                {
                    "action": "input",
                    "selector": "#email",
                    "value": "{{credentials.username}}",
                },
                {
                    "action": "input",
                    "selector": "#password",
                    "value": "{{credentials.password}}",
                },
                {"action": "click", "selector": "button[type=submit]"},
                {"action": "wait", "selector": ".dashboard-header"},
                {
                    "action": "assert",
                    "type": "url_contains",
                    "value": "/dashboard",
                },
                {
                    "action": "screenshot",
                    "path": "evidence/login-to-dashboard-final.png",
                },
            ],
            "expected_final_url": "**/dashboard",
            "expected_text": ["Welcome", "Dashboard"],
        }
        is_valid, errors = validate_trace_file(data)
        assert is_valid, f"Example trace should be valid, got errors: {errors}"
