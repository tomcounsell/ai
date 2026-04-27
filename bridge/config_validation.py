"""Static validation of projects.json for the Telegram and email bridges.

Enforces the single-machine-ownership invariant: every bridge-contact
identifier (DM contact id, Telegram group name, email contact, email
domain) must resolve to exactly one machine across the whole config.
Two machines must never both initiate work on the same incoming bridge
message.

Validation runs over the full config (not the per-machine subset) so
misconfiguration is caught no matter which machine is loading. Invoked
by the update script as a green-light gate before service restart.
"""

from __future__ import annotations


class ConfigValidationError(RuntimeError):
    """Raised when projects.json fails validation. Update script blocks restart."""


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


def validate_telegram_groups(config: dict) -> None:
    """Enforce that every Telegram group name resolves to exactly one machine.

    Two projects on different machines declaring the same group would cause
    both bridges to race for incoming messages. Group names are
    case-insensitive (Telegram itself treats them as such for our purposes).
    """
    projects = config.get("projects", {})
    group_to_machines: dict[str, set[str]] = {}
    group_to_projects: dict[str, list[str]] = {}
    errors: list[str] = []

    for proj_key, proj_cfg in projects.items():
        if not isinstance(proj_cfg, dict):
            continue
        machine = proj_cfg.get("machine")
        groups = (proj_cfg.get("telegram") or {}).get("groups") or {}
        if groups and not machine:
            errors.append(
                f"project '{proj_key}' declares telegram.groups but has no 'machine' field"
            )
            continue
        for gname in groups:
            key = gname.strip().lower()
            group_to_machines.setdefault(key, set()).add(machine)
            group_to_projects.setdefault(key, []).append(proj_key)

    for key, machines in group_to_machines.items():
        if len(machines) > 1:
            errors.append(
                f"telegram group '{key}' is declared on multiple machines "
                f"{sorted(machines)} via projects: {group_to_projects[key]}"
            )

    if errors:
        raise ConfigValidationError(
            "projects.json telegram.groups failed validation:\n  - " + "\n  - ".join(errors)
        )


def validate_email_routing(config: dict) -> None:
    """Enforce that every email contact and domain resolves to exactly one machine.

    Two projects on different machines claiming the same explicit contact
    address — or one claiming a domain wildcard that covers the other's
    explicit contact — would cause both email bridges to dispatch the same
    incoming message. We catch:

    1. Same explicit contact address declared on two machines.
    2. Same domain declared on two machines.
    3. An explicit contact on one machine whose domain is claimed as a
       wildcard by a project on a different machine (the domain wildcard
       would steal the message).

    Matching is case-insensitive.
    """
    projects = config.get("projects", {})
    contact_to_machines: dict[str, set[str]] = {}
    domain_to_machines: dict[str, set[str]] = {}
    contact_to_projects: dict[str, list[str]] = {}
    domain_to_projects: dict[str, list[str]] = {}
    errors: list[str] = []

    for proj_key, proj_cfg in projects.items():
        if not isinstance(proj_cfg, dict):
            continue
        machine = proj_cfg.get("machine")
        email_cfg = proj_cfg.get("email") or {}
        contacts = email_cfg.get("contacts") or []
        domains = email_cfg.get("domains") or []
        if (contacts or domains) and not machine:
            errors.append(f"project '{proj_key}' declares email routing but has no 'machine' field")
            continue
        for c in contacts:
            key = str(c).strip().lower()
            contact_to_machines.setdefault(key, set()).add(machine)
            contact_to_projects.setdefault(key, []).append(proj_key)
        for d in domains:
            key = str(d).strip().lower().lstrip("@").lstrip("*.")
            domain_to_machines.setdefault(key, set()).add(machine)
            domain_to_projects.setdefault(key, []).append(proj_key)

    for c, machines in contact_to_machines.items():
        if len(machines) > 1:
            errors.append(
                f"email contact '{c}' is declared on multiple machines "
                f"{sorted(machines)} via projects: {contact_to_projects[c]}"
            )
    for d, machines in domain_to_machines.items():
        if len(machines) > 1:
            errors.append(
                f"email domain '{d}' is declared on multiple machines "
                f"{sorted(machines)} via projects: {domain_to_projects[d]}"
            )

    # Cross-shape: an explicit contact on machine A vs. a domain wildcard on
    # machine B that would also match it. The domain match would race the
    # contact match.
    for contact, c_machines in contact_to_machines.items():
        if "@" not in contact:
            continue
        contact_domain = contact.rsplit("@", 1)[1]
        d_machines = domain_to_machines.get(contact_domain)
        if not d_machines:
            continue
        all_machines = c_machines | d_machines
        if len(all_machines) > 1:
            errors.append(
                f"email contact '{contact}' (machines {sorted(c_machines)}) "
                f"overlaps with domain '{contact_domain}' wildcard "
                f"(machines {sorted(d_machines)}); both bridges would dispatch"
            )

    if errors:
        raise ConfigValidationError(
            "projects.json email routing failed validation:\n  - " + "\n  - ".join(errors)
        )


def validate_projects_config(config: dict) -> None:
    """Run the full bridge-contact ownership validation suite.

    Aggregates errors from every shape (DM whitelist, Telegram groups,
    email routing) into a single ConfigValidationError so the operator
    sees every problem at once instead of fixing them one round-trip at
    a time.
    """
    errors: list[str] = []
    for fn in (validate_dm_whitelist, validate_telegram_groups, validate_email_routing):
        try:
            fn(config)
        except ConfigValidationError as e:
            errors.append(str(e))
    if errors:
        raise ConfigValidationError("\n\n".join(errors))
