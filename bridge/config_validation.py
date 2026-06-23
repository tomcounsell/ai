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


def validate_telegram_bots(config: dict) -> None:
    """Validate the registered-bot registry (``telegram.bots[]``, issue #1574).

    The bot registry is the home of the deterministic loop-guard: a registered
    bot peer is recorded to history but never spawns a session. Two invariants
    must hold or the guard's safety is undermined:

    1. **Single-machine ownership.** A given bot id must resolve to exactly one
       machine, like every other bridge-contact identifier. Two machines both
       recording (and awaiting on) the same bot is ambiguous.

    2. **Mutual exclusion** with ``dms.whitelist[].id``. If a bot id ALSO appears
       in the DM whitelist, ``find_project_for_dm`` would resolve a project for
       it on the spawn path and its no-reply_to replies would spawn runaway
       sessions — exactly the loop this feature prevents. (Telegram groups are
       declared by name, not numeric id, so there is no id-level overlap to
       check there; the spawn risk is the DM-whitelist path.)

    Each entry must be a dict with an integer ``id`` and live under a project
    that declares a ``machine``.

    Raises:
        ConfigValidationError: if any rule is violated.
    """
    projects = config.get("projects", {})
    bot_to_machines: dict[int, set[str]] = {}
    bot_to_projects: dict[int, list[str]] = {}
    errors: list[str] = []

    for proj_key, proj_cfg in projects.items():
        if not isinstance(proj_cfg, dict):
            continue
        machine = proj_cfg.get("machine")
        bots = (proj_cfg.get("telegram") or {}).get("bots") or []
        if bots and not machine:
            errors.append(f"project '{proj_key}' declares telegram.bots but has no 'machine' field")
            continue
        for entry in bots:
            if not isinstance(entry, dict) or "id" not in entry:
                errors.append(f"project '{proj_key}' telegram.bots entry missing 'id': {entry!r}")
                continue
            try:
                bot_id = int(entry["id"])
            except (TypeError, ValueError):
                errors.append(
                    f"project '{proj_key}' telegram.bots entry has non-integer id: "
                    f"{entry.get('id')!r}"
                )
                continue
            bot_to_machines.setdefault(bot_id, set()).add(machine)
            bot_to_projects.setdefault(bot_id, []).append(proj_key)

    for bot_id, machines in bot_to_machines.items():
        if len(machines) > 1:
            errors.append(
                f"bot id={bot_id} maps to multiple machines {sorted(machines)} "
                f"via projects: {bot_to_projects[bot_id]}"
            )

    # Mutual exclusion: a registered bot id must not also be a DM-whitelist id.
    whitelist_ids: set[int] = set()
    for entry in config.get("dms", {}).get("whitelist", []):
        if isinstance(entry, dict) and "id" in entry:
            try:
                whitelist_ids.add(int(entry["id"]))
            except (TypeError, ValueError):
                continue
    for bot_id in bot_to_machines:
        if bot_id in whitelist_ids:
            errors.append(
                f"bot id={bot_id} also appears in dms.whitelist — a registered bot "
                f"must never resolve a project on the spawn path (loop hazard #1574)"
            )

    if errors:
        raise ConfigValidationError(
            "projects.json telegram.bots failed validation:\n  - " + "\n  - ".join(errors)
        )


def _iter_registered_bot_ids(config: dict) -> list[tuple[int, str]]:
    """Yield ``(bot_id, project_key)`` for every well-formed registered bot.

    Skips structurally-invalid entries (missing/non-integer id) — those are the
    job of :func:`validate_telegram_bots`. This helper only enumerates the ids
    that the live-flag probe should resolve.
    """
    out: list[tuple[int, str]] = []
    for proj_key, proj_cfg in config.get("projects", {}).items():
        if not isinstance(proj_cfg, dict):
            continue
        for entry in (proj_cfg.get("telegram") or {}).get("bots") or []:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            try:
                out.append((int(entry["id"]), proj_key))
            except (TypeError, ValueError):
                continue
    return out


async def validate_bot_live_flags(config: dict, resolver) -> None:
    """Validate each registered bot id against the live Telegram ``User.bot`` flag.

    Acceptance criterion 4 of issue #1574 ("the bot registry validates each entry
    against the live ``User.bot`` flag and surfaces mismatches"). The deterministic
    loop-guard suppresses *every* inbound message from a registered bot id. If a
    swapped token or typo'd id points at a **human** account, that human's messages
    would be silently dropped — so we probe the live flag and fail loud.

    The probe is decoupled from a live Telethon client via an injectable
    ``resolver``: a coroutine ``resolver(bot_id: int) -> object`` returning the
    resolved Telegram entity (anything exposing a ``.bot`` attribute, as the bridge
    already reads via ``getattr(sender, "bot", False)``). Production passes a
    closure over ``client.get_entity``; tests pass a fake. This keeps the function
    runnable on machines with no Telegram session — the resolver is the only live
    dependency.

    Each registered bot id must resolve to an entity whose ``.bot`` flag is truthy.
    A non-bot (human) account, a missing/false ``.bot`` attribute, or a resolver
    that raises is collected as a mismatch; all mismatches are reported together.

    Args:
        config: The full projects.json config dict.
        resolver: ``async def resolver(bot_id: int) -> entity``. May raise to
            signal an unresolvable id.

    Raises:
        ConfigValidationError: if any registered bot id is not a live bot.
    """
    errors: list[str] = []
    seen: set[int] = set()
    for bot_id, proj_key in _iter_registered_bot_ids(config):
        if bot_id in seen:
            continue
        seen.add(bot_id)
        try:
            entity = await resolver(bot_id)
        except Exception as e:  # noqa: BLE001 — any resolver failure is a mismatch
            errors.append(
                f"bot id={bot_id} (project '{proj_key}') failed to resolve against Telegram: {e!r}"
            )
            continue
        if not bool(getattr(entity, "bot", False)):
            errors.append(
                f"bot id={bot_id} (project '{proj_key}') resolves to a NON-bot "
                f"(User.bot is false) — a swapped token or typo'd id pointing at a "
                f"human account would silently suppress that human's messages"
            )

    if errors:
        raise ConfigValidationError(
            "projects.json telegram.bots failed live User.bot validation:\n  - "
            + "\n  - ".join(errors)
        )


def validate_projects_config(config: dict) -> None:
    """Run the full bridge-contact ownership validation suite.

    Aggregates errors from every shape (DM whitelist, Telegram groups,
    email routing, bot registry) into a single ConfigValidationError so the
    operator sees every problem at once instead of fixing them one round-trip
    at a time.
    """
    errors: list[str] = []
    for fn in (
        validate_dm_whitelist,
        validate_telegram_groups,
        validate_email_routing,
        validate_telegram_bots,
    ):
        try:
            fn(config)
        except ConfigValidationError as e:
            errors.append(str(e))
    if errors:
        raise ConfigValidationError("\n\n".join(errors))
