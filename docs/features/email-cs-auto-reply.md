# Email Customer-Service Auto-Reply Layer (Cuttlefish)

## Overview

A two-tier triage layer that classifies inbound Cuttlefish customer emails and
acts on them: auto-handled when there is a safe tool and high confidence,
drafted-for-human or escalated otherwise. It slots into
`bridge/email_bridge.py::_process_inbound_email()` after the
[customer resolver](customer-resolver.md) identifies the sender, and before the
fallback `AgentSession` spawn.

The destination is full auto, gated **structurally** so it ships safely in
incremental phases. The layer is **inert** for any project that does not declare
both a `customer_resolver` and an `email.customer_service` config block — for
every other project the bridge's existing behavior is unchanged.

Package: `tools/email_cs/`.

## The four lanes

Every inbound resolved-customer email is sorted into exactly one lane:

- **manage_podcast** — show/feed/account-level (create show, change
  title/description/art, cadence/length/style, pause/archive).
- **manage_episode** — single-episode (generate, regenerate, edit metadata,
  publish/schedule, status lookup).
- **other_customer_service** — account ops not tied to podcast/episode content
  (subscription status, upgrade/checkout, cancel, billing, how-to, bug reports).
- **raise_to_human** — a *signal*, not a topic (anger/churn/threats,
  legal/press/compliance, refunds/credits, identity mismatch, low confidence,
  VIP). Always escalates.

## Two tiers + the escalation gate

```
_process_inbound_email()  (bridge/email_bridge.py)
  customer_resolver -> customer_id (or drop)
  TIER 1  triage_local()   Ollama granite4.1:3b (OLLAMA_CLASSIFIER_MODEL), temp=0, tolerant JSON parse
       parse-fail / customer_id None / conf<0.75 / escalation-signal -> escalate
  GATE    decide()         the only function before a side effect
  TIER 2  run_action_agent()  Anthropic MODEL_FAST, tools=[whitelist], tool_choice=any
       valid tool -> execute manage.py + auto-reply
       invalid/absent tool / API error -> draft_for_human
       empty whitelist -> escalate (no API call)
  reply via email:outbox  + always write a cuttlefish `customer note` (audit)
```

### Tier 1 — `tools/email_cs/triage.py`

Mirrors `reflections/memory_management.py::_gemma_classify`: one
`ollama_client.chat(messages=..., model=OLLAMA_CLASSIFIER_MODEL, options={"temperature": 0})` call
via `tools/ollama_client` (where `OLLAMA_CLASSIFIER_MODEL = "granite4.1:3b"` from `config/models.py`) with
tolerant post-hoc JSON extraction (`extract_json_payload`), **not**
`format=json`. Fail-safe by contract: every error path (Ollama down, parse
failure, validation failure, empty input, `customer_id is None`)
deterministically returns an **escalate** `Triage` — it never raises into the
bridge and never silently auto-handles.

### The escalation gate — `tools/email_cs/gate.py`

A pure `decide(triage, threshold) -> Disposition`. Forces **escalate** for:

1. the `raise_to_human` lane,
2. any escalation signal (regardless of category/confidence),
3. confidence below the threshold (`CONFIDENCE_THRESHOLD = 0.75`), or
4. any category whose Tier 2 tool whitelist is **empty** (the *structural* gate).

### Tier 2 — `tools/email_cs/agents.py`

Mirrors `tools/classifier.py` (Anthropic `MODEL_FAST` via the shared
`anthropic_slot` concurrency guard). The agent is built **per category** with
only that category's whitelisted tools in the request's `tools=[]` array and
`tool_choice={"type": "any"}` to force a tool call. Enforcement is by
**absence**:

- A category with an **empty (phase-filtered) whitelist** gets an empty `tools`
  array — the function returns `escalate` *without an API call*.
- An invalid/absent tool name yields `draft_for_human`, never an unguarded
  action.
- Any Anthropic API exception yields `draft_for_human`, never auto.

This is the safety core: a refund/takedown/invoice operation has **no callable
tool by construction**, so even a confident misclassification into a mutating
lane can only ever call a whitelisted safe verb.

### Cuttlefish subprocess — `tools/email_cs/cuttlefish.py`

Mirrors `bridge/routing.py::_dispatch_subprocess_resolver` exactly: argv-form
`asyncio.create_subprocess_exec` (never shell), `stdin=DEVNULL`, hard
`asyncio.wait_for` timeout with `proc.kill()`, non-zero exit raises, `cwd` = the
cuttlefish `working_directory`. Every command is scoped `--email <customer_id>`
(the **trusted** resolved id, never parsed from email body) and appends `--json`.
The result envelope is validated to be a JSON object; any drift raises, and the
handler fails-safe to escalate.

