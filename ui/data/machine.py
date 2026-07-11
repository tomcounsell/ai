"""Data access layer for machine-specific project config.

Machine-name and ownership resolution live in :mod:`config.machine` (the lowest
shared layer). This module keeps only the ui-specific ``get_machine_projects``
view (exploded per-Telegram-group rows), which borrows ``get_machine_name``
from there.
"""

import json
from pathlib import Path

from config.machine import get_machine_name


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

    from config.enums import PersonaType

    persona_order = {
        PersonaType.ENGINEER: 0,
        PersonaType.TEAMMATE: 1,
    }
    rows.sort(key=lambda r: (r["project_name"].lower(), persona_order.get(r["persona"], 99)))
    return rows
