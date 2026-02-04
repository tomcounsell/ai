"""Calendar integration for update system."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CalendarHookResult:
    """Result of calendar hook verification/setup."""
    configured: bool
    created: bool = False
    error: str | None = None


@dataclass
class CalendarMapping:
    """A single calendar mapping."""
    slug: str
    calendar_id: str
    calendar_name: str | None = None
    accessible: bool = True


@dataclass
class CalendarConfigResult:
    """Result of calendar config generation."""
    success: bool
    mappings: list[CalendarMapping] = field(default_factory=list)
    oauth_exists: bool = False
    api_connected: bool = False
    error: str | None = None


def get_global_settings_path() -> Path:
    """Get path to global Claude settings."""
    return Path.home() / ".claude" / "settings.json"


def get_hook_script_path(project_dir: Path) -> Path:
    """Get path to calendar hook script."""
    return project_dir / "scripts" / "calendar_prompt_hook.sh"


def verify_global_hook(project_dir: Path) -> CalendarHookResult:
    """Verify the global calendar hook is configured."""
    settings_path = get_global_settings_path()
    hook_script = get_hook_script_path(project_dir)

    if not settings_path.exists():
        return CalendarHookResult(
            configured=False,
            error="No global settings file",
        )

    try:
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})

        # Check UserPromptSubmit hook
        user_prompt_hooks = hooks.get("UserPromptSubmit", [])
        has_user_prompt = any(
            "calendar_prompt_hook" in str(h)
            for h in user_prompt_hooks
        )

        # Check Stop hook
        stop_hooks = hooks.get("Stop", [])
        has_stop = any(
            "calendar_prompt_hook" in str(h)
            for h in stop_hooks
        )

        return CalendarHookResult(
            configured=has_user_prompt and has_stop,
            error=None if (has_user_prompt and has_stop) else "Missing hooks",
        )
    except Exception as e:
        return CalendarHookResult(
            configured=False,
            error=str(e),
        )


def setup_global_hook(project_dir: Path) -> CalendarHookResult:
    """Set up the global calendar hook."""
    settings_path = get_global_settings_path()
    hook_script = get_hook_script_path(project_dir)
    hook_command = f"bash {hook_script}"

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Load existing settings or create new
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            # Backup
            backup_path = settings_path.with_suffix(".json.bak")
            backup_path.write_text(json.dumps(settings, indent=2))
        else:
            settings = {}

        # Ensure hooks dict
        if "hooks" not in settings:
            settings["hooks"] = {}

        # Add UserPromptSubmit hook
        if "UserPromptSubmit" not in settings["hooks"]:
            settings["hooks"]["UserPromptSubmit"] = []

        user_prompt_hooks = settings["hooks"]["UserPromptSubmit"]
        has_calendar = any("calendar_prompt_hook" in str(h) for h in user_prompt_hooks)

        if not has_calendar:
            user_prompt_hooks.append({
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": hook_command,
                    "timeout": 15,
                }],
            })

        # Add Stop hook
        if "Stop" not in settings["hooks"]:
            settings["hooks"]["Stop"] = []

        stop_hooks = settings["hooks"]["Stop"]
        has_stop_calendar = any("calendar_prompt_hook" in str(h) for h in stop_hooks)

        if not has_stop_calendar:
            if not stop_hooks:
                stop_hooks.append({"matcher": "", "hooks": []})
            stop_hooks[0]["hooks"].insert(0, {
                "type": "command",
                "command": hook_command,
                "timeout": 15,
            })

        # Write settings
        settings_path.write_text(json.dumps(settings, indent=2))

        return CalendarHookResult(configured=True, created=True)

    except Exception as e:
        return CalendarHookResult(
            configured=False,
            error=str(e),
        )


def ensure_global_hook(project_dir: Path) -> CalendarHookResult:
    """Verify or set up global calendar hook."""
    result = verify_global_hook(project_dir)

    if result.configured:
        return result

    return setup_global_hook(project_dir)


def generate_calendar_config(project_dir: Path) -> CalendarConfigResult:
    """Generate Google Calendar config by matching projects to calendars."""
    base_dir = Path.home() / "Desktop" / "claude_code"
    config_path = base_dir / "calendar_config.json"
    token_path = base_dir / "google_token.json"

    # Check OAuth token
    if not token_path.exists():
        return CalendarConfigResult(
            success=False,
            oauth_exists=False,
            error=f"No OAuth token at {token_path}. Run /setup to configure.",
        )

    try:
        # Import Google Workspace auth
        import sys
        sys.path.insert(0, str(project_dir))
        from tools.google_workspace.auth import get_service

        # Connect to Calendar API
        service = get_service("calendar", "v3")
        result = service.calendarList().list().execute()
    except Exception as e:
        return CalendarConfigResult(
            success=False,
            oauth_exists=True,
            api_connected=False,
            error=f"Calendar API auth failed: {e}",
        )

    # Build name->id map from Google Calendars
    gcal_by_name: dict[str, str] = {}
    for cal in result.get("items", []):
        gcal_by_name[cal["summary"]] = cal["id"]

    mappings: list[CalendarMapping] = []
    calendars: dict[str, str] = {}

    # Map 'dm' to primary
    calendars["dm"] = "primary"
    mappings.append(CalendarMapping(
        slug="dm",
        calendar_id="primary",
        calendar_name="Primary",
        accessible=True,
    ))

    # Map 'default' to 'Internal Projects'
    if "Internal Projects" in gcal_by_name:
        calendars["default"] = gcal_by_name["Internal Projects"]
        mappings.append(CalendarMapping(
            slug="default",
            calendar_id=gcal_by_name["Internal Projects"],
            calendar_name="Internal Projects",
            accessible=True,
        ))

    # Load projects config
    projects_path = project_dir / "config" / "projects.json"
    projects: dict = {}
    if projects_path.exists():
        projects = json.loads(projects_path.read_text()).get("projects", {})

    # Get active projects from env
    from dotenv import load_dotenv
    load_dotenv(project_dir / ".env")
    active_projects = [
        p.strip()
        for p in os.getenv("ACTIVE_PROJECTS", "").split(",")
        if p.strip()
    ]

    # Match each project to a calendar
    for project_key in active_projects:
        project = projects.get(project_key, {})
        groups = project.get("telegram", {}).get("groups", [])

        for group in groups:
            # Strip 'Dev: ' prefix to get calendar name
            cal_name = group.replace("Dev: ", "")

            if cal_name in gcal_by_name:
                calendars[project_key] = gcal_by_name[cal_name]
                mappings.append(CalendarMapping(
                    slug=project_key,
                    calendar_id=gcal_by_name[cal_name],
                    calendar_name=cal_name,
                    accessible=True,
                ))
                break

    # Verify accessibility of mapped calendars
    for mapping in mappings:
        if mapping.calendar_id == "primary":
            continue
        try:
            service.calendars().get(calendarId=mapping.calendar_id).execute()
        except Exception:
            mapping.accessible = False

    # Write config
    config = {"calendars": calendars}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    return CalendarConfigResult(
        success=True,
        mappings=mappings,
        oauth_exists=True,
        api_connected=True,
    )
