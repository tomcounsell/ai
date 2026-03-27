"""Data access layer for machine-specific project config."""

import json
import subprocess
from pathlib import Path


def get_machine_name() -> str:
    try:
        result = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return ""


def get_machine_projects() -> list[dict]:
    """Return rows for each Telegram group active on this machine.

    Each row has: group_name, persona, project_name, github.
    """
    config_path = Path("~/Desktop/Valor/projects.json").expanduser()
    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text())
    except Exception:
        return []

    machine = get_machine_name().lower()
    rows = []
    for project_key, project in config.get("projects", {}).items():
        if project.get("machine", "").lower() != machine:
            continue

        github = project.get("github", {})
        github_str = f"{github['org']}/{github['repo']}" if github else ""

        groups = project.get("telegram", {}).get("groups", {})
        for group_name, group_cfg in groups.items():
            rows.append(
                {
                    "group_name": group_name,
                    "persona": group_cfg.get("persona", ""),
                    "project_name": project.get("name", project_key),
                    "github": github_str,
                }
            )

    return rows
