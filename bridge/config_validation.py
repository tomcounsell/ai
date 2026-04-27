"""Static validation of projects.json for the Telegram bridge.

Runs at bridge startup over the full config (not the per-machine subset)
so misconfiguration is caught on every machine, regardless of which
projects that machine actually owns.
"""

from __future__ import annotations


class ConfigValidationError(RuntimeError):
    """Raised when projects.json fails validation. The bridge refuses to start."""


def validate_dm_whitelist(config: dict) -> None:
    """Enforce that every DM whitelist contact resolves to exactly one machine.

    Each whitelist entry must declare a ``project`` field that exists in
    ``projects`` and whose project declares a ``machine`` field. A single
    Telegram contact id appearing in multiple entries that resolve to
    different machines would create ambiguous routing — fail hard.

    Raises:
        ConfigValidationError: if any rule is violated.
    """
    whitelist = config.get("dms", {}).get("whitelist", [])
    projects = config.get("projects", {})
    contact_to_machines: dict[int, set[str]] = {}
    contact_to_entries: dict[int, list[dict]] = {}
    errors: list[str] = []

    for entry in whitelist:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        try:
            contact_id = int(entry["id"])
        except (TypeError, ValueError):
            errors.append(f"whitelist entry has non-integer id: {entry.get('id')!r}")
            continue

        proj_key = entry.get("project")
        if not proj_key:
            errors.append(
                f"whitelist entry id={contact_id} ({entry.get('name', '?')}) has no 'project' field"
            )
            continue

        proj_cfg = projects.get(proj_key)
        if not isinstance(proj_cfg, dict):
            errors.append(
                f"whitelist entry id={contact_id} references unknown project '{proj_key}'"
            )
            continue

        machine = proj_cfg.get("machine")
        if not machine:
            errors.append(
                f"project '{proj_key}' (referenced by whitelist id={contact_id}) "
                f"has no 'machine' field"
            )
            continue

        contact_to_machines.setdefault(contact_id, set()).add(machine)
        contact_to_entries.setdefault(contact_id, []).append(entry)

    for contact_id, machines in contact_to_machines.items():
        if len(machines) > 1:
            entry_summary = ", ".join(
                f"{e.get('name', '?')}->{e.get('project')}" for e in contact_to_entries[contact_id]
            )
            errors.append(
                f"contact id={contact_id} maps to multiple machines "
                f"{sorted(machines)} via entries: {entry_summary}"
            )

    if errors:
        raise ConfigValidationError(
            "projects.json dms.whitelist failed validation:\n  - " + "\n  - ".join(errors)
        )
