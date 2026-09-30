"""Per-category Tier 2 tool whitelist — the structural escalation gate's data.

This module is the single source of truth for which Cuttlefish ``manage.py``
verbs each lane may call. It is deliberately dependency-free (no Anthropic SDK,
no Ollama) so both ``gate.py`` and ``agents.py`` can import it cheaply.

The structural escalation gate is enforced by ABSENCE: a category mapped to an
empty list has no callable tool, so the gate forces ``escalate`` and the Tier 2
action agent is built with an empty Anthropic ``tools=[]`` array — the model
literally cannot emit a mutating call.

Phase 1 (shadow-mode) and Phase 2 (read-only auto) only ever execute the
**read-only** verbs below (``customer show``, ``customer checkout-url``). The
``mutating`` verbs (onboard, configure, episode provision) are listed so the
whitelist is complete, but they are gated behind ``auto_mutations=True``
(Phase 3) in ``agents.py`` — a read-only deployment filters them out.

Each tool is a dict in Anthropic tool-schema shape:
    {"name": <verb>, "description": ..., "input_schema": {...}, "_mutating": bool}

The ``_mutating`` flag is a private marker consumed by ``agents.py`` to filter
the array by phase; it is stripped before the array is sent to the API.

NOTE: ``customer note`` (audit) is NOT in any lane whitelist — it is always
written by the handler directly, never chosen by the action agent.
"""

from __future__ import annotations

from .schema import Category

# Reusable input schema: every cuttlefish verb is scoped per-account by --email,
# but the action agent never supplies the email (the handler injects the trusted
# resolved customer_id). The agent only chooses the verb and any non-account args.
_NO_ARGS_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "required": [],
}


def _tool(name: str, description: str, *, mutating: bool, input_schema: dict | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema or _NO_ARGS_SCHEMA,
        "_mutating": mutating,
    }


# Per-category whitelist. ESCALATE-only lanes (raise_to_human, and any lane with
# no safe verb) map to an empty list — the structural gate.
TOOLS: dict[Category, list[dict]] = {
    Category.MANAGE_PODCAST: [
        _tool(
            "customer_show",
            "Look up the customer's podcast/show status and configuration "
            "(read-only). Maps to `manage.py customer show --json`.",
            mutating=False,
        ),
        _tool(
            "customer_configure",
            "Change podcast cadence/length/style/title/description "
            "(mutating). Maps to `manage.py customer configure --json`.",
            mutating=True,
        ),
        _tool(
            "customer_onboard",
            "Onboard / create a new show for the customer (mutating). "
            "Maps to `manage.py customer onboard --json`.",
            mutating=True,
        ),
    ],
    Category.MANAGE_EPISODE: [
        _tool(
            "customer_show",
            "Look up episode/show status for the customer (read-only). "
            "Maps to `manage.py customer show --json`.",
            mutating=False,
        ),
        _tool(
            "episode_provision",
            "Provision a new episode for the customer's podcast (mutating). "
            "Maps to `manage.py episode provision --json`.",
            mutating=True,
        ),
    ],
    Category.OTHER_CUSTOMER_SERVICE: [
        _tool(
            "customer_show",
            "Look up subscription/account status (read-only). "
            "Maps to `manage.py customer show --json`.",
            mutating=False,
        ),
        _tool(
            "customer_checkout_url",
            "Generate an upgrade/checkout link for the customer (read-only — "
            "produces a URL, moves no money). Maps to "
            "`manage.py customer checkout-url --json`.",
            mutating=False,
        ),
    ],
    # raise_to_human has NO callable tool by construction — refund, invoice,
    # takedown, and all human-only operations live here with an empty whitelist.
    Category.RAISE_TO_HUMAN: [],
}

# Map from the agent-facing tool name to the cuttlefish manage.py argv tail.
# The handler/cuttlefish wrapper prepends the venv python + manage.py and
# appends --email <customer_id> --json. argv-form only, never shell.
VERB_ARGV: dict[str, list[str]] = {
    "customer_show": ["customer", "show"],
    "customer_checkout_url": ["customer", "checkout-url"],
    "customer_configure": ["customer", "configure"],
    "customer_onboard": ["customer", "onboard"],
    "episode_provision": ["episode", "provision"],
}


def category_has_tools(category: Category) -> bool:
    """True if the category has a non-empty tool whitelist (structural gate).

    Dependency-free so ``gate.py`` can call it without importing the SDK.
    A category with no tools can never auto-handle — the gate escalates.
    """
    return bool(TOOLS.get(category))


def tools_for_category(category: Category, *, allow_mutations: bool) -> list[dict]:
    """Return the API-shaped tool array for a category, filtered by phase.

    When ``allow_mutations`` is False (Phase 1/2), mutating verbs are stripped,
    leaving only read-only tools. The private ``_mutating`` marker is removed
    from each returned dict so the array is valid Anthropic tool-schema.

    A category whose filtered array is empty yields ``[]`` — the action agent
    built from it cannot emit any tool call and escalates by construction.
    """
    out: list[dict] = []
    for t in TOOLS.get(category, []):
        if t["_mutating"] and not allow_mutations:
            continue
        out.append(
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
        )
    return out
