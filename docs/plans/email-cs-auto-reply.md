---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-06-05
tracking: https://github.com/tomcounsell/ai/issues/1573
last_comment_id:
---

# Email Customer-Service Auto-Reply Layer for Cuttlefish

## Problem

Cuttlefish ([`yudame/cuttlefish`](https://github.com/yudame/cuttlefish)) is a personalized podcast service where Valor acts as the customer-service agent over email. Inbound customer mail lands at `bridge/email_bridge.py::_process_inbound_email()` (line 808). After the sender is resolved to a `customer_id` via the existing `customer_resolver` hook, the handler enqueues a **generic, unscoped `AgentSession`** with no triage.

**Current behavior:** Every inbound customer email spawns a full TEAMMATE-persona AgentSession with no intent classification, no capability boundary, and no distinction between safe-to-automate requests (status lookups) and ones that must reach a human (refunds, anger, legal). There is no way to say "this is a billing question" vs. "this is an episode regeneration request" vs. "this customer is angry and must reach a human."

**Desired outcome:** Inbound Cuttlefish customer emails are triaged into four lanes — **manage_podcast**, **manage_episode**, **other_customer_service**, and **raise_to_human** — and acted on accordingly: auto-handled when there is a safe tool and high confidence, drafted-for-human or escalated otherwise. Full auto is the destination, gated structurally so it ships safely in incremental phases (shadow → read-only auto → mutating auto).

## Freshness Check

**Baseline commit:** `47008572`
**Issue filed at:** 2026-06-05T06:41:32Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/email_bridge.py:808` — `_process_inbound_email()` entry point — **still holds**. Customer-resolver branch at lines 857–874; `enqueue_agent_session()` call at lines 950–964.
- `config/models.py:135` — `OLLAMA_LOCAL_MODEL = "gemma4:e2b"` — **still holds**. `MODEL_FAST = HAIKU` at line 154.
- `reflections/memory_management.py:537` — `_gemma_classify()` — **still holds**. Uses `ollama.chat(options={"temperature": 0})` + tolerant `extract_json_payload()`, **not** `format=json`.
- `tools/classifier.py:57` — `classify_request()` Haiku structured-output pattern — **still holds**.
- `docs/features/customer-resolver.md` — **present** (7.1 KB).
- `agent/output_handler.py:191,280` — `EmailOutputHandler.send()` → `email:outbox:{session_id}` — **still holds**.
- `bridge/routing.py:1403` — `_dispatch_subprocess_resolver()` (argv-form `asyncio.create_subprocess_exec`, hard timeout, no shell) — **still holds**; this is the canonical subprocess pattern to mirror.

**Cited sibling issues/PRs re-checked:**
- #1093 (customer_resolver) — **closed**; its doc is the cited reference. Confirmed.
- #1327 (composed persona system) — merged; provides the `customer-service` persona the resolved branch forces.

**Commits on main since issue was filed (touching referenced files):** none touching `bridge/email_bridge.py`.

**Active plans in `docs/plans/` overlapping this area:** `incoming_email_attachments.md` also touches `bridge/email_bridge.py` (attachment handling — different concern). **Coordination, not blocker** — coordinate merge order to avoid handler-region conflicts near `_process_inbound_email()`.

**Notes:** All issue premises hold at baseline. Two corrections surfaced during recon (see Prior Art / Solution): the audit and human-draft mechanisms already exist as cuttlefish-side `manage.py` commands.

## Prior Art

- **#1093 — dynamic customer resolver**: Replaced static allow-lists with a callable returning `customer_id`. This is the upstream dependency: triage only runs *after* `resolve_customer()` returns non-None. The subprocess dispatch pattern (`_dispatch_subprocess_resolver`) it introduced is the template for this plan's `manage.py` wrapper.
- **#1327 — composed persona system**: Added the `customer-service` persona that `_process_inbound_email()` forces for resolved customers. The triage layer slots in alongside this, before the AgentSession spawn.
- **Cuttlefish management surface (recon)**: `apps/common/management/commands/customer.py` and `apps/podcast/management/commands/episode.py` already expose JSON-emitting verbs. **Two issue assumptions are revised by this:**
  - The issue proposes a new `CustomerServiceNote` model in *this* repo. It **does not exist here** and should not be built here — cuttlefish already has `manage.py customer note --email <e> --body <t> --category … --session-id <id> [--json]`. Audit = calling that command.
  - Cuttlefish already has a human-draft workflow: `manage.py customer email draft|send|discard` + `customer inbox list|read`. The `draft_for_human` disposition should evaluate reusing `customer email draft` rather than inventing a parallel path.

No prior attempt at email CS triage exists (closed-issue and merged-PR searches returned empty). This is greenfield for the triage layer; the integration points are all established.

## Research

**Queries used:**
- Anthropic SDK tool use / forced `tool_choice` / restricting available tools (2026)

**Key findings:**
- **Structural tool-gating via the `tools=[]` array + `tool_choice`** ([Anthropic tool-use docs](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use)): Claude can only call tools that are present in the request's `tools` array. `tool_choice={"type": "any"}` forces a tool call when at least one tool is provided; `{"type": "tool", "name": …}` forces a specific tool. **This is the enforcement mechanism for the escalation gate:** the Tier 2 action agent is built per-category with only that category's whitelisted tools in the array. A category with no safe tool (refund, takedown, invoice) is given an **empty tools array** — the agent literally cannot emit a mutating call and the gate forces `escalate` by construction, not by prompt discipline.
- **Forced tool use is incompatible with extended thinking** — irrelevant here (Tier 2 uses `MODEL_FAST` = Haiku without thinking), but noted so the builder doesn't combine them.

No PydanticAI findings pursued — the issue explicitly declines it to stay consistent with `_gemma_classify` (Tier 1) and `tools/classifier.py` (Tier 2). This plan honors that.

## Data Flow

1. **Entry point**: IMAP poll → `_poll_imap()` → `_process_inbound_email(parsed, config)` (`bridge/email_bridge.py:808`).
2. **Project resolution**: `find_project_for_email()` → cuttlefish project (requires `projects.json` wiring — see Prerequisites).
3. **Customer resolution**: `resolve_customer()` → `customer_id` or None. **None → existing drop path (unchanged).**
4. **NEW — Tier 1 triage** (`tools/email_cs/triage.py`): `triage_local(subject, body, customer_id)` → `ollama.chat(model="gemma4:e2b", temperature=0)` → tolerant JSON parse → `Triage` pydantic model `{category, confidence, escalation_signal, reason}`. Parse failure OR `customer_id is None` OR `confidence < threshold` OR any escalation signal → deterministic `Disposition.ESCALATE`.
5. **NEW — escalation gate** (`tools/email_cs/gate.py`): given the `Triage`, decide `auto | draft | escalate`. The gate is the *only* thing between triage and a side effect. It checks: (a) escalation signals / low confidence → escalate; (b) does the category have a non-empty tool whitelist? no → escalate; yes → proceed to Tier 2.
6. **NEW — Tier 2 action agent** (`tools/email_cs/agents.py`): per-category Anthropic SDK (`MODEL_FAST`) call with `tools = TOOLS[category]` (whitelist) and `tool_choice={"type":"any"}`. Agent picks one whitelisted tool + args. Invalid/absent tool name → `draft_for_human`. Agent's own `escalate()` signal → `route_to_human()`.
7. **NEW — cuttlefish subprocess** (`tools/email_cs/cuttlefish.py`): execute the chosen `manage.py <verb> … --email <customer> --json` via `asyncio.create_subprocess_exec` (argv-form, hard timeout, `cwd = cuttlefish working_directory`). Mirrors `_dispatch_subprocess_resolver`.
8. **Reply / audit / handoff**:
   - **auto**: render reply from subprocess `--json` result → `email:outbox:{session_id}` via `EmailOutputHandler.send()` (reuse). Always call `manage.py customer note --session-id … --json` to audit.
   - **draft**: `manage.py customer email draft …` (cuttlefish-side human-review queue) + Telegram ping to the Cuttlefish chat via `telegram:outbox:` + audit note. No customer-facing send.
   - **escalate**: Telegram ping to the Cuttlefish chat + audit note + (shadow phases) no send. The original inbound stays handled — the existing AgentSession spawn is the fallback for escalate/draft lanes in early phases (see Solution → Phasing).
9. **Output**: customer receives an auto-reply (auto lane only, phase ≥2), OR a human is pinged (draft/escalate). Every interaction is recorded as a cuttlefish `customer note`.

## Architectural Impact

- **New dependencies**: `ollama` (already used by `_gemma_classify`), `anthropic` (already used). No new packages. New subprocess dependency on cuttlefish `manage.py` (already invoked by the resolver).
- **Interface changes**: `_process_inbound_email()` gains a branch after `resolve_customer()` succeeds and before `enqueue_agent_session()`. New package `tools/email_cs/`.
- **Coupling**: Adds a runtime coupling from the ai bridge → cuttlefish `manage.py` CLI surface (subprocess, same coupling shape the resolver already has). No Python import coupling — strictly subprocess-over-`--json`.
- **Data ownership**: Audit notes are owned by cuttlefish (`CustomerServiceNote` lives in *that* repo, written via `customer note`). This repo owns only the triage verdict (logged +, in shadow mode, mirrored to a note).
- **Reversibility**: High. The whole layer is gated behind a `shadow_mode` flag (default on). Phase 1 sends nothing; disabling the layer reverts to today's behavior (always spawn AgentSession). No schema changes in this repo.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (phasing strategy, threshold/escalation-signal set, cuttlefish-command contract)
- Review rounds: 2+ (escalation-gate correctness is safety-critical; subprocess isolation; phasing flags)

This is large because of the safety surface (a wrong auto-reply to a customer is a real-world incident), the cross-repo subprocess contract, and the three-phase rollout — not raw code volume.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Ollama running with `gemma4:e2b` | `python -c "import ollama; ollama.show('gemma4:e2b')"` | Tier 1 local classification |
| Anthropic API key | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Tier 2 action agent |
| Cuttlefish repo present | `test -d ~/src/cuttlefish/apps/common/management/commands` | Subprocess target exists |
| Cuttlefish `customer` command works | `cd ~/src/cuttlefish && .venv/bin/python manage.py customer list --json` | `manage.py --json` surface reachable |
| cuttlefish wired in `projects.json` | `python -c "import json,os; p=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json'))); c=p['projects']['cuttlefish']; assert c.get('email') and c.get('customer_resolver'), 'cuttlefish needs email + customer_resolver blocks'"` | Triage only fires for projects with a resolver |

Run all checks: `python scripts/check_prerequisites.py docs/plans/email-cs-auto-reply.md`

**Note:** The `projects.json` wiring (an `email` block with cuttlefish contacts/domains + a `customer_resolver`) is **private config** in `~/Desktop/Valor/projects.json` (not committed). Without it, `_process_inbound_email()` never enters the resolver branch and the triage layer is dead code. This is an `[EXTERNAL]` prerequisite (see No-Gos) — but the code must degrade gracefully when it's absent (the layer simply never runs).

## Solution

### Key Elements

- **`tools/email_cs/schema.py`** — `Category` (enum: manage_podcast | manage_episode | other_customer_service | raise_to_human), `Disposition` (enum: auto | draft | escalate), `Triage` (pydantic BaseModel: category, confidence, escalation_signal, reason). Single source of truth for the type contract.
- **`tools/email_cs/triage.py`** — Tier 1. `triage_local(subject, body, customer_id) -> Triage`. Mirrors `_gemma_classify`: `ollama.chat(model=OLLAMA_LOCAL_MODEL, options={"temperature": 0})`, tolerant JSON parse, **fail-safe → escalate** (never raises into the bridge).
- **`tools/email_cs/gate.py`** — the escalation gate. `decide(triage, threshold) -> Disposition`. The *only* function before a side effect. Forces escalate for: low confidence, any escalation signal, any category whose tool whitelist is empty.
- **`tools/email_cs/agents.py`** — Tier 2. Per-category `TOOLS` whitelist dict. `run_action_agent(category, triage, email) -> ActionResult`. Anthropic SDK (`MODEL_FAST`) with `tools=TOOLS[category]`, `tool_choice={"type":"any"}`. Empty whitelist → returns escalate without an API call. Invalid tool → `draft_for_human`.
- **`tools/email_cs/cuttlefish.py`** — subprocess wrapper. `run_manage_command(verb_argv, customer_email, timeout) -> dict`. argv-form `asyncio.create_subprocess_exec`, `cwd` = cuttlefish `working_directory` from `projects.json`, `--json` parse, hard timeout. Mirrors `_dispatch_subprocess_resolver`.
- **`tools/email_cs/handler.py`** (new — the orchestration entry the bridge calls) — `async def handle_customer_email(parsed, project, customer_id, *, shadow_mode) -> HandlerOutcome`. Runs triage → gate → (Tier 2 if auto) → reply/draft/escalate. Returns a structured outcome so `_process_inbound_email()` can decide whether to still spawn the fallback AgentSession.

### Flow

Inbound email (resolved customer) → **Tier 1 triage** (gemma4:e2b) → **escalation gate** →
- `auto` → **Tier 2 action agent** (whitelisted tool) → `manage.py <verb> --json` → render reply → `email:outbox` → `customer note` (audit) → **done**
- `draft` → `manage.py customer email draft` → Telegram ping (Cuttlefish chat) → `customer note` → **done (no customer send)**
- `escalate` → Telegram ping (Cuttlefish chat) → `customer note` → fall through to existing AgentSession spawn (human-in-the-loop)

### Technical Approach

- **Hook location** (resolves issue OQ1): the triage layer slots into `_process_inbound_email()` **after** the `customer_id` resolution succeeds (current line ~874, inside the `if customer_id is not None:` region) and **before** the `enqueue_agent_session()` call (line ~951). For **handled `auto` lanes it short-circuits** — replaces the AgentSession spawn. For `draft` and `escalate` lanes in early phases it **runs before and falls through** to the existing spawn so a human path still exists. A clean `if outcome.short_circuit: return` gate after the handler call.
- **Inline, not a new AgentSession** (resolves issue OQ2): Tier 2 runs **inline in the bridge as a bounded subprocess-driving loop**, not via the worker/AgentSession machinery. Rationale: (a) latency — a status lookup must reply in seconds, not wait for the serial worker queue; (b) the single-machine-ownership model already runs the resolver subprocess inline here; (c) observability — the verdict + tool call + result log in one place. The existing AgentSession spawn remains the fallback for escalate/draft, preserving the full-agent path when triage punts.
- **`route_to_human()`** (resolves issue OQ3): realized as (1) a Telegram ping to the Cuttlefish project chat via `telegram:outbox:{session_id}` (reuse the relay), AND (2) a cuttlefish `customer note` for the durable audit trail. No new mailbox-label machinery — the `valor-retry` label path already exists in the resolver for the not-a-customer case; human-handoff reuses the Telegram ping which is the existing operator surface.
- **Confidence threshold & escalation signals** (resolves issue OQ4): start at **0.75** (issue placeholder) as a named constant in `schema.py`, **tunable via shadow-mode data**. The escalation-signal set is fixed up front (anger/churn/threats, legal/press/compliance, refund/credit mentions, identity mismatch, low confidence, VIP markers) and detected by Tier 1's `escalation_signal` field. Phase 1 logs every verdict so the threshold can be calibrated against real inbound before any auto-send.
- **Subprocess auth/isolation** (resolves issue OQ5): mirror `_dispatch_subprocess_resolver` exactly — `asyncio.create_subprocess_exec` (argv-form only, **never shell**), `cwd` = cuttlefish `working_directory`, `stdin=DEVNULL`, hard `asyncio.wait_for` timeout, non-zero exit → raise → fail-safe to escalate. The cuttlefish venv python is `~/src/cuttlefish/.venv/bin/python` resolved from `working_directory`. Every command is scoped `--email <customer_id>` so an agent cannot touch another account.
- **Shadow mode**: a `shadow_mode` flag (default `True`) read from the cuttlefish `projects.json` `email` block. When true: run triage + gate + (optionally) Tier 2 *planning* but **send nothing** to the customer; write only the `customer note` verdict and (optionally) a Telegram digest. This is the issue's Phase-1 default.

#### Phasing (issue rollout, encoded as flags)

| Phase | Flag state | Behavior |
|-------|-----------|----------|
| 1 (default on deploy) | `shadow_mode=True` | Classify + audit-note every verdict. Send nothing. Calibrate threshold. |
| 2 | `shadow_mode=False`, `auto_mutations=False` | Enable read-only auto-replies (`customer show`, `checkout-url` — zero mutation). Mutating lanes draft/escalate. |
| 3 | `shadow_mode=False`, `auto_mutations=True` | Enable mutating auto-handlers (onboard, configure, provision) once logged verdicts prove triage trustworthy. |

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `triage.py` — the `try/except` around `ollama.chat` must **return `Triage(disposition=escalate)`** (fail-safe), with a test asserting an Ollama failure yields escalate, not a crash and not a silent auto.
- [ ] `cuttlefish.py` — subprocess timeout and non-zero exit must raise, caught by `handler.py` and converted to escalate; test asserts a stubbed non-zero exit produces escalate + an audit note recording the failure.
- [ ] `agents.py` — Anthropic API exception → `draft_for_human` (never auto); test asserts a stubbed API error yields draft.
- [ ] No bare `except Exception: pass` permitted anywhere in `tools/email_cs/` — every handler logs at WARNING and changes disposition observably.

### Empty/Invalid Input Handling
- [ ] `triage_local("", "", customer_id)` (empty subject+body) → escalate (low signal); test added.
- [ ] `customer_id is None` reaching the handler → escalate deterministically, never call Tier 2; test added.
- [ ] Tier 2 agent returns a tool name not in the whitelist → `draft_for_human`; test added (this is the structural-gate assertion).
- [ ] Malformed `--json` from `manage.py` → escalate, audit-note the parse failure; test added.

### Error State Rendering
- [ ] Customer-visible auto-reply error path: if reply rendering fails after a successful mutation, the gate must escalate-with-context (the mutation happened, the reply didn't) — test asserts a human is pinged, not silence.
- [ ] Escalation Telegram ping failure must not swallow — log at ERROR and still write the audit note; test asserts the audit note is written even when the ping fails.

## Test Impact

- [ ] `tests/integration/test_email_bridge*.py` (existing inbound-email tests, if any reference `_process_inbound_email`) — **UPDATE**: add assertions that, for a project *without* a `customer_resolver`/email-CS config, behavior is unchanged (still spawns AgentSession). Confirms the new branch is inert when not wired.
- [ ] `tests/unit/test_routing*.py` (resolver tests) — **no change**: triage is a separate module; resolver contract is untouched.

No other existing tests are affected — `tools/email_cs/` is a greenfield package and the `_process_inbound_email` change is an additive branch gated on cuttlefish-specific config that no existing test exercises. The new integration test (per-lane fixture inbound) is net-new coverage.

## Rabbit Holes

- **Building a `CustomerServiceNote` model in this repo** — it already exists cuttlefish-side as `customer note`. Do NOT add a new model here. (Recon-confirmed.)
- **Inventing a parallel human-draft queue** — cuttlefish already has `customer email draft|send|discard`. Reuse it; don't build a second drafting surface.
- **Routing Tier 2 through the worker/AgentSession machinery** — adds queue latency and obscures the verdict. Tier 2 runs inline. (Decided in OQ2.)
- **`format=json` Ollama mode** — the repo pattern is tolerant post-hoc parse (`extract_json_payload`). Don't introduce a divergent invocation.
- **Generalizing the layer to non-cuttlefish projects** — this is cuttlefish-specific (the capability map is cuttlefish `manage.py`). A generic CS framework is a separate project.
- **Implementing the two new cuttlefish commands (`episode regenerate`, `customer cancel`)** — those live in `yudame/cuttlefish` and are NOT this repo's scope (see No-Gos). Phases 1–2 don't need them.

## Risks

### Risk 1: Auto-reply to the wrong customer (cross-account leakage)
**Impact:** A customer receives another account's data or a mutation hits the wrong account — a real-world privacy/billing incident.
**Mitigation:** Every `manage.py` invocation is scoped `--email <customer_id>` (the resolved sender). The customer_id comes from the trusted resolver, never from email body content. Integration test asserts the `--email` arg always equals the resolved `customer_id`, never a value parsed from the message.

### Risk 2: Escalation gate bypassed by a confident-but-wrong classifier
**Impact:** A refund/legal/angry email gets auto-handled because Tier 1 mislabeled it with high confidence.
**Mitigation:** Two independent gates. (a) Escalation *signals* (refund/legal/anger keywords surfaced by Tier 1) force escalate regardless of category/confidence. (b) The *structural* gate: ESCALATE-only lanes have an empty Tier 2 tool whitelist, so even a misclassification into a mutating lane can only call a whitelisted safe verb — there is no refund/takedown tool to call. Shadow mode (Phase 1) validates the classifier against real mail before any send.

### Risk 3: Cuttlefish `manage.py` contract drift
**Impact:** A cuttlefish refactor renames a verb or changes `--json` shape; auto-replies start failing or rendering garbage.
**Mitigation:** `cuttlefish.py` validates the `--json` envelope shape and fails-safe to escalate on any parse mismatch (never sends garbage). The Prerequisites check exercises `customer list --json` so a broken contract is caught at build/deploy. A companion cuttlefish issue tracks the command contract.

### Risk 4: Subprocess resource exhaustion / hung manage.py
**Impact:** A hung `manage.py` blocks the IMAP poll loop.
**Mitigation:** Hard `asyncio.wait_for` timeout (mirrors resolver), `proc.kill()` on timeout, fail-safe to escalate. The handler is `await`ed within the existing async poll loop; the timeout bounds latency.

## Race Conditions

### Race 1: Concurrent inbound from the same customer (thread coalescing)
**Location:** `bridge/email_bridge.py` session-coalescing block (lines 885–918) and the new handler call.
**Trigger:** Two emails from the same customer arrive in the same poll batch; both resolve to the same `session_id` via subject coalescing.
**Data prerequisite:** The `session_id` must be stable before the handler writes to `email:outbox:{session_id}`.
**State prerequisite:** Audit notes must not double-fire for one logical interaction.
**Mitigation:** The handler runs *after* `session_id` is constructed (it reuses the existing coalescing result). Each inbound is still a distinct triage+note (one note per email is correct — they're separate customer messages). No shared mutable state between concurrent handler calls; each gets its own `parsed` dict. IMAP marks messages `\Seen` immediately (existing behavior), so the same message is never processed twice on this machine.

### Race 2: Mutation succeeds but reply/audit fails
**Location:** `handler.py` auto-lane, between `manage.py <mutating verb>` success and the `email:outbox` write.
**Trigger:** Process crash or Redis hiccup after a mutation lands but before the reply queues.
**Data prerequisite:** The audit note should record the mutation even if the reply fails.
**State prerequisite:** No double-mutation on retry.
**Mitigation:** Order of operations — write the audit `customer note` *immediately after* the mutation returns (before the reply), so a crash leaves a durable record. Mutations are idempotent where possible (`configure` is set-state, not increment). Phase 1/2 (read-only) have no mutation, so this race only applies in Phase 3 and is gated behind `auto_mutations=True`.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #1573]` The two new cuttlefish management commands (`episode regenerate`, `customer cancel --at-period-end`) live in `yudame/cuttlefish`, a separate repo/venv. They are tracked as a companion cuttlefish issue; Phases 1–2 of this plan do not depend on them. (Tracking issue: this plan's issue #1573 names the dependency; the cuttlefish-side work is filed in that repo.)
- `[EXTERNAL]` Wiring the cuttlefish `email` block + `customer_resolver` into `~/Desktop/Valor/projects.json` — private, iCloud-synced config the agent edits on the owning machine but which is not committed. The code degrades gracefully (layer is inert) until this is present.
- `[EXTERNAL]` Phase 2/3 flag flips (`shadow_mode=False`, `auto_mutations=True`) are human-gated operational decisions made after reviewing shadow-mode verdict logs — not flipped by this plan.
- `[ORDERED]` Enabling mutating auto-handlers (Phase 3) is blocked on the human-gated review of Phase-1 shadow data proving triage trustworthiness.

## Update System

- **No `scripts/remote-update.sh` change required** — the layer uses existing deps (`ollama`, `anthropic`) and the existing cuttlefish subprocess pattern.
- **`projects.json` propagation**: the cuttlefish `email` + `customer_resolver` blocks are private config; they sync via iCloud (`~/Desktop/Valor/projects.json`), not via the update script. Documented as an `[EXTERNAL]` prerequisite.
- **Ollama model**: `gemma4:e2b` is already the repo's local model (`OLLAMA_LOCAL_MODEL`), pulled by existing `/update` Ollama-sync steps. No new model to propagate.
- **Single-machine ownership**: cuttlefish is owned by `Tom's MacBook Pro` (this machine). The triage layer runs only where the cuttlefish email block is owned — consistent with the single-machine-ownership invariant. No multi-machine coordination needed.

## Agent Integration

The triage layer is **bridge-internal**, not an agent-facing tool. It runs inside `_process_inbound_email()` before any AgentSession spawn.

- **No new CLI entry point** in `pyproject.toml [project.scripts]` — the layer is not invoked by the agent's Bash tool; it's invoked by the bridge directly.
- **The bridge imports the new code directly**: `bridge/email_bridge.py::_process_inbound_email()` imports `tools.email_cs.handler.handle_customer_email` and calls it inline. This is the established pattern (the bridge already imports `resolve_customer` from `bridge.routing`).
- **No MCP server change** — Tier 2's "tools" are cuttlefish `manage.py` subprocesses gated by a Python whitelist, not MCP tools exposed to a Claude agent.
- **Integration test** verifies the bridge path: a fixture inbound for each lane, with `tools/email_cs/cuttlefish.py` subprocess stubbed, asserts the expected disposition (auto/draft/escalate) and that `handle_customer_email` is actually reached from `_process_inbound_email` (grep-confirm the import + call).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/email-cs-auto-reply.md` describing the two-tier triage, the four lanes, the structural escalation gate, the cuttlefish `manage.py` capability map, and the three-phase rollout.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/customer-resolver.md` (the upstream dependency) to the new doc.

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for runtime features.

### Inline Documentation
- [ ] Module docstrings for each `tools/email_cs/*.py` file naming the pattern it mirrors (`triage.py` → `_gemma_classify`; `agents.py` → `tools/classifier.py`; `cuttlefish.py` → `_dispatch_subprocess_resolver`).
- [ ] Docstring on `handle_customer_email` documenting the disposition contract and the shadow-mode/phasing flags.

### Infra Documentation
- [ ] Create `docs/infra/email-cs-auto-reply.md` (new external dependency on cuttlefish `manage.py` + Ollama + Anthropic; rate/cost notes; rollback = flip `shadow_mode` / unwire `projects.json`).

## Success Criteria

- [ ] `tools/email_cs/` exists with `schema.py`, `triage.py`, `agents.py`, `gate.py`, `cuttlefish.py`, and `handler.py` following the recon-confirmed patterns.
- [ ] Tier 1 `triage_local` classifies a real sample inbound into one of the four lanes via local `gemma4:e2b`, returning a validated `Triage` model; parse failure or `customer_id is None` deterministically returns escalate.
- [ ] The escalation gate forces `raise_to_human` for: confidence < threshold (0.75), any escalation signal, and any category whose tool whitelist is empty.
- [ ] Tier 2 action agents can only invoke commands in their category's whitelist (enforced by the `tools=[]` array + empty-array-for-escalate-lanes); an invalid/absent tool name yields `draft_for_human`, never an unguarded action.
- [ ] `_process_inbound_email()` invokes `handle_customer_email`; auto lanes reply via `EmailOutputHandler` outbox; every interaction writes a cuttlefish `customer note` (audit).
- [ ] Shadow-mode flag exists (classify + audit-note, send nothing) and is the default on first deploy.
- [ ] Integration test: a fixture inbound email for each of the four lanes drives the expected disposition (auto / draft / escalate) with the cuttlefish subprocess layer stubbed.
- [ ] Behavior is unchanged for projects without an email-CS config (the new branch is inert).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `bridge/email_bridge.py` imports and calls `tools.email_cs.handler.handle_customer_email`.

## Team Orchestration

### Team Members

- **Builder (schema + triage)**
  - Name: `triage-builder`
  - Role: `schema.py`, `triage.py`, `gate.py` — the type contract and Tier 1 + gate.
  - Agent Type: builder
  - Resume: true

- **Builder (action agent + subprocess)**
  - Name: `action-builder`
  - Role: `agents.py`, `cuttlefish.py` — Tier 2 whitelist agent and the `manage.py` subprocess wrapper.
  - Agent Type: builder
  - Resume: true

- **Builder (handler + bridge wiring)**
  - Name: `handler-builder`
  - Role: `handler.py` orchestration + the `_process_inbound_email()` branch + shadow/phasing flags.
  - Agent Type: builder
  - Resume: true

- **Validator (escalation safety)**
  - Name: `gate-validator`
  - Role: Verify the structural gate cannot be bypassed; every failure path fails to escalate, never to silent auto.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `cs-documentarian`
  - Role: feature + infra docs.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Build schema + Tier 1 triage + gate
- **Task ID**: build-triage
- **Depends On**: none
- **Validates**: tests/unit/test_email_cs_triage.py (create), tests/unit/test_email_cs_gate.py (create)
- **Informed By**: recon (Tier 1 mirrors `_gemma_classify`; tolerant parse not `format=json`)
- **Assigned To**: triage-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/email_cs/schema.py` (Category, Disposition, Triage, threshold constant 0.75, escalation-signal set).
- Create `tools/email_cs/triage.py` mirroring `_gemma_classify` (ollama.chat temp=0, tolerant parse, fail-safe → escalate).
- Create `tools/email_cs/gate.py` (`decide(triage, threshold) -> Disposition`; empty-whitelist → escalate).
- Unit tests for empty input, customer_id None, low confidence, escalation signal.

### 2. Build Tier 2 action agent + cuttlefish subprocess
- **Task ID**: build-action
- **Depends On**: build-triage (imports schema)
- **Validates**: tests/unit/test_email_cs_agents.py (create), tests/unit/test_email_cs_cuttlefish.py (create)
- **Informed By**: research (tools=[] + tool_choice any structural gate); recon (`_dispatch_subprocess_resolver` argv-form pattern)
- **Assigned To**: action-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/email_cs/agents.py` (per-category TOOLS whitelist; Anthropic MODEL_FAST; empty whitelist → escalate without API call; invalid tool → draft).
- Create `tools/email_cs/cuttlefish.py` (argv-form async subprocess, cwd from projects.json, hard timeout, `--json` parse + envelope validation, fail-safe escalate).
- Unit tests: invalid tool name → draft; subprocess timeout/non-zero → escalate; malformed json → escalate.

### 3. Build handler + bridge wiring + phasing flags
- **Task ID**: build-handler
- **Depends On**: build-triage, build-action
- **Validates**: tests/integration/test_email_cs_handler.py (create)
- **Assigned To**: handler-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/email_cs/handler.py` (`handle_customer_email` orchestration; shadow_mode + auto_mutations flags; audit-note-before-reply ordering).
- Wire `_process_inbound_email()`: call handler after `customer_id` resolution, short-circuit on auto, fall through on draft/escalate. Inert when no email-CS config.
- Reuse `EmailOutputHandler` for auto replies; `telegram:outbox` ping for route_to_human.
- Integration test: per-lane fixture inbound → expected disposition (subprocess stubbed).

### 4. Validate escalation safety
- **Task ID**: validate-gate
- **Depends On**: build-handler
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no failure path reaches silent auto; every exception → escalate or draft.
- Verify `--email` arg always equals resolved customer_id, never body-parsed.
- Verify ESCALATE lanes have empty tool whitelists.
- Verify behavior unchanged for non-CS projects.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-handler, validate-gate
- **Assigned To**: cs-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/email-cs-auto-reply.md` + README index entry + cross-link from customer-resolver doc.
- Create `docs/infra/email-cs-auto-reply.md`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-triage, build-action, build-handler, validate-gate, document-feature
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full suite; verify all success criteria including docs and the grep-confirm of the bridge import/call.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_email_cs_*.py tests/integration/test_email_cs_*.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/email_cs/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/email_cs/` | exit code 0 |
| Package exists | `test -f tools/email_cs/schema.py -a -f tools/email_cs/triage.py -a -f tools/email_cs/agents.py -a -f tools/email_cs/gate.py -a -f tools/email_cs/cuttlefish.py -a -f tools/email_cs/handler.py` | exit code 0 |
| Bridge calls handler | `grep -q "email_cs.handler" bridge/email_bridge.py` | exit code 0 |
| Shadow mode is default | `grep -q "shadow_mode" tools/email_cs/handler.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

The five open questions from the issue are **resolved in the plan** (Technical Approach):

1. **Hook location** → after `customer_id` resolution, before `enqueue_agent_session()`; short-circuit on auto, fall through on draft/escalate. (OQ1 resolved.)
2. **Tier 2 inline vs. AgentSession** → inline bounded subprocess loop in the bridge (latency + ownership + observability). (OQ2 resolved.)
3. **`route_to_human()`** → Telegram ping to the Cuttlefish chat + cuttlefish `customer note` audit. (OQ3 resolved.)
4. **Threshold & signals** → 0.75 named constant, tunable from shadow-mode data; signal set fixed up front. (OQ4 resolved.)
5. **Subprocess auth/isolation** → mirror `_dispatch_subprocess_resolver` (argv-form, cwd, timeout, fail-safe). (OQ5 resolved.)

**Remaining for supervisor confirmation:**

1. **Phasing trigger**: Should Phase 2 (read-only auto) auto-enable after N clean shadow verdicts, or stay a manual human flag-flip? (Plan currently assumes manual `[EXTERNAL]` flip.)
2. **draft_for_human reuse**: Confirm the plan should route drafts through cuttlefish's existing `customer email draft` rather than a Telegram-only draft. (Plan assumes reuse.)
3. **Cuttlefish companion issue**: Should I file the `episode regenerate` / `customer cancel --at-period-end` issue in `yudame/cuttlefish` now, or is that out of scope for this work item?
