#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""
Validate Claude Code configuration files conform to standards.

Checks:
1. Commands: proper markdown structure, optional frontmatter
2. Skills: SKILL.md with required frontmatter (name, description)
3. Agents: proper frontmatter with name, description
4. Validators: can be executed standalone

Exit codes:
- 0: All validations passed
- 1: Validation errors found
- 2: Critical error (missing directories)

Usage:
  uv run validate_claude_config.py                    # Check all
  uv run validate_claude_config.py --type commands    # Check only commands
  uv run validate_claude_config.py --fix              # Auto-fix simple issues
  uv run validate_claude_config.py --verbose          # Show all checks
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Standard paths relative to project root
CLAUDE_DIR = ".claude"
COMMANDS_DIR = f"{CLAUDE_DIR}/commands"
SKILLS_DIR = f"{CLAUDE_DIR}/skills"
AGENTS_DIR = f"{CLAUDE_DIR}/agents"
HOOKS_DIR = f"{CLAUDE_DIR}/hooks"
VALIDATORS_DIR = f"{HOOKS_DIR}/validators"


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    path: str
    check: str
    passed: bool
    message: str
    fixable: bool = False
    fix_hint: str = ""


@dataclass
class ValidationReport:
    """Complete validation report."""

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if not r.passed]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.passed and "warning" in r.message.lower()]

    def add(self, result: ValidationResult):
        self.results.append(result)

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        return f"Checked {total} items: {passed} passed, {failed} failed"


