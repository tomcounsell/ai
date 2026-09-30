"""Check that each active project repo has a '## Running' section in its README.

Developers adding a new project to projects.json should document how to start
the dev server so tools like /do-pr-review can find the right command instead
of guessing. This check warns (never fails) so it surfaces drift without
blocking updates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from config.machine import get_machine_name

REQUIRED_HEADING = "## Running"
# Regex: heading must be at the start of a line (with optional trailing whitespace)
_HEADING_RE = re.compile(r"^##\s+Running\s*$", re.MULTILINE | re.IGNORECASE)

README_CANDIDATES = ["README.md", "README.rst", "README.txt", "README"]

_EXAMPLE_BLOCK = """\
  ## Running

  ```bash
  # Start the development server — replace with the actual command for this repo
  # Django:  python manage.py runserver
  # Node:    npm run dev
  # Go:      go run .
  # FastAPI: uvicorn app.main:app --reload
  ```"""


@dataclass
class ProjectReadmeStatus:
    """Status of a single project's README check."""

    project_key: str
    working_dir: Path
    readme_path: Path | None
    has_running_section: bool
    missing_readme: bool = False


@dataclass
class ReadmeCheckResult:
    """Aggregated result of README checks across all active projects."""

    ok: bool = True
    checked: int = 0
    missing_section: list[ProjectReadmeStatus] = field(default_factory=list)
    missing_readme: list[ProjectReadmeStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_dir(working_directory: str) -> Path:
    return Path(working_directory.replace("~", str(Path.home()))).expanduser()


def _find_readme(repo_dir: Path) -> Path | None:
    for name in README_CANDIDATES:
        candidate = repo_dir / name
        if candidate.is_file():
            return candidate
    return None


def _has_running_section(readme: Path) -> bool:
    try:
        content = readme.read_text(encoding="utf-8", errors="replace")
        return bool(_HEADING_RE.search(content))
    except OSError:
        return False


def check_project_readmes(project_dir: Path) -> ReadmeCheckResult:
    """Check all projects owned by this machine for a '## Running' README section.

    Loads config/projects.json (the local real-file copy), filters to projects
    whose 'machine' matches the current ComputerName, then checks each repo's
    README for the required heading.
    """
    import json

    result = ReadmeCheckResult()

    projects_json = project_dir / "config" / "projects.json"
    if not projects_json.is_file():
        result.warnings.append("config/projects.json not found — skipping README check")
        return result

    try:
        data = json.loads(projects_json.read_text())
    except (json.JSONDecodeError, OSError) as e:
        result.warnings.append(f"README check skipped: could not read projects.json: {e}")
        return result

    machine_name = get_machine_name()
    if not machine_name:
        result.warnings.append("README check skipped: could not determine ComputerName")
        return result

    projects = data.get("projects", {})
    for key, val in projects.items():
        if not isinstance(val, dict):
            continue
        if val.get("machine") != machine_name:
            continue

        working_directory = val.get("working_directory", "")
        if not working_directory:
            continue

        repo_dir = _resolve_dir(working_directory)
        if not repo_dir.is_dir():
            continue

        result.checked += 1
        readme = _find_readme(repo_dir)

        if readme is None:
            status = ProjectReadmeStatus(
                project_key=key,
                working_dir=repo_dir,
                readme_path=None,
                has_running_section=False,
                missing_readme=True,
            )
            result.missing_readme.append(status)
            result.ok = False
            result.warnings.append(
                f"[{key}] No README found in {repo_dir} — "
                f"add a README.md with a '{REQUIRED_HEADING}' section"
            )
            continue

        if not _has_running_section(readme):
            status = ProjectReadmeStatus(
                project_key=key,
                working_dir=repo_dir,
                readme_path=readme,
                has_running_section=False,
            )
            result.missing_section.append(status)
            result.ok = False
            result.warnings.append(
                f"[{key}] {readme} is missing a '{REQUIRED_HEADING}' section\n"
                f"  Add the following to {readme.name}:\n"
                f"{_EXAMPLE_BLOCK}"
            )

    return result