### Orchestration — `tools/email_cs/handler.py`

`handle_customer_email(parsed, project, customer_id, *, session_id, shadow_mode)`
runs the pipeline and returns a `HandlerOutcome`. Its `short_circuit` flag tells
the bridge whether to skip the fallback `AgentSession` spawn:

| Disposition | Side effect | `short_circuit` |
|-------------|-------------|-----------------|
| auto (Phase ≥ 2) | execute read-only verb → audit note → email reply | `True` |
| draft | `customer email draft` + Telegram ping + audit note (no customer send) | `True` |
| escalate | Telegram ping + audit note; **fall through** to AgentSession | `False` |

**Audit-before-reply ordering** (race mitigation): the audit `customer note` is
written *immediately after* the command returns, before the customer-facing
reply, so a crash leaves a durable record. The Telegram ping is best-effort and
never swallows the audit note.

## Shadow mode and the three-phase rollout

| Phase | Flags | Behavior |
|-------|-------|----------|
| 1 (default) | `shadow_mode=True` | Classify + write an audit note per verdict. **Send nothing.** Never short-circuits. Calibrate the threshold against real inbound. |
| 2 | `shadow_mode=False`, `auto_mutations=False` | Read-only auto-replies (`customer show`, `checkout-url`). Mutating lanes draft/escalate. |
| 3 | `shadow_mode=False`, `auto_mutations=True` | Mutating auto-handlers, once shadow data proves triage trustworthy. |

Phase flips are **human-gated operational decisions** made after reviewing
shadow-mode verdict logs — not flipped by code. In Phase 1 the
`auto_mutations`/read-only filter still strips mutating verbs from the Tier 2
whitelist as defense in depth.

## Configuration

The layer activates only when the project (in the private, iCloud-synced
`~/Desktop/Valor/projects.json`) declares both a `customer_resolver` block (see
[customer-resolver.md](customer-resolver.md)) and an `email.customer_service`
block:

```json
{
  "projects": {
    "cuttlefish": {
      "working_directory": "~/src/cuttlefish",
      "customer_resolver": { "type": "subprocess", "command": ["..."] },
      "email": {
        "customer_service": {
          "shadow_mode": true,
          "auto_mutations": false,
          "escalation_chat_id": -1001234567,
          "note_category": "general"
        }
      }
    }
  }
}
```

| Key | Default | Meaning |
|-----|---------|---------|
| `shadow_mode` | `true` | Phase 1: classify + audit only, send nothing. |
| `auto_mutations` | `false` | Phase 3 gate: allow mutating verbs in Tier 2 whitelists. |
| `escalation_chat_id` | — | Telegram chat for draft/escalate pings. If absent, the ping is skipped (logged) but the audit note still lands. |
| `note_category` | `general` | `--category` passed to `customer note`. |

The wiring is private config and is **not** committed. Until it is present, the
layer is dead code that never runs.

## Audit trail

This repo does **not** own a `CustomerServiceNote` model. Auditing reuses the
cuttlefish-side `manage.py customer note --email … --body … --category …
--session-id … --json` command. Drafts reuse cuttlefish's existing
`customer email draft` workflow rather than inventing a parallel drafting
surface.

## Safety properties

- No failure path reaches a silent auto: every exception logs and changes the
  disposition observably (escalate or draft). No bare `except Exception: pass`
  anywhere in `tools/email_cs/`.
- The `--email` arg is always the trusted resolved `customer_id`, never a value
  parsed from message content — preventing cross-account leakage.
- ESCALATE-only lanes have empty tool whitelists, so full-auto cannot touch
  refund/takedown/invoice operations by construction.
- The layer is inert for any project without an `email.customer_service` block.

## Tests

- `tests/unit/test_email_cs_triage.py` — Tier 1 fail-safe paths.
- `tests/unit/test_email_cs_gate.py` — every escalate trigger + the auto path.
- `tests/unit/test_email_cs_agents.py` — structural gate, draft fallbacks, phase filter.
- `tests/unit/test_email_cs_cuttlefish.py` — subprocess timeout/exit/JSON failures, `--email` scoping.
- `tests/integration/test_email_cs_handler.py` — per-lane fixture inbound → expected disposition (subprocess stubbed); inertness; audit-on-escalate.

## See also

- [Customer Resolver](customer-resolver.md) — the upstream dependency.
- [Email Bridge](email-bridge.md) — the IMAP/SMTP transport.
- [Infra: Email CS Auto-Reply](../infra/email-cs-auto-reply.md) — external deps, cost, rollback.