def extract_frontmatter(content: str) -> tuple[dict | None, str]:
    """Extract YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return None, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content

    try:
        import yaml

        frontmatter = yaml.safe_load(parts[1])
        body = parts[2].strip()
        return frontmatter, body
    except Exception:
        return None, content


def validate_command(filepath: Path) -> list[ValidationResult]:
    """Validate a command file."""
    results = []
    rel_path = str(filepath)

    if not filepath.exists():
        results.append(
            ValidationResult(rel_path, "exists", False, "File does not exist")
        )
        return results

    content = filepath.read_text(encoding="utf-8")
    frontmatter, body = extract_frontmatter(content)

    # Check: Has content
    if len(body.strip()) < 50:
        results.append(
            ValidationResult(
                rel_path,
                "content",
                False,
                "Command has insufficient content (< 50 chars)",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "content", True, "Has substantial content")
        )

    # Check: Has a title (# heading)
    if not re.search(r"^#\s+\w+", body, re.MULTILINE):
        results.append(
            ValidationResult(
                rel_path,
                "title",
                False,
                "Missing H1 title (# Title)",
                fixable=True,
                fix_hint="Add a title with # at the start",
            )
        )
    else:
        results.append(ValidationResult(rel_path, "title", True, "Has H1 title"))

    # Check: Optional frontmatter is valid if present
    if frontmatter:
        valid_keys = {
            "description",
            "argument-hint",
            "model",
            "disallowed-tools",
            "allowed-tools",
            "hooks",
        }
        unknown_keys = set(frontmatter.keys()) - valid_keys
        if unknown_keys:
            results.append(
                ValidationResult(
                    rel_path,
                    "frontmatter-keys",
                    False,
                    f"Unknown frontmatter keys: {unknown_keys}",
                )
            )
        else:
            results.append(
                ValidationResult(
                    rel_path, "frontmatter-keys", True, "Valid frontmatter keys"
                )
            )

        # Validate model if specified
        if "model" in frontmatter:
            valid_models = {"sonnet", "opus", "haiku"}
            if frontmatter["model"] not in valid_models:
                results.append(
                    ValidationResult(
                        rel_path,
                        "model",
                        False,
                        f"Invalid model '{frontmatter['model']}', must be one of {valid_models}",
                    )
                )

    return results


def validate_skill(skill_dir: Path) -> list[ValidationResult]:
    """Validate a skill directory."""
    results = []
    rel_path = str(skill_dir)

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        results.append(
            ValidationResult(
                rel_path,
                "skill-file",
                False,
                "Missing SKILL.md file",
                fixable=False,
                fix_hint="Create SKILL.md with name and description frontmatter",
            )
        )
        return results

    content = skill_file.read_text(encoding="utf-8")
    frontmatter, body = extract_frontmatter(content)

    # Check: Has frontmatter
    if not frontmatter:
        results.append(
            ValidationResult(
                rel_path,
                "frontmatter",
                False,
                "Missing YAML frontmatter (---)",
                fixable=True,
            )
        )
        return results

    results.append(
        ValidationResult(rel_path, "frontmatter", True, "Has YAML frontmatter")
    )

    # Check: Required fields
    required_fields = ["name", "description"]
    for field_name in required_fields:
        if field_name not in frontmatter:
            results.append(
                ValidationResult(
                    rel_path,
                    f"field-{field_name}",
                    False,
                    f"Missing required field: {field_name}",
                    fixable=True,
                )
            )
        else:
            results.append(
                ValidationResult(
                    rel_path, f"field-{field_name}", True, f"Has {field_name} field"
                )
            )

    # Check: name matches directory
    if "name" in frontmatter:
        if frontmatter["name"] != skill_dir.name:
            results.append(
                ValidationResult(
                    rel_path,
                    "name-match",
                    False,
                    f"Skill name '{frontmatter['name']}' doesn't match "
                    f"directory '{skill_dir.name}'",
                    fixable=True,
                )
            )
        else:
            results.append(
                ValidationResult(rel_path, "name-match", True, "Name matches directory")
            )

    # Check: description is useful
    if "description" in frontmatter:
        desc = frontmatter["description"]
        if len(desc) < 20:
            results.append(
                ValidationResult(
                    rel_path,
                    "description-quality",
                    False,
                    "Description too short (< 20 chars)",
                )
            )
        elif "use when" not in desc.lower() and "use for" not in desc.lower():
            results.append(
                ValidationResult(
                    rel_path,
                    "description-quality",
                    True,
                    "Warning: Description should explain when to use this skill",
                )
            )
        else:
            results.append(
                ValidationResult(
                    rel_path, "description-quality", True, "Good description"
                )
            )

    # Check: Has content
    if len(body.strip()) < 50:
        results.append(
            ValidationResult(
                rel_path,
                "content",
                False,
                "Skill has insufficient content (< 50 chars)",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "content", True, "Has substantial content")
        )

    return results


def validate_agent(filepath: Path) -> list[ValidationResult]:
    """Validate an agent definition file."""
    results = []
    rel_path = str(filepath)

    if filepath.name == "README.md":
        # Skip README
        return results

    if not filepath.exists():
        results.append(
            ValidationResult(rel_path, "exists", False, "File does not exist")
        )
        return results

    content = filepath.read_text(encoding="utf-8")
    frontmatter, body = extract_frontmatter(content)

    # Check: Has frontmatter
    if not frontmatter:
        results.append(
            ValidationResult(
                rel_path,
                "frontmatter",
                False,
                "Missing YAML frontmatter (---)",
                fixable=True,
            )
        )
        return results

    results.append(
        ValidationResult(rel_path, "frontmatter", True, "Has YAML frontmatter")
    )

    # Check: Required fields
    required_fields = ["name", "description"]
    for field_name in required_fields:
        if field_name not in frontmatter:
            results.append(
                ValidationResult(
                    rel_path,
                    f"field-{field_name}",
                    False,
                    f"Missing required field: {field_name}",
                    fixable=True,
                )
            )
        else:
            results.append(
                ValidationResult(
                    rel_path, f"field-{field_name}", True, f"Has {field_name} field"
                )
            )

    # Check: name matches filename (without .md)
    if "name" in frontmatter:
        expected_name = filepath.stem
        if frontmatter["name"] != expected_name:
            results.append(
                ValidationResult(
                    rel_path,
                    "name-match",
                    False,
                    f"Agent name '{frontmatter['name']}' doesn't match filename '{expected_name}'",
                    fixable=True,
                )
            )
        else:
            results.append(
                ValidationResult(rel_path, "name-match", True, "Name matches filename")
            )

    # Check: model is valid if specified
    if "model" in frontmatter:
        valid_models = {"sonnet", "opus", "haiku"}
        if frontmatter["model"] not in valid_models:
            results.append(
                ValidationResult(
                    rel_path,
                    "model",
                    False,
                    f"Invalid model '{frontmatter['model']}', must be one of {valid_models}",
                )
            )

    return results


def validate_validator(filepath: Path) -> list[ValidationResult]:
    """Validate a validator script."""
    results = []
    rel_path = str(filepath)

    if not filepath.exists():
        results.append(
            ValidationResult(rel_path, "exists", False, "File does not exist")
        )
        return results

    content = filepath.read_text(encoding="utf-8")

    # Check: Has shebang for uv run
    if not content.startswith("#!/usr/bin/env"):
        results.append(
            ValidationResult(
                rel_path,
                "shebang",
                False,
                "Missing uv run shebang (#!/usr/bin/env -S uv run --script)",
                fixable=True,
            )
        )
    elif "uv run" not in content.split("\n")[0]:
        results.append(
            ValidationResult(
                rel_path,
                "shebang",
                False,
                "Shebang should use 'uv run --script' for dependency management",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "shebang", True, "Has uv run shebang")
        )

    # Check: Has inline script metadata
    if "# /// script" not in content:
        results.append(
            ValidationResult(
                rel_path,
                "script-metadata",
                False,
                "Missing inline script metadata (# /// script)",
                fixable=True,
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "script-metadata", True, "Has script metadata")
        )

    # Check: Has docstring
    if '"""' not in content[:500]:
        results.append(
            ValidationResult(
                rel_path,
                "docstring",
                False,
                "Missing module docstring",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "docstring", True, "Has module docstring")
        )

    # Check: Has main function
    if "def main(" not in content:
        results.append(
            ValidationResult(
                rel_path,
                "main-function",
                False,
                "Missing main() function",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "main-function", True, "Has main() function")
        )

    # Check: Has proper exit codes
    if "sys.exit(0)" not in content or "sys.exit(2)" not in content:
        results.append(
            ValidationResult(
                rel_path,
                "exit-codes",
                False,
                "Should use sys.exit(0) for success and sys.exit(2) for failure",
            )
        )
    else:
        results.append(
            ValidationResult(rel_path, "exit-codes", True, "Has proper exit codes")
        )

    # Check: Can execute (syntax check)
    try:
        result = subprocess.run(
            ["python", "-m", "py_compile", str(filepath)],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            results.append(
                ValidationResult(
                    rel_path,
                    "syntax",
                    False,
                    f"Syntax error: {result.stderr.decode()}",
                )
            )
        else:
            results.append(
                ValidationResult(rel_path, "syntax", True, "Valid Python syntax")
            )
    except Exception as e:
        results.append(
            ValidationResult(rel_path, "syntax", False, f"Could not check syntax: {e}")
        )

    return results


def run_validation(
    project_dir: Path,
    check_types: list[str] | None = None,
    verbose: bool = False,
) -> ValidationReport:
    """Run all validations and return report."""
    report = ValidationReport()
    all_types = ["commands", "skills", "agents", "validators"]
    types_to_check = check_types or all_types

    # Validate commands
    if "commands" in types_to_check:
        commands_dir = project_dir / COMMANDS_DIR
        if commands_dir.exists():
            for cmd_file in sorted(commands_dir.glob("*.md")):
                results = validate_command(cmd_file)
                for r in results:
                    report.add(r)
        elif verbose:
            report.add(
                ValidationResult(
                    COMMANDS_DIR, "directory", False, "Commands directory not found"
                )
            )

    # Validate skills
    if "skills" in types_to_check:
        skills_dir = project_dir / SKILLS_DIR
        if skills_dir.exists():
            for skill_dir in sorted(skills_dir.iterdir()):
                if skill_dir.is_dir() and not skill_dir.name.startswith("."):
                    results = validate_skill(skill_dir)
                    for r in results:
                        report.add(r)
        elif verbose:
            report.add(
                ValidationResult(
                    SKILLS_DIR, "directory", False, "Skills directory not found"
                )
            )

    # Validate agents
    if "agents" in types_to_check:
        agents_dir = project_dir / AGENTS_DIR
        if agents_dir.exists():
            for agent_file in sorted(agents_dir.glob("*.md")):
                results = validate_agent(agent_file)
                for r in results:
                    report.add(r)
        elif verbose:
            report.add(
                ValidationResult(
                    AGENTS_DIR, "directory", False, "Agents directory not found"
                )
            )

    # Validate validators
    if "validators" in types_to_check:
        validators_dir = project_dir / VALIDATORS_DIR
        if validators_dir.exists():
            for validator_file in sorted(validators_dir.glob("*.py")):
                if validator_file.name.startswith("validate_"):
                    results = validate_validator(validator_file)
                    for r in results:
                        report.add(r)
        elif verbose:
            report.add(
                ValidationResult(
                    VALIDATORS_DIR,
                    "directory",
                    False,
                    "Validators directory not found",
                )
            )

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Validate Claude Code configuration files"
    )
    parser.add_argument(
        "-t",
        "--type",
        choices=["commands", "skills", "agents", "validators"],
        action="append",
        dest="types",
        help="Type(s) to check (can be repeated)",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Project directory (default: current)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show all checks, not just errors"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Attempt to fix simple issues (not implemented)",
    )
    args = parser.parse_args()

    # Consume stdin if provided (for hook usage)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    report = run_validation(args.directory, args.types, args.verbose)

    if args.json:
        output = {
            "passed": report.passed,
            "summary": report.summary(),
            "errors": [
                {"path": r.path, "check": r.check, "message": r.message}
                for r in report.errors
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        if args.verbose:
            for result in report.results:
                status = "PASS" if result.passed else "FAIL"
                print(f"[{status}] {result.path}: {result.check} - {result.message}")
            print()

        if report.errors:
            print("ERRORS:")
            for result in report.errors:
                print(f"  {result.path}")
                print(f"    {result.check}: {result.message}")
                if result.fix_hint:
                    print(f"    Fix: {result.fix_hint}")
            print()

        print(report.summary())

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
