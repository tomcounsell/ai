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
    try:
        from config.settings import vault

        config_path = vault.projects_path
    except Exception:
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

    from config.enums import PersonaType

    persona_order = {
        PersonaType.ENGINEER: 0,
        PersonaType.TEAMMATE: 1,
    }
    rows.sort(key=lambda r: (r["project_name"].lower(), persona_order.get(r["persona"], 99)))
    return rows


def get_machine_project_keys() -> list[str]:
    """Return the ``project_key``s owned by this machine (``projects.<key>.machine`` match).

    Used to scope machine-local Redis counter aggregation (e.g. the
    ``slot_reclaims`` self-heal counter, issue #1820) to the projects this
    worker actually serves — the same machine filter ``get_machine_projects()``
    already applies, but returning raw project_key strings instead of exploded
    per-Telegram-group rows.
    """
    config_path = Path("~/Desktop/Valor/projects.json").expanduser()
    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text())
    except Exception:
        return []

    machine = get_machine_name().lower()
    return [
        project_key
        for project_key, project in config.get("projects", {}).items()
        if project.get("machine", "").lower() == machine
    ]
