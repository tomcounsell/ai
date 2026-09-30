"""Tests for happy path Rodney script generator."""

import json
import stat

from tools.happy_path_generator import (
    generate_from_file,
    generate_script,
    generate_step_command,
    resolve_credential_placeholder,
)
from tools.happy_path_schema import Trace, TraceStep

# ── Credential resolution ────────────────────────────────────────────


class TestCredentialResolution:
    def test_username_placeholder(self):
        result = resolve_credential_placeholder("{{credentials.username}}")
        assert result == "$HAPPY_PATH_USERNAME"

    def test_password_placeholder(self):
        result = resolve_credential_placeholder("{{credentials.password}}")
        assert result == "$HAPPY_PATH_PASSWORD"

    def test_no_placeholder(self):
        result = resolve_credential_placeholder("plain text")
        assert result == "plain text"

    def test_multiple_placeholders(self):
        result = resolve_credential_placeholder("{{credentials.user}}:{{credentials.pass}}")
        assert result == "$HAPPY_PATH_USER:$HAPPY_PATH_PASS"


# ── Step command generation ──────────────────────────────────────────


class TestStepCommandGeneration:
    def test_navigate(self):
        cmd = generate_step_command({"action": "navigate", "url": "https://example.com"})
        assert cmd == "rodney open 'https://example.com'"

    def test_click(self):
        cmd = generate_step_command({"action": "click", "selector": "#submit"})
        assert cmd == "rodney click '#submit'"

    def test_input(self):
        cmd = generate_step_command(
            {"action": "input", "selector": "#email", "value": "test@test.com"}
        )
        assert cmd == "rodney input '#email' 'test@test.com'"

    def test_input_with_credential(self):
        cmd = generate_step_command(
            {
                "action": "input",
                "selector": "#pass",
                "value": "{{credentials.password}}",
            }
        )
        assert cmd is not None
        assert "$HAPPY_PATH_PASSWORD" in cmd
        # Credential env vars should be in double quotes
        assert '"$HAPPY_PATH_PASSWORD"' in cmd

    def test_wait(self):
        cmd = generate_step_command({"action": "wait", "selector": ".loaded"})
        assert cmd == "rodney wait '.loaded'"

    def test_screenshot(self):
        cmd = generate_step_command({"action": "screenshot", "path": "evidence/shot.png"})
        assert cmd == "rodney screenshot 'evidence/shot.png'"

    def test_exists(self):
        cmd = generate_step_command({"action": "exists", "selector": ".avatar"})
        assert cmd == "rodney exists '.avatar'"

    def test_assert_url_contains(self):
        cmd = generate_step_command(
            {"action": "assert", "type": "url_contains", "value": "/dashboard"}
        )
        assert cmd is not None
        assert "rodney assert" in cmd
        assert "window.location.href.includes" in cmd

    def test_assert_text_visible(self):
        cmd = generate_step_command(
            {"action": "assert", "type": "text_visible", "value": "Welcome"}
        )
        assert cmd is not None
        assert "document.body.innerText.includes" in cmd

    def test_assert_title_equals(self):
        cmd = generate_step_command(
            {"action": "assert", "type": "title_equals", "value": "Dashboard"}
        )
        assert cmd is not None
        assert "document.title ===" in cmd

    def test_assert_element_exists(self):
        cmd = generate_step_command(
            {"action": "assert", "type": "element_exists", "value": ".header"}
        )
        assert cmd is not None
        assert "document.querySelector" in cmd

    def test_unknown_action_returns_none(self):
        cmd = generate_step_command({"action": "hover", "selector": "#x"})
        assert cmd is None

    def test_missing_selector_returns_none(self):
        cmd = generate_step_command({"action": "click"})
        assert cmd is None

    def test_missing_url_returns_none(self):
        cmd = generate_step_command({"action": "navigate"})
        assert cmd is None


# ── Script generation ────────────────────────────────────────────────


class TestScriptGeneration:
    def test_generates_executable_script(self, tmp_path):
        trace = Trace(
            name="test-flow",
            url="https://example.com",
            steps=[
                TraceStep(action="navigate", url="https://example.com"),
                TraceStep(action="click", selector="#button"),
            ],
        )
        output = tmp_path / "test-flow.sh"
        result = generate_script(trace, output)

        assert result is True
        assert output.exists()
        content = output.read_text()
        assert "#!/usr/bin/env bash" in content
        assert "set -euo pipefail" in content
        assert "rodney open" in content
        assert "rodney click" in content
        assert "PASS: test-flow" in content
        # Should be executable
        assert output.stat().st_mode & stat.S_IXUSR

    def test_includes_final_assertions(self, tmp_path):
        trace = Trace(
            name="test",
            url="https://example.com",
            steps=[TraceStep(action="navigate", url="https://example.com")],
            expected_final_url="/dashboard",
            expected_text=["Welcome", "Success"],
        )
        output = tmp_path / "test.sh"
        generate_script(trace, output)
        content = output.read_text()

        assert "Final URL assertion" in content
        assert "/dashboard" in content
        assert "Final text assertions" in content
        assert "Welcome" in content
        assert "Success" in content

    def test_empty_steps_returns_false(self, tmp_path):
        trace = Trace(name="empty", url="https://example.com", steps=[])
        output = tmp_path / "empty.sh"
        result = generate_script(trace, output)
        assert result is False
        assert not output.exists()

    def test_no_credentials_inlined(self, tmp_path):
        trace = Trace(
            name="login",
            url="https://example.com",
            steps=[
                TraceStep(action="navigate", url="https://example.com"),
                TraceStep(
                    action="input",
                    selector="#email",
                    value="{{credentials.username}}",
                ),
                TraceStep(
                    action="input",
                    selector="#pass",
                    value="{{credentials.password}}",
                ),
            ],
        )
        output = tmp_path / "login.sh"
        generate_script(trace, output)
        content = output.read_text()

        # Must NOT contain literal credential placeholders
        assert "{{credentials" not in content
        # Must contain env var references
        assert "$HAPPY_PATH_USERNAME" in content
        assert "$HAPPY_PATH_PASSWORD" in content


# ── File-based generation ────────────────────────────────────────────


class TestGenerateFromFile:
    def test_generates_from_json_file(self, tmp_path):
        trace_data = {
            "name": "simple-nav",
            "url": "https://example.com",
            "steps": [{"action": "navigate", "url": "https://example.com"}],
        }
        trace_file = tmp_path / "traces" / "simple-nav.json"
        trace_file.parent.mkdir(parents=True)
        trace_file.write_text(json.dumps(trace_data))

        output_dir = tmp_path / "scripts"
        result = generate_from_file(trace_file, output_dir)

        assert result is True
        script = output_dir / "simple-nav.sh"
        assert script.exists()

    def test_handles_malformed_json(self, tmp_path):
        trace_file = tmp_path / "bad.json"
        trace_file.write_text("not json{{{")

        result = generate_from_file(trace_file, tmp_path / "scripts")
        assert result is False

    def test_handles_invalid_trace(self, tmp_path):
        trace_file = tmp_path / "invalid.json"
        trace_file.write_text(json.dumps({"name": "", "url": "", "steps": []}))

        result = generate_from_file(trace_file, tmp_path / "scripts")
        assert result is False
