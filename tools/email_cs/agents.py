"""Tier 2 action agent — per-category, structurally-gated tool selection.

Mirrors ``tools/classifier.py`` (Anthropic ``MODEL_FAST`` = Haiku, async via the
shared ``anthropic_slot`` concurrency guard) with one critical addition: the
agent is built per-category with ONLY that category's whitelisted tools in the
request's ``tools=[]`` array, and ``tool_choice={"type": "any"}`` forces a tool
call. This is the *structural escalation gate*:

- A category with an empty (phase-filtered) whitelist gets an empty ``tools``
  array. The agent cannot emit a mutating call by construction, so this function
  returns ``escalate`` WITHOUT an API call.
- If the model somehow names a tool not in the whitelist (it cannot, but we
  defend anyway), the result is ``draft_for_human`` — never an unguarded action.
- Any Anthropic API exception yields ``draft_for_human`` (never auto).

Forced tool use is incompatible with extended thinking; Tier 2 uses Haiku
without thinking, so the two are never combined.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.anthropic_client import anthropic_slot
from config.models import MODEL_FAST

from .schema import Category, Disposition, Triage
from .tools import VERB_ARGV, tools_for_category

logger = logging.getLogger(__name__)

_MAX_TOKENS = 512

_ACTION_SYSTEM = (
    "You are a customer-service action agent for a personalized podcast service. "
    "You have been given a triaged customer email and a set of safe tools. "
    "Choose exactly one tool that resolves the customer's request. You do not "
    "have access to the customer's account identifier — it is injected by the "
    "system after you choose. If none of the tools can safely resolve the "
    "request, do not invent one."
)


@dataclass
class ActionResult:
    """Outcome of a Tier 2 action-agent run.

    ``disposition`` is AUTO (a valid whitelisted tool was chosen), DRAFT (the
    agent picked an invalid tool, returned no tool, or the API errored), or
    ESCALATE (empty whitelist — no API call was made).

    On AUTO, ``tool_name`` is the chosen agent-facing tool name, ``verb_argv``
    is the cuttlefish ``manage.py`` argv tail (e.g. ``["customer", "show"]``),
    and ``tool_args`` carries any non-account arguments the agent supplied.
    """

    disposition: Disposition
    tool_name: str | None = None
    verb_argv: list[str] = field(default_factory=list)
    tool_args: dict = field(default_factory=dict)
    reason: str = ""


async def run_action_agent(
    category: Category,
    triage: Triage,
    email: dict,
    *,
    allow_mutations: bool = False,
) -> ActionResult:
    """Run the per-category Tier 2 action agent.

    Args:
        category: The triaged lane (drives the tool whitelist).
        triage: The Tier 1 verdict (for the prompt / reason context).
        email: Dict with at least ``subject`` and ``body`` keys.
        allow_mutations: Phase-3 flag. When False (Phase 1/2), mutating verbs
            are filtered out of the whitelist before the call.

    Returns:
        An ``ActionResult``. Empty whitelist -> ESCALATE (no API call).
        Invalid/absent tool or API error -> DRAFT. Valid tool -> AUTO.
    """
    tools = tools_for_category(category, allow_mutations=allow_mutations)

    # Structural gate: empty whitelist -> escalate without an API call.
    if not tools:
        logger.info(f"[email_cs.agents] empty whitelist for {category.value} -> escalate")
        return ActionResult(
            disposition=Disposition.ESCALATE,
            reason=f"no safe tool for {category.value}",
        )

    valid_names = {t["name"] for t in tools}
    prompt = (
        f"Customer email subject: {(email.get('subject') or '').strip()[:500]}\n\n"
        f"Customer email body:\n{(email.get('body') or '').strip()[:2000]}\n\n"
        f"Tier 1 classified this as: {category.value} "
        f"(reason: {triage.reason or 'n/a'}).\n"
        "Choose the single best tool to resolve this request."
    )

    try:
        async with anthropic_slot() as client:
            response = await client.messages.create(
                model=MODEL_FAST,
                max_tokens=_MAX_TOKENS,
                system=_ACTION_SYSTEM,
                tools=tools,
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": prompt}],
            )
    except Exception as e:
        # API failure must never auto-handle — draft for a human instead.
        logger.warning(f"[email_cs.agents] Anthropic call failed for {category.value}: {e}")
        return ActionResult(
            disposition=Disposition.DRAFT,
            reason=f"tier2 api failure: {e}",
        )

    tool_use = _first_tool_use(response)
    if tool_use is None:
        logger.warning(f"[email_cs.agents] no tool_use block for {category.value} -> draft")
        return ActionResult(
            disposition=Disposition.DRAFT,
            reason="tier2 agent returned no tool call",
        )

    tool_name = tool_use.get("name", "")
    # Defensive: a tool name outside the whitelist yields draft, never an action.
    if tool_name not in valid_names or tool_name not in VERB_ARGV:
        logger.warning(
            f"[email_cs.agents] invalid/unknown tool {tool_name!r} for {category.value} -> draft"
        )
        return ActionResult(
            disposition=Disposition.DRAFT,
            tool_name=tool_name or None,
            reason=f"tier2 chose invalid tool: {tool_name!r}",
        )

    tool_args = tool_use.get("input") or {}
    logger.info(f"[email_cs.agents] {category.value} -> tool {tool_name!r}")
    return ActionResult(
        disposition=Disposition.AUTO,
        tool_name=tool_name,
        verb_argv=list(VERB_ARGV[tool_name]),
        tool_args=dict(tool_args) if isinstance(tool_args, dict) else {},
        reason=triage.reason,
    )


def _first_tool_use(response) -> dict | None:
    """Extract the first ``tool_use`` content block as a plain dict.

    Returns ``{"name": str, "input": dict}`` or ``None`` if the response carries
    no tool_use block. Tolerant of both SDK objects and dict-shaped blocks so
    tests can stub a lightweight response.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    for block in content or []:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype != "tool_use":
            continue
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        binput = getattr(block, "input", None)
        if binput is None and isinstance(block, dict):
            binput = block.get("input")
        return {"name": name or "", "input": binput or {}}
    return None
