"""Generate deterministic Rodney shell scripts from happy path trace JSON.

Pure Python, no LLM involvement. Reads trace JSON files and emits standalone
shell scripts that use Rodney for headless Chrome test execution.

Usage:
    python tools/happy_path_generator.py tests/happy-paths/traces/login.json
    python tools/happy_path_generator.py tests/happy-paths/traces/  # all traces
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

from tools.happy_path_schema import Trace, parse_trace

logger = logging.getLogger(__name__)

# Rodney command mapping (validated against Rodney v0.4.0)
ACTION_MAP = {
    "navigate": "rodney open",
    "input": "rodney input",
    "click": "rodney click",
    "wait": "rodney wait",
    "screenshot": "rodney screenshot",
    "exists": "rodney exists",
}

# Credential placeholder pattern: {{credentials.field_name}}
CREDENTIAL_PATTERN = re.compile(r"\{\{credentials\.(\w+)\}\}")


def resolve_credential_placeholder(value: str) -> str:
    """Convert credential placeholders to shell environment variable references.

    {{credentials.username}} -> $HAPPY_PATH_USERNAME
    {{credentials.password}} -> $HAPPY_PATH_PASSWORD
    """

    def _replace(match: re.Match) -> str:
        field_name = match.group(1).upper()
        return f"$HAPPY_PATH_{field_name}"

    return CREDENTIAL_PATTERN.sub(_replace, value)


def _shell_escape(value: str) -> str:
    """Escape a value for safe use in shell scripts."""
    # If value contains env var references, use double quotes
    if value.startswith("$"):
        return f'"{value}"'
    # Otherwise single-quote for literal strings
    return "'" + value.replace("'", "'\\''") + "'"


def generate_step_command(step_data: dict) -> str | None:
    """Generate a single Rodney command line from a trace step.

    Args:
        step_data: A trace step as a dict with action, selector, value, etc.

    Returns:
        Shell command string, or None if the step should be skipped.
    """
    action = step_data.get("action", "")

    if action == "navigate":
        url = step_data.get("url", "")
        if not url:
            logger.warning("Skipping navigate step: missing url")
            return None
        return f"rodney open {_shell_escape(url)}"

    if action == "click":
        selector = step_data.get("selector", "")
        if not selector:
            logger.warning("Skipping click step: missing selector")
            return None
        return f"rodney click {_shell_escape(selector)}"

    if action == "input":
        selector = step_data.get("selector", "")
        value = step_data.get("value", "")
        if not selector:
            logger.warning("Skipping input step: missing selector")
            return None
        resolved_value = resolve_credential_placeholder(value)
        return f"rodney input {_shell_escape(selector)} {_shell_escape(resolved_value)}"

    if action == "wait":
        selector = step_data.get("selector", "")
        if not selector:
            logger.warning("Skipping wait step: missing selector")
            return None
        return f"rodney wait {_shell_escape(selector)}"

    if action == "assert":
        assert_type = step_data.get("type", "")
        value = step_data.get("value", "")
        if not assert_type or value is None:
            logger.warning("Skipping assert step: missing type or value")
            return None
        # Map assert types to Rodney JS expressions
        if assert_type == "url_contains":
            js_expr = f"window.location.href.includes({_shell_escape(value)})"
        elif assert_type == "text_visible":
            js_expr = f"document.body.innerText.includes({_shell_escape(value)})"
        elif assert_type == "title_equals":
            js_expr = f"document.title === {_shell_escape(value)}"
        elif assert_type == "element_exists":
            js_expr = f"document.querySelector({_shell_escape(value)}) !== null"
        else:
            logger.warning("Skipping assert step: unknown type %s", assert_type)
            return None
        return f"rodney assert {_shell_escape(js_expr)}"

    if action == "screenshot":
        path = step_data.get("path", "screenshot.png")
        return f"rodney screenshot {_shell_escape(path)}"

    if action == "exists":
        selector = step_data.get("selector", "")
        if not selector:
            logger.warning("Skipping exists step: missing selector")
            return None
        return f"rodney exists {_shell_escape(selector)}"

    logger.warning("Unknown action '%s', skipping step", action)
    return None


def generate_script(trace: Trace, output_path: Path) -> bool:
    """Generate a Rodney shell script from a trace.

    Args:
        trace: Validated Trace instance.
        output_path: Path to write the shell script.

    Returns:
        True if script was generated, False if trace had no valid steps.
    """
    errors = trace.validate()
    if errors:
        for err in errors:
            logger.error("Trace validation error: %s", err)
        return False

    commands: list[str] = []
    for step in trace.steps:
        step_dict = {
            "action": step.action,
            "url": step.url,
            "selector": step.selector,
            "value": step.value,
            "type": step.type,
            "path": step.path,
        }
        cmd = generate_step_command(step_dict)
        if cmd is not None:
            commands.append(cmd)

    if not commands:
        logger.warning(
            "Trace '%s' produced no valid commands, skipping script generation",
            trace.name,
        )
        return False

    # Build the shell script
    lines = [
        "#!/usr/bin/env bash",
        f"# Happy path test: {trace.name}",
        "# Generated from trace. Do not edit manually.",
        f"# Source URL: {trace.url}",
        "",
        "set -euo pipefail",
        "",
    ]

    for cmd in commands:
        lines.append(cmd)

    # Add final assertions if specified in trace
    if trace.expected_final_url:
        pattern = trace.expected_final_url
        js_expr = f"window.location.href.includes('{pattern}')"
        lines.append("")
        lines.append("# Final URL assertion")
        lines.append(f"rodney assert {_shell_escape(js_expr)}")

    if trace.expected_text:
        lines.append("")
        lines.append("# Final text assertions")
        for text in trace.expected_text:
            js_expr = f"document.body.innerText.includes('{text}')"
            lines.append(f"rodney assert {_shell_escape(js_expr)}")

    lines.append("")
    lines.append(f"echo 'PASS: {trace.name}'")
    lines.append("")

    script_content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(script_content)
    output_path.chmod(0o755)

    logger.info("Generated script: %s (%d commands)", output_path, len(commands))
    return True


def generate_from_file(
    trace_path: Path,
    output_dir: Path | None = None,
) -> bool:
    """Generate a Rodney script from a trace JSON file.

    Args:
        trace_path: Path to trace JSON file.
        output_dir: Directory for output scripts. Defaults to
            tests/happy-paths/scripts/.

    Returns:
        True if script was generated successfully.
    """
    if output_dir is None:
        output_dir = Path("tests/happy-paths/scripts")

    try:
        data = json.loads(trace_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read trace file %s: %s", trace_path, e)
        return False

    try:
        trace = parse_trace(data)
    except ValueError as e:
        logger.error("Invalid trace format in %s: %s", trace_path, e)
        return False

    output_path = output_dir / f"{trace.name}.sh"
    return generate_script(trace, output_path)


def main() -> int:
    """CLI entry point: generate scripts from trace files."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m tools.happy_path_generator <trace.json|traces_dir/>")
        return 2

    target = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    if target.is_dir():
        trace_files = sorted(target.glob("*.json"))
        if not trace_files:
            logger.warning("No trace JSON files found in %s", target)
            return 0
        results = [generate_from_file(f, output_dir) for f in trace_files]
        generated = sum(results)
        logger.info(
            "Generated %d/%d scripts from %s",
            generated,
            len(trace_files),
            target,
        )
        return 0 if generated > 0 else 1
    elif target.is_file():
        success = generate_from_file(target, output_dir)
        return 0 if success else 1
    else:
        logger.error("Path does not exist: %s", target)
        return 2


if __name__ == "__main__":
    sys.exit(main())
