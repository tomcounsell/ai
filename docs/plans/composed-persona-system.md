---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1268
last_comment_id:
revision_applied: true
revision_cycle: 4
---

# Composed Persona System: Single (persona × access-level × channel) Builder

## Problem

The agent's system-prompt assembly conflates three independent axes — *who the agent is* (persona), *what it's allowed to do* (access level), and *where output is going* (channel) — into a hand-coded `if/elif` ladder duplicated across two call sites. Adding any new combination means another branch in two files; channel-specific rules leak into the working-agent prompt where they don't belong.

**Current behavior:**

- Two parallel pickers exist:
  - [`agent/sdk_client.py`](../../agent/sdk_client.py) — actually **two blocks**: L3349–L3363 (resolve persona enum) + L3382–L3419 (build `custom_system_prompt`). The `get_response_via_harness` path. Email override at L3355–L3361.
  - [`agent/session_executor.py`](../../agent/session_executor.py) L1563–L1629 — the harness-route persona resolution. Same email-vs-session-type ladder, similar but not identical.
  Both branch on `SessionType` plus a `transport == "email"` override that swaps in `project.email.persona`. They have drifted independently (issue cited only `sdk_client.py`; freshness check found the second site).
- Two prompt-builder functions exist with hard-baked behavior:
  - `load_system_prompt()` (sdk_client.py:966) — bakes in the developer persona + `WORKER_RULES` + principal context + completion criteria.
  - `load_pm_system_prompt(working_dir)` (sdk_client.py:998) — bakes in the project-manager persona, *omits* `WORKER_RULES`, appends work-vault `CLAUDE.md`. Documented invariant in its docstring at sdk_client.py:1022–1026.
  - For teammate / customer-service personas, `_load_persona_overlay_with_log()` (sdk_client.py:1787) is called directly with no rails layer at all.
- Channel awareness leaks into the working-agent prompt: persona segments (`identity.md`, `tools.md`) and the developer/PM overlays describe Telegram-specific behaviour. Concrete leakage points (verified at HEAD `5576e4dc`):
  - `config/personas/segments/identity.md` L43 (Telegram PM guide reference), L61 (empty-promises rule citing "by the time my response reaches Telegram"), L65 (drafter mention), L100 (group-chat history rule)
  - `config/personas/segments/work-patterns.md` L98 (telegram history rule), L338 (launchd reconnect mention)
  - `config/personas/segments/tools.md` L67–L80 (computer-use Electron app guidance with Telegram Desktop), L103–L131 (`valor-telegram` CLI section), L130–L132 (TOOL USAGE ONLY guard against `valor-telegram send` syntax leaking into responses), L134 (chat-history rule), L154 (Telethon mention)
  - `~/Desktop/Valor/personas/customer-service.md` — 21 telegram/email mentions (this is *expected*; customer-service is fundamentally an email persona)
  - `~/Desktop/Valor/personas/project-manager.md` (and `config/personas/project-manager.md` fallback) — L44, L220, L534 (Telegram-specific bullets)
  - `~/Desktop/Valor/personas/developer.md` — 0 telegram/email mentions (clean)
  - `~/Desktop/Valor/personas/teammate.md` — 0 telegram/email mentions (clean)

  Most of those concerns only matter at message-drafting time.
- Voice rules (banned phrases, "no empty promises", good/bad reply examples) are scattered across persona overlays and `bridge/message_drafter.py:1295` `DRAFTER_SYSTEM_PROMPT` with no single source of truth. This plan **observes** the duplication but does not consolidate it (see Risk 4 / No-Gos — voice consolidation is deferred to a follow-up plan to keep the byte-stability mitigation clean).
- `email.persona` per-project override is the only "channel changes the prompt" code path and lives inline in *both* pickers, not in a composer.

**Desired outcome:**

A single `compose_system_prompt(persona, access_level, channel=None, **kwargs)` function is the only path that produces a fully assembled agent system prompt. Both pickers collapse to: derive `(persona, access_level, channel)`, call the composer, return. Channel-specific deltas are pushed into the message drafter (the only place that legitimately needs to know whether output is going to Telegram vs email). The four existing persona overlays continue to work; observable prompt bytes for the `(developer, worker, telegram)` and `(project-manager, pm-readonly, telegram)` cells are byte-identical to today's output (preserving #1227's cache stability invariant).

## Freshness Check

**Baseline commit (issue authored against):** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Refresh commit (this revision):** `5576e4dc907247076b3154e4f73fef96ff0e92f0`
**Issue filed at:** 2026-05-04T09:18:37Z
**Refresh date:** 2026-05-06
**Disposition:** Line-number drift across all referenced sites — seven commits have touched `agent/sdk_client.py`, `agent/session_executor.py`, and adjacent files since the issue was filed. The structural shape of the picker is unchanged, but every line range needs to be re-pinned. The two-site picker finding from cycle-3 still holds and remains the most important freshness signal.

**File:line references re-verified at refresh commit:**

| Reference | Cycle-3 plan said | Verified at HEAD `5576e4dc` | Status |
|-----------|-------------------|------------------------------|--------|
| `agent/sdk_client.py` `load_persona_prompt` | L892 | L892 | unchanged |
| `agent/sdk_client.py` `load_system_prompt` | L966 | L966 | unchanged |
| `agent/sdk_client.py` `load_pm_system_prompt` | L998 | L998 | unchanged |
| `agent/sdk_client.py` `_resolve_overlay_path` | L878 | L878 | unchanged |
| `agent/sdk_client.py` `_load_persona_overlay_with_log` | L1837–1853 | L1787 (function start) | drifted up ~50 lines |
| `agent/sdk_client.py` `_resolve_persona` | (next door) | L1860 | (drifted) |
| `agent/sdk_client.py` picker — persona resolve block | L3326–L3395 (single block) | **TWO blocks**: L3349–L3363 (resolve persona enum) + L3382–L3419 (build `custom_system_prompt` based on persona) | Cycle-3 plan was wrong about it being one block |
| `agent/sdk_client.py` email-persona override | L3331 | L3355–L3361 | drifted, still inline |
| `agent/sdk_client.py` `WORKER_RULES` constant | L647 | L647 | unchanged |
| `agent/sdk_client.py` PM-cell prompt assembly comment ("Worker rules FIRST") | L994 | L994 | unchanged |
| `agent/session_executor.py` second picker site | L1430–L1486 | L1563–L1629 | drifted ~133 lines down |
| `bridge/message_drafter.py` `DRAFTER_SYSTEM_PROMPT` | L1295 | L1295 | unchanged |
| `bridge/message_drafter.py` `draft_message` signature | L1720–L1747 | L1720–L1750 | minor doc drift only |
| `bridge/message_drafter.py` `_validate_for_medium` | L381 | L381 | unchanged |
| `config/enums.py` `PersonaType` (4 members) | L25 | L25 | unchanged |
| `config/enums.py` `SessionType` (3 members) | L17 | L17 | unchanged |
| `config/personas/segments/manifest.json` (4 segments) | unchanged | unchanged | unchanged |

**Commits on main since baseline that touch referenced files:**

```
5576e4dc feat(dashboard): session-detail liveness signals (#1269)
1c50ded6 feat(skills): migrate agent-browser → BYOB per-skill (#1274)
e336277f feat(orphan-reap): cross-process orphan reaper with worker self-suicide guard (#1271)
d1aaaab2 guard slugless dev sessions with synthetic slug + CLI symmetry (#1272)
dcab49b1 fix(session-executor): require worktree to exist on disk, not just in path string (#887 follow-up)
2dfc4baf docs(persona-pm): require explicit worktrees for parallel builds + cleanup
ce44e1e4 BYOB real-Chrome MCP + macOS computer-use skill (#1256)
```

None of these commits change the *structure* of the picker, the composer functions, or the drafter — they shift line numbers and modify the surrounding harness/session-executor scaffolding. The plan's design choices (composer signature, picker collapse, drafter `medium=` extension) are unaffected. The only ground-truth change required is the line-range table above.

**Cited sibling issues/PRs re-checked:**

- #395 — CLOSED. Established persona/session-type split. Successful prior art.
- #1148 — CLOSED. Added PM persona overlay with CRITIQUE/SDLC rules + the loader-warning pattern at sdk_client.py:919–948. Composer must preserve those warnings.
- #1189 — CLOSED. Added the workflow-announcement guard (loader warning at L931–939). Composer must preserve.
- #1227 — CLOSED. Established the byte-stable PM prompt-prefix invariant for Anthropic prompt cache. Composer must preserve byte-for-byte for the PM cell.

**Active plans in `docs/plans/` overlapping this area:**

- `pm-persona-hardening.md` (#1007, In Progress) — adds three new sections to the PM overlay file. Scope is the PM overlay *content*, not the loader. **Not blocking.** Composer treats the overlay as opaque text.
- `unify-persona-vocabulary.md` (#599, Draft) — eliminates `ChatMode` enum and renames `qa_*` to `teammate_*`. Plan touches `config/enums.py` and the picker. **Coordination signal:** if `unify-persona-vocabulary.md` lands first, this plan picks up `PersonaType` cleanly; if this plan lands first, that plan rebases against the new composer call site. Both plans agree on `PersonaType` as the canonical enum — there is no semantic conflict.

**Notes:** Cycle-3 plan said the `sdk_client.py` picker was one contiguous block at L3326–L3395. At HEAD `5576e4dc` it is in fact **two blocks separated by ~20 lines of logging**:
- **Block A** (L3349–L3363): map `(SessionType, project, transport)` → `PersonaType` enum value
- **Block B** (L3382–L3419): map `(SessionType, project_mode, persona)` → `custom_system_prompt` string (calls `load_pm_system_prompt`, `_load_persona_overlay_with_log`, etc.)

The composer collapses Block B into a single `compose_system_prompt(...)` call. Block A becomes the body of `_resolve_compose_args(...)`. **Both** call sites in `sdk_client.py` and the picker in `session_executor.py:1563–1629` collapse the same way.

## Prior Art

- **#395** (CLOSED) — Multi-persona system. Established PersonaType / SessionType split and the project-manager overlay. Successful; this plan builds on it.
- **#1148** (CLOSED) — PM persona overlay with CRITIQUE/SDLC rules. Added inline loader warnings at `load_persona_prompt` (L919–948). Composer must keep those warnings.
- **#1189** (CLOSED) — Workflow-announcement rule. Added the second loader warning. Composer must keep.
- **#1227** (CLOSED) — PM prompt cache stability via `--exclude-dynamic-system-prompt-sections`. Established the byte-stable prefix property. Composer must preserve byte-for-byte.
- **#599** (DRAFT plan) — Unifying persona vocabulary; not blocking but coordinated.

## Research

External research skipped — this is an internal refactor with no new dependencies, no external library upgrades, and no API contracts changing. All mechanics (file layout, manifest, segment ordering) are repo-internal Python.

## Architectural Impact

- **New types**: `AccessLevel` enum in `config/enums.py` (or equivalent canonical declaration). Members: `WORKER` (full permissions + WORKER_RULES), `PM_READONLY` (PM mode, no WORKER_RULES, with work-vault CLAUDE.md), `TEAMMATE` (conversational, no rails), `CUSTOMER_SERVICE` (action-oriented, no code writes).
- **New function**: `compose_system_prompt(persona, access_level, channel=None, *, project=None, working_directory=None) -> str` in `agent/sdk_client.py` (or a new `agent/persona_composer.py` if extraction is preferred — the plan defaults to keeping it adjacent to the existing loaders to keep the diff focused).
- **Removed/redirected functions**: `load_system_prompt()` and `load_pm_system_prompt()` become thin wrappers that call `compose_system_prompt(...)` with the right tuple. `_load_persona_overlay_with_log()` stays as a logging adapter but now delegates the actual composition to `compose_system_prompt`.
- **Picker collapse**: both `sdk_client.py` (Block A at L3349–L3363 — persona enum resolution; Block B at L3382–L3419 — system-prompt assembly) and `session_executor.py:1563–1629` collapse to a single helper `_resolve_compose_args(session_type, project, transport, ...) -> (persona, access_level, channel)` that lives in one place and is called from both sites. Block B is replaced with one `compose_system_prompt(*args)` call per site.
- **Channel-aware drafter**: `bridge/message_drafter.py:draft_message(raw_response, session=None, *, medium="telegram", persona=None)` already accepts `medium` (today wired through to `_validate_for_medium` only — see `bridge/message_drafter.py:1720`–1747). This plan extends `medium` from validator-only into prompt selection: the drafter system prompt splits into a medium-agnostic base section plus a per-medium format section. **No new public parameter is introduced** — `medium` already exists, defaults to `"telegram"`, and is documented as the per-medium prompt/validator discriminator. The base section keeps the drafter's current voice content in place — no shared `voice.md` segment is introduced in this plan (see Risk 4 + No-Gos; consolidation is deferred to a follow-up).
- **Coupling**: Slightly *decreased*. Today the picker knows about three concerns (session type, project mode, transport). After: the picker knows only about resolving three values; the composer knows only about composition; the drafter knows only about channel format.
- **Reversibility**: High. The composer is purely additive; the new enum is small; both wrappers preserve their old signatures for safety. Rollback = revert the diff.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus one validator pair for the byte-stability regression.

**Interactions:**
- PM check-ins: 1–2 (resolve the seven open architectural questions before build; one mid-build check after the byte-stability test passes)
- Review rounds: 1 (code review focusing on the byte-stability test and picker-collapse correctness)

Communication overhead is the bottleneck: the seven architectural questions each have low coding cost but high "did we agree on the right answer" cost. Coding is mechanical once the questions are resolved.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/Desktop/Valor/personas/` overlays present (or repo fallback) | `python -c "from pathlib import Path; assert (Path.home()/'Desktop/Valor/personas/project-manager.md').exists() or (Path('config/personas/project-manager.md')).exists()"` | Composer must load real overlays; tests will skip with a warning if neither exists, but the developer must have one to validate byte stability locally |
| `config/personas/segments/manifest.json` valid JSON | `python -c "import json; json.load(open('config/personas/segments/manifest.json'))"` | Composer reads this for segment order |

## Solution

### Key Elements

- **`AccessLevel` enum**: declares the four canonical access levels. Today's implicit access levels (worker-rails-on, pm-readonly, teammate-conversational, customer-service-action) become explicit enum members. Defined in `config/enums.py` next to `PersonaType` and `SessionType`.
- **`compose_system_prompt(...)` function**: the single composer. Internally: load identity → assemble segments per manifest → append persona overlay (preserving `load_persona_prompt`'s drift-warning pattern from sdk_client.py:919–948) → apply access-level rails (prepend `WORKER_RULES` for `AccessLevel.WORKER`, append principal+completion-criteria for the same) → optionally append a minimal channel facet (TBD per Question 4) → append project extras (work-vault `CLAUDE.md` for `AccessLevel.PM_READONLY`).
- **`_resolve_compose_args(...)` helper**: collapses the two-site picker into one function. Inputs: `session_type`, `project`, `transport`, `chat_title`, `is_dm`. Outputs: `(persona, access_level, channel)`. Single source of truth for the email-persona override.
- **`load_system_prompt()` / `load_pm_system_prompt()` wrappers**: kept as thin shims for backward compatibility. They delegate to the composer with the appropriate tuple. New code is encouraged to call the composer directly.
- **Voice rules consolidation: deferred (out of scope).** Voice content (banned phrases, no-empty-promises, tone) stays in its current locations — per-persona overlays + `DRAFTER_SYSTEM_PROMPT` — for this plan. Promoting voice rules into a shared `voice.md` segment is **explicitly out of scope** because adding a new segment to `manifest.json` would change the assembled prompt bytes for the existing four cells and break Risk 1's byte-stability mitigation (see Risk 4 + No-Gos). A follow-up plan, opened after this composer ships and stabilizes, will move voice content into a shared source. This plan only introduces the composer; the drafter's voice content stays exactly where it is.
- **Medium-aware drafter**: `bridge/message_drafter.py:draft_message(...)` already accepts `medium="telegram" | "email"` (see `bridge/message_drafter.py:1720`). The drafter composes its system prompt as `BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]`. Today's behaviour is the `medium="telegram"` cell. `BASE_DRAFTER_PROMPT` retains the drafter's current voice content verbatim (no extraction to a separate file in this plan).

### Composer Signature & Call Sites (Pin-Down)

**Pinned signature:**

```python
def compose_system_prompt(
    persona: PersonaType,                  # required — one of 4 enum members
    access_level: AccessLevel,             # required — one of 4 enum members
    channel: str | None = None,            # accepted, currently unused (forward-compat; see Question 4 / Open Q1)
    *,
    project: dict | None = None,           # for project.email.persona override + work-vault CLAUDE.md
    working_directory: str | None = None,  # required when access_level == PM_READONLY (raises ValueError otherwise)
) -> str:
```

**The four entry points that funnel through it:**

| # | Today's call site | Today's behavior | Composer call after refactor |
|---|-------------------|------------------|-------------------------------|
| 1 | `agent/sdk_client.py:966` `load_system_prompt()` (kept as wrapper) | Builds developer + WORKER_RULES + principal + completion criteria | `compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)` |
| 2 | `agent/sdk_client.py:998` `load_pm_system_prompt(working_dir)` (kept as wrapper) | Builds project-manager + work-vault CLAUDE.md, no WORKER_RULES | `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, working_directory=working_dir)` |
| 3a | `agent/sdk_client.py:3382–3419` Block B (`get_response_via_harness` path) | Branches by `_session_type` / `project_mode` / `persona` to call one of `load_pm_system_prompt`, `_load_persona_overlay_with_log` | Replaced with: `args = _resolve_compose_args(_session_type, project, _session_extra_context.get("transport"), chat_title, is_dm); custom_system_prompt = compose_system_prompt(*args, project=project, working_directory=working_dir)` |
| 3b | `agent/session_executor.py:1563–1629` (harness-route persona resolution) | Builds `_pm_system_prompt: str | None` via the parallel ladder (PM session → `load_pm_system_prompt`; email/teammate → `_load_persona_overlay_with_log`) | Same `_resolve_compose_args(...)` + `compose_system_prompt(...)` call. Edge case: this site can produce `None` (PM persona load failure → harness runs without overlay). The composer never returns `None` — failure modes raise. The call site catches and falls back to `None` to preserve today's "warn and degrade" behavior at log line `[pm-persona-missing]`. |

(There is also `bridge/message_drafter.py` `draft_message` — a fifth funnel for the *drafter's* system prompt — but it composes a different prompt for a different model, so it does **not** call `compose_system_prompt`. It instead calls a sibling helper `_compose_drafter_prompt(medium)` introduced in this plan. See Drafter prompt assembly below.)

**Net result:** four working-agent call sites collapse to two function bodies (the picker helper + the composer), called from three places (the two wrappers + the two pickers, where each picker is one location-not-two after collapse). Drafter is a parallel cleanup with its own helper, not a sub-call of the agent composer.

### Flow

**Working-agent prompt assembly:**

Session arrives at executor → `_resolve_compose_args(session_type, project, transport)` returns `(persona, access_level, channel)` → `compose_system_prompt(persona, access_level, channel, project=project, working_directory=working_dir)` returns the full prompt string → string passed to `claude -p` via `--append-system-prompt`.

**Drafter prompt assembly:**

Worker emits agent output → `bridge/message_drafter.py:draft_message(raw_response, session=None, *, medium="telegram", persona=None)` (the existing public entry point at `bridge/message_drafter.py:1720`) → drafter composes `BASE_DRAFTER_PROMPT + MEDIUM_RULES["telegram"]` (or `"email"`, etc.) → calls Haiku via `client.messages.create(system=composed_prompt, ...)`.

**Note on naming:** the function is `draft_message` (not `format_for_chat`) and the parameter is `medium` (not `channel`). The plan uses `medium` consistently because (a) that's the existing parameter name on `draft_message`, (b) `_validate_for_medium(text, medium)` already exists at `bridge/message_drafter.py:381`, and (c) renaming `medium` to `channel` is out of scope. The composer can still be named with `channel` internally because no working-agent prompt cell uses it today (Question 4 / Open Question 1) — but the drafter's public surface stays as `medium=`.

### Channel-Awareness Decision (concrete content move list)

The issue's directive: "push channel-specific deltas into the drafter; minimal channel facet remains in the working agent only if a concrete need is identified." This plan resolves that into the following content-move ledger. **Voice consolidation (banned phrases, tone rules) is deferred per Risk 4** — what follows is structural channel-awareness only, not voice.

| Content block | Lives today in | Move target after this plan | Rationale |
|---------------|----------------|------------------------------|-----------|
| `valor-telegram` CLI invocation guidance (read/send commands, group history search) | `config/personas/segments/tools.md` L103–L131 | **Stay in `tools.md`** | This is a *tool the agent calls*, not a *channel the agent outputs to*. The agent uses `valor-telegram read` to look up history regardless of where the response goes. Treat as tool documentation, not channel awareness. |
| `valor-telegram send` syntax warning ("never include CLI syntax in responses") | `config/personas/segments/tools.md` L130–L132 | **Stay in `tools.md`** | Tool-misuse guard, lives next to the tool. Same logic. |
| Telegram Desktop in `computer-use` Electron app list | `config/personas/segments/tools.md` L67–L80 | **Stay in `tools.md`** | One of many Electron apps in the same paragraph; not channel-specific. |
| "By the time my response reaches Telegram, my session is OVER" (empty-promises rule) | `identity.md` L61 | **Voice content — DEFER** to follow-up voice plan. Stays in `identity.md` for this plan. | This is a *voice* rule (no empty promises), not a channel format rule. Risk 4 says voice consolidation is out of scope. |
| "Long agent outputs are drafted before sending to Telegram. The drafter…" | `identity.md` L65 (~next 5 lines) | **Stay in `identity.md`** for this plan; flag for follow-up voice plan | Same — explains the drafter to the agent so it does not duplicate drafting work. Cross-cutting concern; do not move in this plan. |
| Group-chat history rule ("search Telegram before asking") | `identity.md` L100, `work-patterns.md` L98, `tools.md` L134 | **Stay in segments** | Behavior rule about asking-vs-searching; applies regardless of output channel. |
| Launchd reconnect mention | `work-patterns.md` L338 | **Stay** | Self-healing context, not channel-format. |
| PM overlay "Replying to messages, reading state, sending Telegram messages" (L44) | `~/Desktop/Valor/personas/project-manager.md` | **Stay** in PM overlay | PM overlay is private/iCloud-synced and out of repo scope. Touching it bursts byte-stability for the PM cell (Risk 1). Defer with the rest of voice consolidation. |
| PM overlay "send Telegram update before pausing" (L534) | `~/Desktop/Valor/personas/project-manager.md` | **Stay** | Same reason — and this is a PM workflow rule, not a channel-format rule. |
| Telegram-length / chat-format rules (`• ` bullets, `>>` question prefix, FORMAT RULES #1–#4) | `bridge/message_drafter.py:1295` `DRAFTER_SYSTEM_PROMPT` (FORMAT RULES section) | **Move into `MEDIUM_RULES["telegram"]`** in drafter | This is *the* example of channel-format content. Plain candidate for the drafter's per-medium split. |
| Email-specific format rules (no markdown, no inline code, plain conversational email) | Spread across `customer-service.md` overlay (L83, L148, L150 in customer-service overlay; nothing in the drafter today for email) | **Add a stub `MEDIUM_RULES["email"]`** in the drafter; **leave** the customer-service overlay alone (overlay describes *the persona*, drafter describes *the medium*) | Surfaces the deferred work cleanly: email medium gets a stub today, customer-service overlay keeps describing the persona. |
| SDLC stage progress / link footer | `bridge/message_drafter.py:1295` (FORMAT RULES section #4) | **Move into `MEDIUM_RULES["telegram"]`** | Telegram-specific rendering conventions that don't apply to email. |
| Structured-output `tool_use` schema (`response`, `expectations`, `context_summary`) | `bridge/message_drafter.py` (separate from `DRAFTER_SYSTEM_PROMPT`) | **No change — stays shared across mediums** | The schema is delivery-agnostic; the email medium would still want all three fields. |

**Summary of structural moves:**

1. **Working-agent segments + overlays:** **no content moves in this plan.** The structural change is the composer signature. Segments and overlays continue to ship Telegram-leaning content; that content's eventual relocation is a Q2/voice-consolidation follow-up.
2. **Drafter:** `DRAFTER_SYSTEM_PROMPT` splits into `BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]`. Telegram-specific FORMAT RULES move into `MEDIUM_RULES["telegram"]`. Email gets a stub `MEDIUM_RULES["email"]` (initially identical-ish to telegram minus telegram-only formatting; concrete email format rules are an explicit follow-up). The base prompt retains all *voice* content verbatim.
3. **Working-agent composer `channel` parameter:** accepted, ignored. Forward-compat for a future facet but unused today (see Open Question 1).

This ledger is the build phase's source of truth for the content-move scope.

### AGENT-VOICE.md Decision

The issue references fazm's [`AGENT-VOICE.md`](https://github.com/mediar-ai/fazm/blob/main/inbox/skill/AGENT-VOICE.md) — a single canonical voice file that all channel-specific skills reference. The user request asks: **do we ship a single voice doc, and where does it live?**

**Decision for this plan: NO, not in this plan.** Defer to a follow-up voice-consolidation plan after this composer ships.

**Reasoning:**

1. Adding a single voice source — whether as a new `voice.md` segment in `manifest.json`, a `config/personas/voices/{persona}.md` per-persona file, or a top-level `AGENT-VOICE.md` — **changes the assembled prompt bytes** for the existing four cells the moment any segment-list reordering or new content insertion happens. That breaks Risk 1's byte-stability mitigation, which is *the* invariant this plan must preserve to avoid busting #1227's prompt cache.
2. Voice consolidation is a quality-and-content concern, not a structural one. The composer refactor is a pure structural cleanup. Coupling them risks two simultaneously-changing variables in a refactor whose entire purpose is to be a no-observable-behavior-change cleanup.
3. The drafter `BASE_DRAFTER_PROMPT` keeps today's voice content verbatim. The composer reads voice content from where it lives today (segments + overlays + drafter prompt). No new file is introduced. The four current voice locations stay intact.

**What the follow-up plan will look like (sketch, not in scope here):**

- Single source: `config/personas/voice.md` referenced by both the composer (new segment in `manifest.json`, with explicit one-time cache-bust per #1227) and the drafter (read at module load, concatenated into `BASE_DRAFTER_PROMPT`).
- Content scope: banned phrases, "no empty promises" rule, tone guidance. Good/bad reply *examples* are a separate quality plan after voice consolidation lands.
- Cache-bust strategy for #1227: deploy during a quiet window, accept one cold-cache hit per machine per session-bucket.

**Decision recorded for traceability:** the plan documents this decision so the follow-up plan has a clear starting point. If the reviewer wants the voice plan filed *now* as a placeholder issue, that is a build-phase choice (see Open Question 3 below). The default in this plan is to file the placeholder issue at PR-merge time so it can reference the actual landed composer.

### Technical Approach

- **Where the composer lives**: keep it adjacent to existing loaders in `agent/sdk_client.py`, alongside `load_persona_prompt`/`load_system_prompt`/`load_pm_system_prompt`. Extracting to a new module is a follow-up if the file gets too large.
- **Question resolutions baked in** (the seven open questions from the issue, resolved here):

  1. **Access-level vs session-type**: `AccessLevel` is **orthogonal** to `SessionType`. `SessionType` is the AgentSession discriminator (decides queueing, child-session shape, output handler); `AccessLevel` is the prompt-rails layer. The mapping today happens to be 1:1 (`pm` → `PM_READONLY`, `dev` → `WORKER`, `teammate` → `TEAMMATE`), but they live separately so future per-project rails (e.g., a teammate session in customer-service mode) don't need new SessionType members. The resolver `_resolve_compose_args` encodes the mapping.

  2. **Voice doc location: deferred to a follow-up plan.** Voice rules (banned phrases, "no empty promises", tone) stay in their current locations — distributed across per-persona overlays and `bridge/message_drafter.py:1295` `DRAFTER_SYSTEM_PROMPT`. Consolidating them into a shared `voice.md` segment would require adding a new entry to `config/personas/segments/manifest.json`, which would change the assembled prompt bytes for the existing four cells and break Risk 1's byte-stability mitigation (see Risk 4 + No-Gos). The drafter and the working-agent composer continue to read voice content from where it lives today; no shared file is introduced. A follow-up plan will move voice content into a single source after this composer ships and the byte-stability test stabilizes — that plan can negotiate the cache bust on its own terms.

  3. **Medium extraction into the drafter**: `bridge/message_drafter.py` composes its system prompt **at module load** as `BASE + MEDIUM_RULES[medium]`. The structured-output `tool_use` schema is unchanged — it stays shared across mediums. The `medium` parameter on `draft_message` already exists (defaults to `"telegram"`); only the future email-medium case needs to pass `medium="email"` and that call site is also already wired. `BASE_DRAFTER_PROMPT` keeps the drafter's existing voice content verbatim — voice consolidation is deferred (see Question 2). The plan uses `medium` (not `channel`) on the drafter's public surface because that is the existing parameter name; renaming is out of scope.

  4. **Minimum channel-awareness for the working agent**: **None.** The working agent does not need channel context in its system prompt. Reachability/emoji-react decisions are made by the agent based on tool output (e.g., reading recent Telegram chat state via `valor-telegram read`), not encoded in the prompt. This drops the `channel=` parameter from the composer's required signature; it remains as an optional facet **only if a concrete need is proven during build** (Open Question 1 below pins this).

  5. **Composition order and overrides**: **strict additive layering, no redaction.** Order is fixed: `WORKER_RULES (if WORKER) → identity → work-patterns → tools → private-tag → persona overlay → principal context (if WORKER) → completion criteria (if WORKER) → work-vault CLAUDE.md (if PM_READONLY)`. The order matches today's `manifest.json` exactly; **no new segments are added** in this plan (see Question 2 — voice.md is deferred). If two layers contradict, the source documents must be fixed; the composer does not silently mediate. A startup lint pass (Question 7) detects contradictions.

  6. **Migration path**: the four existing overlays continue to load via `_resolve_overlay_path` unchanged. The migration is internal to the composer — wrappers preserve their public signatures. Byte-stability test (Success Criterion below) asserts the `(developer, WORKER, None)` and `(project-manager, PM_READONLY, None)` cells produce **byte-identical** output to `load_system_prompt()` and `load_pm_system_prompt(work_dir)` from main today.

  7. **Runtime preview / lint**: a one-off `pytest` test (`test_compose_system_prompt_invariants`) asserts at test time: (a) every cell composes without exception, (b) PM cell stays under 80K chars (cache budget), (c) no `{{identity.*}}` markers remain in the output, (d) `WORKER_RULES` precedes the persona overlay text in the `WORKER` cell. **No runtime cost** — this runs in CI, not on every compose. The validators in `_load_persona_overlay_with_log` and `load_persona_prompt` (the existing CRITIQUE/workflow-announcement substring checks) stay where they are; they catch overlay drift, not composer drift.

- **Picker collapse**: extract `_resolve_compose_args` into a private helper at `agent/sdk_client.py` near `_resolve_persona` (L1860). Both call sites (sdk_client.py:3349–3363 + 3382–3419 and session_executor.py:1563–1629) call this helper. The helper encapsulates the email-persona override (`if transport == "email" and project.email.persona: persona = ...`). **Note:** `session_executor.py` also keeps its `[persona-load-failed]` ERROR log at L1620–L1629 (when explicit `email.persona` is requested but no overlay loads); the composer does not subsume this — the call site still catches and logs.
- **Backward compatibility**: `load_system_prompt()` and `load_pm_system_prompt(work_dir)` remain as wrappers (one-line implementations that call the composer). All existing call sites continue to work without change. New code path: `compose_system_prompt(...)` direct.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_load_persona_overlay_with_log` already has structured exception handling for missing overlays (sdk_client.py:1837–1853). The composer must NOT swallow exceptions silently — it raises `FileNotFoundError` for missing required overlays, matching `load_persona_prompt`'s behavior at sdk_client.py:960–963. Tests assert that the composer raises (not returns empty string) for unknown personas with no fallback.
- [ ] Add tests asserting that a missing required segment listed in `manifest.json` produces a clear error at startup (preserves today's behavior; the composer does not swallow segment-load failures).

### Empty/Invalid Input Handling

- [ ] `compose_system_prompt(persona="invalid", ...)` raises `ValueError` with a list of valid persona names.
- [ ] `compose_system_prompt(persona=PersonaType.DEVELOPER, access_level="not-an-AccessLevel", ...)` raises `TypeError`.
- [ ] `compose_system_prompt(persona=..., access_level=PM_READONLY, working_directory=None)` raises `ValueError("PM_READONLY requires working_directory")`. PM_READONLY without a working dir is a programmer error today (the wrapper crashes at `Path(None)`).
- [ ] Empty / whitespace-only overlay file: composer logs a WARNING and proceeds (matches today's behavior; overlay files always contain content but tests assert the failure mode).

### Error State Rendering

- [ ] If a required segment listed in `manifest.json` is missing the composer raises at startup; the worker logs `[persona-compose-failed]` and the session refuses to start (no silent fallback to a segment-less prompt). Test asserts the log line is emitted.
- [ ] If the picker resolves to an unknown `(persona, access_level)` pair, the composer raises before any IO; test asserts no partial prompt is returned.

## Test Impact

Audit performed at refresh commit `5576e4dc` against the actual test files in `tests/unit/`. Specific lines pinned where they affect the test mapping.

- [ ] `tests/unit/test_persona_loading.py` (24K, last touched May 2) — UPDATE: `load_system_prompt` and `load_pm_system_prompt` now delegate to `compose_system_prompt`; existing tests at L164–L201 (`load_persona_prompt` calls), L298–L312 (`load_system_prompt`/`load_pm_system_prompt` regressions), and the WORKER_RULES + persona-content assertions still pass because the wrappers preserve byte output. **Add** a new test `test_load_system_prompt_byte_stable_through_composer` asserting `load_system_prompt() == compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)`.
- [ ] `tests/unit/test_sdk_client_sdlc.py` (22K, last touched Apr 12) — UPDATE: `WORKER_RULES` constant tests at L37–L71 are unchanged (the constant itself does not move). The `load_system_prompt` assertions are rewritten to also assert against `compose_system_prompt(persona=DEVELOPER, access_level=WORKER)` and produce identical output. **Add** a regression: `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, working_directory="...")` does NOT contain `WORKER_RULES` substring (preserves the `load_pm_system_prompt` invariant from sdk_client.py:1023).
- [ ] `tests/unit/test_message_drafter.py` (85K, last touched May 5) — UPDATE: drafter system prompt is now `BASE + MEDIUM_RULES["telegram"]`. Existing tests should pass unchanged because the default `medium="telegram"` produces the same text. The two assertions at L1574–L1587 that import `DRAFTER_SYSTEM_PROMPT` directly need to be updated: either keep them as-is (the symbol is exported and the test verifies the *current* string still includes the substrings) or rewrite to import via `_compose_drafter_prompt("telegram")`. **Add** a new test asserting `draft_message(raw_response, session=None, medium="email")` produces a system prompt that does NOT include the `• ` bullet rule from FORMAT RULES #4 (Telegram-specific). The tested function is `draft_message` at `bridge/message_drafter.py:1720`; signature `async def draft_message(raw_response: str, session=None, *, medium: str = "telegram", persona: str | None = None) -> MessageDraft`.
- [ ] `tests/unit/test_drafter_validators.py` (8.7K, last touched Apr 20) — UPDATE: tests at L194 (`telegram` medium), L214–L217 (`email` medium) already pass `medium=` explicitly. No drafter signature changes are required. The plan reuses the existing `_validate_for_medium` validator unchanged.
- [ ] `tests/unit/test_pm_persona_guards.py` (9.9K, last touched May 2) — **no change**. The PM overlay loader-warning tests at sdk_client.py:919–948 are preserved; they're inside `load_persona_prompt`, which stays as the segment-and-overlay assembler.
- [ ] `tests/unit/test_message_drafter_chat_log.py` (5.7K), `tests/unit/test_message_drafter_linkify.py` (3.7K) — UPDATE only if they import `DRAFTER_SYSTEM_PROMPT` directly. Grep on refresh commit shows no such imports → **no change**.
- [ ] `tests/unit/test_agent_session_scheduler_persona.py` (2.4K, last touched Apr 9) — REVIEW. Tests persona resolution by scheduler. Should still pass; scheduler doesn't use the composer directly. Confirm during build.
- [ ] `tests/unit/test_pm_session_factory.py`, `tests/unit/test_pm_channels.py`, `tests/unit/test_config_driven_routing.py`, `tests/unit/test_routing_mode.py` — these contain `persona`/`PersonaType` references but operate at the session-factory/router layer above the composer. Composer is invoked downstream of these tests' subjects → **no change** unless they assert specific picker branches.
- [ ] **NEW** `tests/unit/test_compose_system_prompt.py` — create. Covers:
  1. **Byte-stability** for `(DEVELOPER, WORKER)` and `(PROJECT_MANAGER, PM_READONLY)` cells against the per-machine fixtures from Step 1 (Risk 1 strategy (c)). SKIPs on machines without a baseline.
  2. **Per-cell composition** — one test per `(PersonaType, AccessLevel)` cell; the cells that don't exist in `_resolve_compose_args` mapping raise `ValueError` with a useful message.
  3. **Executable invariants** (per Q7): `compose_system_prompt(PROJECT_MANAGER, PM_READONLY, working_directory=W)` is < 80K chars; no `{{identity.*}}` markers remain in any cell's output; `WORKER_RULES` precedes the persona overlay text in the `WORKER` cell; PM cell does NOT contain `WORKER_RULES`; channel parameter is accepted as `None` and as any string without raising.
  4. **Concrete acceptance examples** (executable, per memory note "Acceptance criteria must be executable"):
     - `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, channel="email", working_directory=W)` returns a prompt that **contains** the project-manager overlay text and the work-vault `CLAUDE.md` content (when present), and does **not** contain `WORKER_RULES`.
     - `compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER, channel="telegram")` returns a prompt that **starts with** `WORKER_RULES` (before the `\n\n---\n\n` separator) and contains the developer overlay text after the separator.
     - `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY)` (no `working_directory`) raises `ValueError` whose message names `working_directory` and `PM_READONLY`.
     - `compose_system_prompt(PersonaType.DEVELOPER, "not-an-AccessLevel")` raises `TypeError`.
     - `compose_system_prompt(persona="invalid-string", access_level=AccessLevel.WORKER)` raises `ValueError` listing valid persona names.
- [ ] **NEW** `tests/unit/test_resolve_compose_args.py` — create. Parametrized over the input cells:
  | session_type | project_mode | transport | project.email.persona | → expected (persona, access_level, channel) |
  |---|---|---|---|---|
  | PM | pm | telegram | (any) | `(PROJECT_MANAGER, PM_READONLY, "telegram")` |
  | PM | dev | telegram | (any) | `(PROJECT_MANAGER, PM_READONLY, "telegram")` |
  | TEAMMATE | dev | telegram | (none) | `(TEAMMATE, TEAMMATE, "telegram")` |
  | TEAMMATE | dev | email | "customer-service" | `(CUSTOMER_SERVICE, CUSTOMER_SERVICE, "email")` |
  | TEAMMATE | dev | email | (none/missing) | `(TEAMMATE, TEAMMATE, "email")` |
  | TEAMMATE | dev | email | "teammate" | `(TEAMMATE, TEAMMATE, "email")` |
  | DEV | dev | telegram | (any) | `(DEVELOPER, WORKER, "telegram")` |
  | DEV | dev | email | "customer-service" | `(CUSTOMER_SERVICE, CUSTOMER_SERVICE, "email")` (or per Question 6 — confirm during build) |
  | DEV | pm | telegram | (any) | `(PROJECT_MANAGER, PM_READONLY, "telegram")` (project_mode=pm overrides) |

  Replaces inline branch-by-branch testing of the two pickers.

No existing integration tests reference the prompt-byte content directly, so no integration test impact. The byte-stability invariant lives in unit tests.

## Rabbit Holes

- **A new runtime permission system tied to AccessLevel.** The hook system at `agent/hooks/pre_tool_use.py` already enforces PM read-only restrictions via `SESSION_TYPE`. AccessLevel is **prompt-only**; do not refactor the hook layer to use it. Out of scope.
- **Refactoring `bridge/message_drafter.py` beyond the channel split.** The drafter is 1860 lines with substantial format logic, structured-output schemas, fallback chains, and chat-log formatting. The plan touches ONLY the system-prompt composition; everything else stays.
- **Moving `WORKER_RULES` content into a segment file.** The current constant is short (13 lines) and lives next to the composer. Moving it to `config/personas/rails/worker.md` adds file IO without value. Defer.
- **Voice rules consolidation into a shared `voice.md` segment.** Promoting banned-phrases / tone / no-empty-promises rules into a single source is a worthy follow-up but is **deferred** because it would alter assembled prompt bytes for the existing four cells and break Risk 1's byte-stability mitigation. A separate plan handles it after this composer ships. Out of scope.
- **A new "voice doc" with good/bad reply *examples*.** Even if voice consolidation were in scope, good-vs-bad example pairs are a separate quality concern that needs independent validation. Out of scope for this refactor.
- **Renaming `_session_type` / picker variables.** Cosmetic. Stays out.

## Risks

### Risk 1: Byte-stability regression for the PM cell breaks #1227's prompt cache

**Impact:** PM session TTFT regresses from <90s (warm) to 15–20min (cold). Catastrophic UX hit.

**Machine-stability problem:** The composed prompt embeds machine-specific values that vary across developer machines and bridge machines:
- `working_directory` (PM cell only) — embedded as a path inside the work-vault `CLAUDE.md` content; the path differs by machine (`/Users/tomcounsell/...` vs `/Users/valorengels/...`).
- `{{identity.*}}` substitutions — `config/identity.json` ships repo-default values, but `~/Desktop/Valor/identity.json` shallow-merges per-machine overrides via `load_identity()` at `agent/sdk_client.py:785`. A bridge machine with no override produces different bytes than a dev machine with one.
- Work-vault `CLAUDE.md` content (PM cell only) — read from `Path(working_directory)/"CLAUDE.md"` at `agent/sdk_client.py:1037`; content varies per project and per machine.

A single `tests/fixtures/pm_system_prompt_baseline.txt` snapshot would be byte-stable only on the machine that generated it.

**Mitigation strategy: per-machine snapshot, asserted on the local machine (strategy (c) in cycle-2 critique).** Rationale: #1227's prompt-cache invariant is *itself* per-machine (Anthropic's cache TTL is per-machine, per-session, and the `--exclude-dynamic-system-prompt-sections` flag already handles cwd/env/git stripping at the `claude -p` boundary). The fixture's job is "the composer output equals what `load_system_prompt()` / `load_pm_system_prompt(work_dir)` returned on **this** machine **before** the refactor" — not "the same bytes on every machine ever." This is the only strategy that preserves Risk 1's actual invariant (cache stability across consecutive sessions on the same machine) without false positives or false negatives.

Concretely:
- Fixtures live at `tests/fixtures/{machine_name}/dev_system_prompt_baseline.txt` and `tests/fixtures/{machine_name}/pm_system_prompt_baseline.txt`, where `machine_name` is the slug from `socket.gethostname()` (or equivalent stable identifier — to be picked at build time, but `gethostname()` is the leading candidate because it is what Anthropic's prompt cache keys on de facto via the API connection).
- The byte-stability test reads the local-machine fixture only; on machines without a fixture, the test SKIPs with a clear message (`"no baseline for hostname '{name}'; run scripts/capture_persona_baseline.py to record one"`). Skipping is acceptable because cache stability for this plan's purposes is about *consecutive* sessions on a *single* machine — a freshly-introduced machine has no prior cache to break.
- A capture script `scripts/capture_persona_baseline.py` regenerates the local-machine fixture from `load_system_prompt()` / `load_pm_system_prompt(work_dir)` *before* the composer ships, and the dev/CI machine commits its own fixture. The build cannot proceed on a machine until it has captured its own baseline.
- Strategies considered and rejected: (a) **token normalization** (replace machine-specific paths/values with sentinels before snapshot) was rejected because the same normalization would have to be applied at runtime to validate the cache prefix, which means the runtime would no longer pass byte-identical bytes to `claude -p` — defeating the entire #1227 invariant. (b) **structural-equality assertions** (compare segment-list ordering, not bytes) was rejected because the prompt cache hits on byte equality, not structural equality — a structural test would pass while the cache silently broke.

**Build gate:** the build cannot proceed until the byte-stability test passes on the local machine. The fixture(s) committed by the dev are sufficient for local CI; bridge-machine fixtures are captured during deploy and not blockers for the PR.

The mitigation also includes a check that the `--exclude-dynamic-system-prompt-sections` integration is unaffected (the runtime prompt the composer hands to `get_response_via_harness` must still trigger that flag's stripping behavior; tested by inspecting argv in a unit test).

### Risk 2: Two-site picker drift returns

**Impact:** A future change to the email override is added to one picker site and not the other; behaviour diverges between the `get_response_via_harness` path and the `session_executor` harness path.
**Mitigation:** the `_resolve_compose_args` helper is the single source of truth; both call sites import it. A unit test (`test_resolve_compose_args.py`) is the primary regression. Optional: a grep-based test asserts no `if _session_type == SessionType` ladder remains in either file outside the helper.

### Risk 3: Drafter format rules break when the channel split lands

**Impact:** Drafter output silently changes — bullets vs prose, "no empty promises" warnings change, format regressions ship to production.
**Mitigation:** `BASE_DRAFTER_PROMPT` keeps today's `DRAFTER_SYSTEM_PROMPT` text verbatim (minus the medium-specific format rules that move into `MEDIUM_RULES["telegram"]`). The split is purely structural — concatenating `BASE + MEDIUM_RULES["telegram"]` reproduces today's prompt byte-for-byte. Tests in `test_drafter_validators.py` already cover format invariants — they must remain green. (Note: voice consolidation is **not** part of this plan; see Risk 4 and No-Gos.)

### Risk 4: A new shared voice segment would change prompt bytes for existing cells

**Impact:** If `voice.md` were added to `manifest.json` in this plan, the `(DEVELOPER, WORKER)` cell would no longer be byte-stable against today's `load_system_prompt()` because a new segment would be inserted into the assembled prompt. This would break Risk 1's mitigation.
**Mitigation:** **Voice consolidation is out of scope for this plan.** This plan introduces the composer with byte-stability for the existing four cells; voice content stays in its current locations (per-persona overlays + `DRAFTER_SYSTEM_PROMPT`). A follow-up plan, opened only after this one ships and stabilizes, will move voice content into a shared source — that plan can negotiate the one-time cache bust on its own terms with #1227's invariant in mind. This keeps Risk 1's mitigation simple and avoids two simultaneously-changing variables.

## Race Conditions

No race conditions identified — prompt composition is synchronous, single-threaded, and runs once per session at startup. All file IO is read-only against immutable-during-session files. The composer is called from both `sdk_client.py:get_response_via_harness` and `session_executor.py` execute paths, but each call is independent and there is no shared mutable state.

## No-Gos (Out of Scope)

- AccessLevel as a runtime hook-enforcement system (see Rabbit Holes).
- Promoting voice rules to a shared `voice.md` segment in this plan (deferred to a follow-up plan to keep Risk 1's byte-stability mitigation clean — see Risk 4).
- Adding good/bad reply *examples* in any voice file (rules only would still be a separate quality plan; examples definitely separate).
- **Shipping a fazm-style `AGENT-VOICE.md`** in this plan. Decision recorded in the AGENT-VOICE.md Decision section above. The follow-up voice-consolidation plan handles single-source voice content; this plan only introduces the composer.
- **Moving Telegram-leaning content out of `config/personas/segments/identity.md` / `tools.md` / `work-patterns.md`** in this plan. The Channel-Awareness Decision ledger above is explicit: those segments stay as-is for byte-stability. The drafter is where the channel split lands.
- **Touching `~/Desktop/Valor/personas/*.md` overlay files**. They are iCloud-synced human-edited content; this plan only changes the loader/composer.
- Refactoring the drafter beyond system-prompt composition (no schema changes, no fallback-chain changes, no chat-log-formatting changes — only `DRAFTER_SYSTEM_PROMPT` → `BASE + MEDIUM_RULES[medium]`).
- Adding new persona overlays (`developer`, `project-manager`, `teammate`, `customer-service` are the only four; new ones are a separate plan).
- **Adding non-`None` channel awareness in the working-agent prompt in this plan**. The `channel=` parameter is accepted but unused; promoting any content into a channel-conditioned facet is a separate plan triggered by a concrete need.
- Runtime composer caching / memoization (each compose is < 50ms; not worth a cache layer).

## Update System

**Minor update-system change required** — the per-machine byte-stability fixture (Risk 1) means each machine running the test suite needs its own baseline captured locally:

- New script: `scripts/capture_persona_baseline.py` — captures `tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt` from `load_system_prompt()` / `load_pm_system_prompt(work_dir)` on the current machine. Idempotent; safe to re-run.
- The byte-stability test in `tests/unit/test_compose_system_prompt.py` SKIPs (not FAILs) when no baseline exists for the current machine — so a freshly-updated bridge machine without a baseline does not block deploy. Bridge machines do not run the unit test suite during normal operation; this is a dev-machine concern.
- `scripts/remote-update.sh` is unchanged. The `update` skill does not need to invoke the capture script automatically — developers running the test suite on a new machine for the first time will hit the SKIP and follow the message to capture their baseline.
- No new dependencies, no new config files. Bridge machines pick up the composer code on the next normal `git pull` like any other internal refactor.
- The fixtures in `tests/fixtures/{machine_name}/` are committed per machine. Each developer/CI host commits its own subdirectory. This adds a small N-machines-of-fixtures cost to the repo but keeps the byte-stability test honest (Risk 1).

## Agent Integration

No agent integration required — this is internal to the agent prompt-composition layer. The agent receives the composed prompt via `claude -p --append-system-prompt`; nothing the agent calls (no MCP servers, no CLI tools, no `pyproject.toml` scripts) changes. Tests in `tests/unit/` cover the composer directly without bridge integration.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/composed-persona-system.md` describing: the composer signature, the (persona × access-level) matrix, where to add a new access-level, the byte-stability invariant, and how the drafter's channel split interacts. Reference the seven resolved questions and their answers. Include an explicit note that voice consolidation is deferred and a follow-up plan will be filed.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/pm-dev-session-architecture.md` to reference the composer as the prompt source for both PM and Dev sessions (replaces the implicit pointer to `load_pm_system_prompt`).

### External Documentation Site

No external docs site for this repo — skip.

### Inline Documentation

- [ ] Docstring on `compose_system_prompt` describes all parameters, the invariant ordering, and the byte-stability guarantee for the existing four cells.
- [ ] Update docstring on `load_system_prompt` and `load_pm_system_prompt` to note they delegate to the composer.
- [ ] Comment block above `_resolve_compose_args` enumerates every (input → output) cell mapping for grep-able reference.

## Success Criteria

Each criterion is **executable** — written as an assertion the validator can run, not a vague description.

- [ ] `compose_system_prompt(persona, access_level, channel=None, **kwargs)` exists at `agent/sdk_client.py` (or `agent/persona_composer.py` if extracted) and is the only path that produces a fully assembled agent system prompt; `load_system_prompt` and `load_pm_system_prompt` are thin wrappers (≤ 5 lines each, single delegation call).
- [ ] `AccessLevel` enum is defined in `config/enums.py` with members `WORKER`, `PM_READONLY`, `TEAMMATE`, `CUSTOMER_SERVICE`. Verified by `python -c "from config.enums import AccessLevel; assert {e.name for e in AccessLevel} == {'WORKER', 'PM_READONLY', 'TEAMMATE', 'CUSTOMER_SERVICE'}"`.
- [ ] `_resolve_compose_args(session_type, project, transport, chat_title=None, is_dm=False)` helper exists and is the **only** branch ladder mapping `SessionType + project + transport → (persona, access_level, channel)`. Both `agent/sdk_client.py` (the prompt-assembly site, formerly L3382–L3419) and `agent/session_executor.py` (the harness-route site, formerly L1563–L1629) call it. Verified by grep (see Verification table — no leftover ladder).
- [ ] **Byte-stability (per-machine)**: on the local machine, fixture-equality holds:
    - `compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)` is byte-identical to `load_system_prompt()` from main.
    - `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, working_directory=W)` is byte-identical to `load_pm_system_prompt(W)` from main.
    - Asserted via fixtures at `tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt`. Test SKIPs (not FAILs) on machines without a captured baseline (clear pointer to `scripts/capture_persona_baseline.py`).
- [ ] **Concrete persona × access × channel cells** (executable assertions, in `tests/unit/test_compose_system_prompt.py`):
    - `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, channel="email", working_directory=W)` returns a string that **contains** the project-manager overlay text and (if `Path(W)/CLAUDE.md` exists) the work-vault CLAUDE.md text, and **does not contain** `WORKER_RULES`.
    - `compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER, channel="telegram")` returns a string that **starts with** `WORKER_RULES` (before the `\n\n---\n\n` separator) and contains the developer overlay text after the separator.
    - `compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY)` (no `working_directory=`) raises `ValueError` whose message names both `working_directory` and `PM_READONLY`.
    - `compose_system_prompt(PersonaType.DEVELOPER, "not-an-AccessLevel")` raises `TypeError`.
    - `compose_system_prompt(persona="invalid-string", access_level=AccessLevel.WORKER)` raises `ValueError` listing valid persona names.
- [ ] Drafter `medium` parameter (already exists on `draft_message` at `bridge/message_drafter.py:1720`) now drives prompt selection. Verified by `_compose_drafter_prompt("telegram")` reproducing today's `DRAFTER_SYSTEM_PROMPT` byte-for-byte (snapshot test using captured-from-main string), and `_compose_drafter_prompt("email") != _compose_drafter_prompt("telegram")` (substantive difference).
- [ ] `email.persona` per-project override flows through `_resolve_compose_args` (not as inline branches in either picker site). Verified by the parametrized table in `tests/unit/test_resolve_compose_args.py`.
- [ ] All seven open architectural questions are answered in the plan body (above) with rationale.
- [ ] PM/Dev/Teammate/Customer-Service sessions continue to work with no observable behaviour change for the existing four overlays. Verified by running an actual PM session locally via `python -m tools.valor_session create --role pm --message "ping"` and inspecting the worker log for `[persona-compose-failed]` (must be absent) and `Persona overlay loaded: name=project-manager` (must be present with the same prompt_chars as before).
- [ ] No new segments added to `config/personas/segments/manifest.json` (voice consolidation is explicitly deferred). Verified by `git diff main -- config/personas/segments/manifest.json` returning empty.
- [ ] Tests pass: `pytest tests/unit/test_compose_system_prompt.py tests/unit/test_resolve_compose_args.py tests/unit/test_persona_loading.py tests/unit/test_sdk_client_sdlc.py tests/unit/test_message_drafter.py tests/unit/test_drafter_validators.py tests/unit/test_pm_persona_guards.py -v` exits 0.
- [ ] Documentation updated (`/do-docs`).
- [ ] Format clean (`python -m ruff format --check .`).

## Team Orchestration

### Team Members

- **Builder (composer)**
  - Name: composer-builder
  - Role: implements `AccessLevel` enum, `compose_system_prompt`, `_resolve_compose_args`, and the wrappers; updates the two picker sites
  - Agent Type: builder
  - Resume: true

- **Builder (drafter-channel-split)**
  - Name: drafter-builder
  - Role: refactors `bridge/message_drafter.py` to accept `channel` parameter and split system prompt into base + channel rules
  - Agent Type: builder
  - Resume: true

- **Test Writer (byte-stability)**
  - Name: byte-stability-tester
  - Role: writes `test_compose_system_prompt.py` including the byte-stability fixtures captured from main
  - Agent Type: test-engineer
  - Resume: true

- **Validator (full)**
  - Name: composer-validator
  - Role: runs the full pytest unit suite; confirms byte-stability fixtures match; confirms no `if _session_type` ladder remains outside the helper
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: composer-docs
  - Role: creates `docs/features/composed-persona-system.md` and updates index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Capture byte-stability baselines (per-machine)

- **Task ID**: spike-baseline
- **Depends On**: none
- **Validates**: tests/fixtures/{machine_name}/pm_system_prompt_baseline.txt, tests/fixtures/{machine_name}/dev_system_prompt_baseline.txt
- **Assigned To**: byte-stability-tester
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `scripts/capture_persona_baseline.py` that reads `socket.gethostname()` (slugified), calls today's `load_system_prompt()` and `load_pm_system_prompt(work_dir)` (where `work_dir` is the local work-vault path, e.g. `/Users/tomcounsell/src/work-vault/AI Valor Engels System` on this machine), and writes byte-exact output to `tests/fixtures/{machine_name}/dev_system_prompt_baseline.txt` and `tests/fixtures/{machine_name}/pm_system_prompt_baseline.txt`
- Commit the fixtures for the dev's local machine. Other machines (other developers, bridge machines) capture their own baselines during deploy via the same script — see `## Update System` for deploy-time capture wiring
- The byte-stability test in Step 5 reads the local-machine fixture only; if no fixture exists for the current machine, the test SKIPs (not FAILs) with a message pointing at the capture script
- **Strategy rationale (per Risk 1):** strategy (c) — per-machine snapshot, asserted on the local machine. Strategies (a) token normalization and (b) structural equality were rejected because they break the actual #1227 invariant (byte-identical runtime prompt for prompt-cache hits)

### 2. Build AccessLevel enum and compose_system_prompt

- **Task ID**: build-composer
- **Depends On**: spike-baseline
- **Validates**: tests/unit/test_compose_system_prompt.py (create)
- **Assigned To**: composer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `AccessLevel` enum to `config/enums.py` with members `WORKER`, `PM_READONLY`, `TEAMMATE`, `CUSTOMER_SERVICE`
- Implement `compose_system_prompt(persona, access_level, channel=None, *, project=None, working_directory=None)` in `agent/sdk_client.py` near `load_pm_system_prompt`
- Rewrite `load_system_prompt()` as a one-line wrapper: `return compose_system_prompt(PersonaType.DEVELOPER, AccessLevel.WORKER)`
- Rewrite `load_pm_system_prompt(working_directory)` as a one-line wrapper: `return compose_system_prompt(PersonaType.PROJECT_MANAGER, AccessLevel.PM_READONLY, working_directory=working_directory)`
- Implement `_resolve_compose_args(session_type, project, transport, chat_title=None, is_dm=False)` returning `(persona, access_level, channel)` near `_resolve_persona`
- **Do NOT** modify `config/personas/segments/manifest.json` — no new segments are added (voice consolidation is deferred per Risk 4)

### 3. Collapse picker sites

- **Task ID**: build-picker-collapse
- **Depends On**: build-composer
- **Validates**: tests/unit/test_resolve_compose_args.py (create)
- **Assigned To**: composer-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `agent/sdk_client.py` Block A (L3349–L3363) with a single `_resolve_compose_args(...)` call that yields `(persona, access_level, channel)`; replace Block B (L3382–L3419) with one `compose_system_prompt(*args, project=project, working_directory=working_dir)` call
- Replace the equivalent block at `agent/session_executor.py:1563–1629` with the same `_resolve_compose_args` + `compose_system_prompt` pair, preserving the `try/except` around the call so PM-overlay-load failures still emit `[pm-persona-missing]` and the harness still degrades to `_pm_system_prompt = None`
- Preserve the `[persona-load-failed]` ERROR log at session_executor.py:1620–1629 — the composer raises on explicit `email.persona` overlay-missing failure but the call site logs the louder error message
- Confirm both sites produce the same `custom_system_prompt` value (or `_pm_system_prompt` value in session_executor's case) as before for every test cell

### 4. Channel-aware drafter

- **Task ID**: build-drafter-channel
- **Depends On**: spike-baseline
- **Validates**: tests/unit/test_message_drafter.py (update), tests/unit/test_drafter_validators.py (update)
- **Assigned To**: drafter-builder
- **Agent Type**: builder
- **Parallel**: true
- The drafter entry point is `draft_message` at `bridge/message_drafter.py:1720`; the `medium: str = "telegram"` parameter already exists (no signature change required)
- Refactor `DRAFTER_SYSTEM_PROMPT` into `BASE_DRAFTER_PROMPT` (medium-agnostic, retains today's voice content verbatim) + `MEDIUM_RULES = {"telegram": ..., "email": ...}` (initially: `"telegram"` is today's exact format-rules text, `"email"` is a stub identical to `"telegram"` minus Telegram-only format rules)
- Add helper `_compose_drafter_prompt(medium: str) -> str` that returns `BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]`
- Wire `_compose_drafter_prompt(medium)` into the system prompt passed to `client.messages.create(...)` inside `draft_message` (today the drafter loads `DRAFTER_SYSTEM_PROMPT` directly; replace that load with the helper call, parameterized by the existing `medium` argument)
- All drafter call sites that don't pass `medium=` get `"telegram"` by default — no behaviour change
- Verify `BASE + MEDIUM_RULES["telegram"]` reproduces today's `DRAFTER_SYSTEM_PROMPT` byte-for-byte (snapshot test)

### 5. Byte-stability and matrix tests

- **Task ID**: test-byte-stability
- **Depends On**: build-composer, build-picker-collapse
- **Validates**: tests/unit/test_compose_system_prompt.py, tests/unit/test_resolve_compose_args.py
- **Assigned To**: byte-stability-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Write `test_compose_system_prompt.py`: byte-stability cell (compares against fixtures from Task 1), one test per (persona × access-level) cell, startup-lint invariants
- Write `test_resolve_compose_args.py`: parametrized over every input cell

### 6. Integration validation

- **Task ID**: validate-composer
- **Depends On**: test-byte-stability, build-drafter-channel
- **Assigned To**: composer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_compose_system_prompt.py tests/unit/test_resolve_compose_args.py tests/unit/test_persona_loading.py tests/unit/test_sdk_client_sdlc.py tests/unit/test_message_drafter.py tests/unit/test_drafter_validators.py -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Grep for any remaining `if _session_type == SessionType` branches outside the helper — must return zero outside `_resolve_compose_args`
- Confirm `config/personas/segments/manifest.json` is unchanged from main (no new segments added)

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-composer
- **Assigned To**: composer-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/composed-persona-system.md` (include explicit note that voice consolidation is deferred to a follow-up plan)
- Add entry to `docs/features/README.md` index
- Update `docs/features/pm-dev-session-architecture.md` to reference the composer

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: composer-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run full unit-test suite
- Verify all success criteria
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Composer tests pass | `pytest tests/unit/test_compose_system_prompt.py -v` | exit code 0 |
| Resolver tests pass | `pytest tests/unit/test_resolve_compose_args.py -v` | exit code 0 |
| Persona loading regressions | `pytest tests/unit/test_persona_loading.py tests/unit/test_sdk_client_sdlc.py -v` | exit code 0 |
| Drafter regressions | `pytest tests/unit/test_message_drafter.py tests/unit/test_drafter_validators.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No leftover picker ladder for prompt composition | After the build, lines L3349–L3363 (Block A — persona enum resolution) and L3382–L3419 (Block B — system-prompt assembly) in `agent/sdk_client.py` are gone, and L1573–L1588 in `agent/session_executor.py` (the persona-resolution if/elif) is gone. Verified by inspecting the diff: `git diff main -- agent/sdk_client.py agent/session_executor.py \| grep -E "^-.*if _session_type == SessionType\.(PM\|TEAMMATE)"` shows the deleted ladder lines. Other `_session_type` ladders for non-prompt-composition concerns (env injection at L3130, wait-for-children at L3314/L3325, harness_env injection in session_executor) are unaffected and **expected to remain**. | Diff shows ≥4 deleted ladder lines (the persona-resolution branches), and the surviving `_session_type` checks are limited to the non-prompt-composition concerns above. |
| Byte-stability fixture present (per-machine) | `python -c "import socket; from pathlib import Path; m=socket.gethostname().replace('.','-'); assert (Path('tests/fixtures')/m/'pm_system_prompt_baseline.txt').exists() and (Path('tests/fixtures')/m/'dev_system_prompt_baseline.txt').exists(), f'missing baseline for {m}; run scripts/capture_persona_baseline.py'"` | exit code 0 |
| Manifest unchanged | `git diff main -- config/personas/segments/manifest.json` | empty output |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER  | (cycle-1 critique) | voice.md scope contradiction — plan simultaneously claimed voice.md was a new segment in `manifest.json` (Solution Key Element 5; Question 2 + Question 5 resolutions) AND was NOT created in this plan because doing so breaks byte-stability (Risk 4 + No-Gos). | Solution Key Element on voice rules; Technical Approach Question 2 + Question 5; Risk 3; Risk 4; No-Gos; Update System; Step 2 + Step 4 + Step 6 task notes; Documentation; Success Criteria; Verification table. | Scrubbed all references to creating `config/personas/segments/voice.md` in this plan. Voice consolidation is now uniformly framed as **deferred to a follow-up plan**. The composition order in Question 5 no longer includes a `voice` segment (matches today's `manifest.json` exactly). The drafter's `BASE_DRAFTER_PROMPT` retains today's voice content verbatim — no extraction. A new Success Criterion + Verification check assert `manifest.json` is unchanged. Risk 4's framing is preserved as authoritative. |
| BLOCKER  | (cycle-2 critique) | Byte-stability fixture not machine-stable — single fixture file would fail on bridge machines because composed prompt embeds `working_directory` paths, `{{identity.*}}` overrides from `~/Desktop/Valor/identity.json`, and per-machine work-vault `CLAUDE.md` content. | Risk 1 (full rewrite); Step 1 (capture baselines); Update System; Success Criteria byte-stability bullet; Verification table fixture-present check. | Adopted strategy (c): per-machine snapshots stored at `tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt`, with `machine_name` derived from `socket.gethostname()`. Test SKIPs (not FAILs) on machines without a baseline, pointing developers at `scripts/capture_persona_baseline.py`. Strategies (a) token normalization and (b) structural equality were considered and rejected with explicit rationale (both break the actual #1227 cache invariant, which depends on byte-identical runtime prompts). |
| BLOCKER  | (cycle-2 critique) | `format_for_chat` doesn't exist — plan referenced a non-existent function on the drafter; actual API is `draft_message(raw_response, session=None, *, medium="telegram", persona=None)` at `bridge/message_drafter.py:1720`. | Solution → Flow → Drafter prompt assembly; Solution → Key Elements channel-aware drafter bullet; Technical Approach Question 3; Test Impact `tests/unit/test_message_drafter.py` row; Step 4 (drafter channel-split task). | Replaced every `format_for_chat` reference with `draft_message`. Replaced the parameter name `channel` with `medium` everywhere the drafter's *public surface* is described (Step 4, Test Impact, Flow, Question 3). The composer's internal parameter naming (`channel=None` in the working-agent composer) is unaffected because no working-agent cell uses it today (Question 4 / Open Question 1). The signature `async def draft_message(raw_response: str, session=None, *, medium: str = "telegram", persona: str | None = None) -> MessageDraft` already exists at `bridge/message_drafter.py:1720`–1747; the existing `medium` parameter is wired through to `_validate_for_medium` only today, and this plan extends it to also drive prompt selection (`BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]`). No new public parameter is introduced. |
| MAJOR    | (cycle-4 refresh) | Line numbers across all referenced sites have drifted since baseline `5055b527`; cycle-3 plan incorrectly described the `sdk_client.py` picker as a single block at L3326–L3395 when it is actually two blocks (L3349–L3363 + L3382–L3419) separated by ~20 lines of logging; `session_executor.py` second picker drifted from L1430–L1486 down to L1563–L1629; `_load_persona_overlay_with_log` drifted from L1837 up to L1787. Step-by-step tasks and verification grep referenced stale ranges. Channel content moves and AGENT-VOICE.md location were not concretely decided. Acceptance criteria were largely vague (memory note "executable acceptance criteria"). | Freshness Check (full rewrite with line-range table); Problem section (refreshed line refs and channel-leak inventory); Solution → Composer Signature & Call Sites (new section pinning every funnel); Solution → Channel-Awareness Decision (new section listing every content block and where it lands); Solution → AGENT-VOICE.md Decision (new section); Technical Approach picker-collapse bullet; Step 3 task; Verification table grep; Test Impact (refreshed test files + concrete cell assertions); Success Criteria (rewritten as executable assertions); Open Questions (tightened to top-3 reviewer judgment calls); No-Gos (added explicit AGENT-VOICE.md and segment/overlay no-touch rules). | Refreshed all line references against HEAD `5576e4dc907247076b3154e4f73fef96ff0e92f0`. Added the **Composer Signature & Call Sites** pin-down table (4 working-agent funnels + 1 drafter funnel mapped to their post-refactor calls). Added the **Channel-Awareness Decision** ledger enumerating every channel-leaking content block today and explicitly choosing stay-vs-move for each — net: nothing moves out of segments/overlays in this plan; only the drafter's `DRAFTER_SYSTEM_PROMPT` splits. Added the **AGENT-VOICE.md Decision** section explicitly choosing NOT to ship a single voice doc in this plan (deferred to follow-up to keep Risk 1's byte-stability mitigation). Made all Success Criteria executable (memory note compliance) — each criterion is now an assertion the validator runs. Tightened Test Impact with refreshed line numbers and concrete (persona × access × channel) cell assertions. Tightened Open Questions to top-3 reviewer judgment calls. |

---

## Open Questions

These are the top-3 design questions remaining for `/do-plan-critique` to decide. All blocking architectural questions from the original issue (the seven Solution-Sketch questions) are resolved in Technical Approach above; what remains here are reviewer judgment calls.

1. **Keep `channel=` parameter on the composer despite no current cell using it?** The plan resolves Question 4 as "no channel awareness in the working agent." Two options:
    - **(A) Keep the parameter** (plan default). `compose_system_prompt(persona, access_level, channel=None, ...)` accepts but ignores `channel`. Adds 1 parameter to the signature, no behavior. Forward-compat for the moment a real use surfaces. Adds a tiny risk that the parameter rots and gets misused.
    - **(B) Drop the parameter**. `compose_system_prompt(persona, access_level, ...)`. Cleaner now; the picker passes only `(persona, access_level)`. If channel-awareness becomes needed, add the parameter then. Tighter design, no rot risk.
    - **Default in plan: A**, on the grounds that the issue title literally calls it out as a tuple member. Reviewer can flip to B.

2. **`AccessLevel.CUSTOMER_SERVICE` vs collapsing to a `CONVERSATIONAL` umbrella**: the customer-service overlay today is loaded via `_load_persona_overlay_with_log("customer-service", ...)` with no rails. Two options:
    - **(A) 1:1 access level per overlay** (plan default). Four AccessLevel members. Trivial mapping, but argues against the orthogonality goal — `(persona × access_level)` looks square but is actually 4 cells along a diagonal.
    - **(B) Collapse `TEAMMATE` + `CUSTOMER_SERVICE` into `CONVERSATIONAL`**. Three AccessLevel members. The persona overlay carries the action-orientation difference (customer-service.md is action-heavy; teammate.md is conversational-only). Tighter design, surfaces the orthogonality intent, but breaks the simple 1:1 mapping today and requires a small migration in `_resolve_compose_args`.
    - **Default in plan: A**. Reviewer should evaluate B because it's the cleaner expression of the actual axes.

3. **File the voice-consolidation follow-up issue at plan-commit or at PR-merge?** This plan defers voice consolidation entirely (see Risk 4 / AGENT-VOICE.md Decision). Two options:
    - **(A) File at PR-merge time** (plan default). Follow-up issue references the actual landed composer, including post-build line numbers, so it has accurate ground truth.
    - **(B) File at plan-commit time as a placeholder**. Tracks the deferred work earlier; reduces risk of the deferred decision being forgotten. Issue body would need a "to be updated post-build" note for line numbers.
    - **Default in plan: A**. Reviewer can flip to B if traceability beats accuracy at this stage.

---

## Critique Results — Cycle 5

War-room critique against `revision_cycle: 4`, plan commit `84488a30`, HEAD `5576e4dc`. Six critic perspectives + automated structural checks. All cited line numbers verified at HEAD.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections (Documentation, Update System, Agent Integration, Test Impact) | PASS | All four present and non-empty. |
| Task numbering | PASS | 8 tasks, no gaps. |
| Dependencies valid | PASS | All Depends-On targets resolve to existing task IDs; no cycles. |
| File paths exist (HEAD `5576e4dc`) | PASS | All cited paths verified present, including private overlays at `~/Desktop/Valor/personas/`. |
| Cited line numbers match HEAD | PASS | Sampled across `agent/sdk_client.py` (L647, L878, L892, L919–948, L966, L994, L998, L1023, L1037, L1787, L1860, L3349–3363, L3382–3419), `agent/session_executor.py` (L1563–1629), `bridge/message_drafter.py` (L381, L1295, L1720–1750), `config/enums.py` L17, L25 — all correct. |
| Prerequisites met | PASS | Both manifest.json and the PM overlay (private + repo-fallback) load. |
| Cross-references consistent | PASS | Every Success Criterion maps to at least one task; No-Gos do not contradict the Solution. |

### Findings

#### BLOCKERS

None.

#### CONCERNS

##### C1. `_resolve_compose_args` semantic drops per-group/dm persona resolution today routed through `_resolve_persona`

- **Severity**: CONCERN
- **Critics**: Skeptic, Adversary, Consistency Auditor (all flagged the same gap)
- **Location**: Solution → "Composer Signature & Call Sites" + Step 3 + Test Impact `test_resolve_compose_args.py` table (L313–L322)
- **Finding**: The plan's resolver table covers `(session_type, project_mode, transport, email.persona) → (persona, access_level, channel)` but elides the third axis the existing picker depends on: today's `_resolve_persona(project, chat_title, is_dm)` at `agent/sdk_client.py:1860` returns persona values driven by **DM-vs-group + per-group config** (`telegram_config.groups[<title>].persona` at L1900–1904, `telegram_config.dm_persona` at L1888). Block A at sdk_client.py:3363 falls through to that resolver for DEV sessions. The cycle-4 resolver table only enumerates `session_type × project_mode × transport × email.persona`, so a Telegram group whose `groups[<chat>].persona = "customer-service"` (today picked up by `_resolve_persona`) maps to `DEVELOPER` under the new table.
- **Suggestion**: Add a row family to the table for `(DEV, dev, telegram, no email override) → call _resolve_persona(project, chat_title, is_dm)` and parametrize the test over at least: DM with `dm_persona=teammate`, group with `groups[<title>].persona=customer-service`, group with no persona key (default DEVELOPER), and project=None (returns DEVELOPER for non-DM). Either keep `_resolve_persona` as the inner DM/group resolver and document `_resolve_compose_args` as a thin wrapper around it, or inline its logic and delete the function — but do not silently drop it.
- **Implementation Note**: `_resolve_compose_args(session_type, project, transport, chat_title, is_dm, email_persona_requested)` — when `session_type == DEV` and no email override, delegate to `_resolve_persona(project, chat_title, is_dm)` for the persona value (then map to `(persona, AccessLevel.WORKER, channel)`). The session_executor.py call site does NOT have `chat_title`/`is_dm` as locals at L1563 — they live on `session.chat_title` and need `is_dm = session.chat_title is None` synthesized at call site (mirrors the pattern at sdk_client.py:3348). The resolver must return the same persona enum value the existing `_resolve_persona` returns for every (project, chat_title, is_dm) triple — covered by an additional parametrized test that calls both old and new resolvers and asserts equality.

##### C2. Email-persona override semantics differ between sdk_client.py and session_executor.py — collapse must pick one

- **Severity**: CONCERN
- **Critics**: Adversary, Consistency Auditor
- **Location**: Solution → Technical Approach picker-collapse bullet (L267) + Step 3 (L516–L527)
- **Finding**: The two pickers are not merely "drifted but equivalent." At `agent/sdk_client.py:3352–3361` the email-persona override fires **only when `_session_type == SessionType.TEAMMATE`**. At `agent/session_executor.py:1576–1585` the override fires when **`_transport == "email"` OR `_email_persona_requested` is set, regardless of session_type** (excluding PM, which is matched first). For a hypothetical `(DEV, transport=email, email.persona=customer-service)` session (currently impossible because `bridge/email_bridge.py:716` only emits PM or TEAMMATE — verified), the two pickers would produce **different** personas. The plan's collapse target inherits the executor's looser rule (Test Impact table row "DEV | dev | email | customer-service → CUSTOMER_SERVICE") without naming the divergence. The byte-stability fixture won't catch this because both PM and TEAMMATE paths agree today; the divergence only surfaces if a future caller emits a DEV email session.
- **Suggestion**: Either (a) document explicitly in `_resolve_compose_args` and the resolver test that the executor's behavior is canonical and the sdk_client.py behavior was an unintended subset, with a one-line comment citing this rationale, or (b) tighten the resolver to match sdk_client.py's narrower rule (only TEAMMATE + email triggers the override) and adjust the test row accordingly. Picking either is fine; silently picking one is the failure mode.
- **Implementation Note**: At the point of collapse, write the rule as `if _email_persona_requested and (session_type == SessionType.TEAMMATE or transport == "email"): persona = _email_persona_requested` (the union of the two sites' rules; verified safe today because email_bridge.py:716 only emits PM/TEAMMATE). Add a docstring line to `_resolve_compose_args` recording the choice and citing `bridge/email_bridge.py:716` as why DEV+email doesn't actually appear today. The Step 3 task acceptance check `Confirm both sites produce the same custom_system_prompt value (or _pm_system_prompt value) as before for every test cell` then needs a second clause: "for every cell **that the existing pickers AGREE on today**." Cells where they would disagree (DEV+email+override) are explicitly delegated to the new canonical rule, and the deviation is logged in the picker-collapse PR description.

##### C3. Test Impact missing 5 existing test files that import the soon-to-be-wrapper functions

- **Severity**: CONCERN
- **Critics**: Skeptic, Operator
- **Location**: Test Impact (L289–L326)
- **Finding**: Grep across the repo at HEAD shows `load_system_prompt` / `load_pm_system_prompt` are imported by these test files NOT listed in Test Impact:
  - `tests/unit/test_sdk_client.py` (imports `load_system_prompt`, asserts non-empty)
  - `tests/unit/test_pm_channels.py` (imports both; the plan calls it "no change unless they assert specific picker branches" — but it does call both functions directly at L33, L38, L46, L52, L61, L66 and the regression test at L65 specifically depends on `load_system_prompt()` returning a string with WORKER_RULES)
  - `tests/unit/test_load_principal_context.py` (imports `load_system_prompt`, asserts principal context integration)
  - `tests/unit/test_sdk_permissions.py` (imports `load_system_prompt`, asserts content)
  - `tests/unit/test_agent_session_hierarchy.py` (`@patch("agent.sdk_client.load_system_prompt", ...)` at L433, L444 — patches the *function path*; if the wrapper is rewritten, the patch target must still be the same module attribute or these tests break)
  - `tests/unit/test_cross_repo_gh_resolution.py` (`patch("agent.sdk_client.load_system_prompt", ...)` at L22 — same patch-target concern)
  - `tests/integration/test_harness_env_pm_injection.py` (calls `load_pm_system_prompt(str(wd))` at L97, L230 — integration test, the plan claims "no integration tests reference the prompt-byte content")
- **Suggestion**: Either explicitly mark each of these in Test Impact as **NO CHANGE because the wrappers preserve their public signatures and module paths** (which is the plan's actual invariant), or audit each file for any assumption that breaks under the wrapper rewrite (patch targets are the highest-risk class). The integration test claim "No existing integration tests reference the prompt-byte content directly" is false as worded — `test_harness_env_pm_injection.py::test_load_pm_system_prompt_failure_does_not_crash` directly tests the function's failure mode.
- **Implementation Note**: Add to Test Impact: a single line per file with disposition NO CHANGE + "wrappers preserve `load_system_prompt`/`load_pm_system_prompt` symbol path; mock.patch targets `agent.sdk_client.load_system_prompt` continue to resolve." For `test_harness_env_pm_injection.py`, add a one-line update to the integration-test claim: "**Update**: `tests/integration/test_harness_env_pm_injection.py` calls `load_pm_system_prompt` directly at L97, L230. Test must continue to pass; covered by the byte-stability invariant since the wrapper delegates."

##### C4. Per-machine baseline strategy degrades to "no enforcement" on every fresh CI machine

- **Severity**: CONCERN
- **Critics**: Operator, Skeptic
- **Location**: Risk 1 + Step 1 + Update System + Verification table "Byte-stability fixture present (per-machine)" check
- **Finding**: The mitigation strategy makes the byte-stability test SKIP when no per-machine fixture exists. CI runners are typically ephemeral (each PR may run on a fresh GH Actions / launchd worker with a different `socket.gethostname()`), so the test will SKIP on every CI run for any machine that hasn't pre-committed a fixture. The Verification table has a fixture-present check, but it only checks the *current* machine — it cannot detect that the byte-stability assertion never actually ran on the CI machine that gated the merge. The Risk 1 mitigation defends against false positives but introduces a silent false-negative path: a real byte regression on the dev machine that committed its baseline gets caught; the same regression on a different machine after merge does not. This is the trade-off the plan accepted with strategy (c), but it should be explicit that **the byte-stability invariant is only enforced for the developer who builds the feature**, with no machine-agnostic gate.
- **Suggestion**: Either (a) accept and document that limitation explicitly in Risk 1 (one sentence: "byte stability is enforced on the developer's local machine; bridge machines and fresh CI runners SKIP — this is the cost of strategy (c) and is acceptable because cache stability is itself a per-machine, per-session invariant"), or (b) add a *machine-agnostic* structural check that runs everywhere — e.g. assert the segment-list ordering in the composed prompt matches `manifest.json` order, assert WORKER_RULES is the first ~13 lines of the WORKER cell, assert `\n\n---\n\n` separators occur at expected fence positions. Strategy (b) does not replace byte stability but covers the failure modes that byte stability would catch (segment reordering, separator drop, missing WORKER_RULES) on every machine. The plan rejected structural equality as a *replacement* for byte stability; it can still be a *backup* on machines where byte stability SKIPs.
- **Implementation Note**: Add to `test_compose_system_prompt.py` two test classes: `TestByteStability` (current plan, SKIPs without baseline) and `TestStructuralInvariants` (always runs). The structural test asserts: (i) WORKER cell starts with the exact `WORKER_RULES` constant followed by `\n\n---\n\n`, (ii) PM cell does NOT contain `WORKER_RULES`, (iii) for both cells, segment-content substrings appear in the same order as `manifest.json["segments"]` (use a list-of-find-indices monotonicity check, not full substring equality), (iv) no `{{identity.*}}` templating residue. This catches the >90% of "structural regression" failure modes on any machine without requiring per-machine fixtures. Update Risk 1 to add one paragraph: "Strategy (c) is byte-exact on the dev machine; structural invariants run everywhere as a defense-in-depth against the regressions byte stability is meant to catch."

##### C5. Drafter `BASE + MEDIUM_RULES["telegram"]` byte-equality is not asserted at module load time

- **Severity**: CONCERN
- **Critics**: Skeptic, Operator
- **Location**: Step 4 (L529–L542) + Success Criteria drafter bullet (L443) + Risk 3
- **Finding**: The plan's invariant for the drafter split is "concatenating `BASE + MEDIUM_RULES['telegram']` reproduces today's prompt byte-for-byte (snapshot test using captured-from-main string)." But Step 4 does not actually require the snapshot to be captured *before* the refactor begins (parallel to the per-machine baseline capture in Step 1). If the developer refactors `DRAFTER_SYSTEM_PROMPT` and then writes a snapshot test against the *post-refactor* concatenation, the test is tautological — it asserts that the new code equals itself. The PM-cell baseline gets explicit "capture from main *before* the composer ships" treatment in Step 1; the drafter does not.
- **Suggestion**: Add a sub-step to Task 1 (or a sibling task) to capture `tests/fixtures/drafter_system_prompt_baseline.txt` from `bridge/message_drafter.py:DRAFTER_SYSTEM_PROMPT` on `main` *before* the refactor lands. The snapshot test then asserts `_compose_drafter_prompt("telegram") == fixture_text`. This fixture is machine-agnostic (the drafter prompt embeds no machine-specific paths or identity values) so a single repo-committed file works.
- **Implementation Note**: Capture script can be `python -c "from bridge.message_drafter import DRAFTER_SYSTEM_PROMPT; open('tests/fixtures/drafter_system_prompt_baseline.txt','w').write(DRAFTER_SYSTEM_PROMPT)"` run on `main` before checking out `plan/composed-persona-1268`. Commit the fixture file in Task 1's PR. Step 4 then asserts `_compose_drafter_prompt("telegram") == open('tests/fixtures/drafter_system_prompt_baseline.txt').read()`. This is a single fixture, not per-machine — drafter prompt has no machine-stable axis problem.

#### NITS

##### N1. Open Question 1 (channel parameter) — recommend (B) drop the parameter

- **Severity**: NIT
- **Critics**: Simplifier, User
- **Location**: Open Question 1 (L618–L621)
- **Finding**: The plan keeps `channel=None` on the composer for forward-compat despite Question 4 resolving "no channel awareness in the working agent." A parameter that is accepted, ignored, and has zero callers asserting on it will rot. The Channel-Awareness Decision ledger explicitly records that nothing moves into a channel facet in this plan; if that's true, the parameter has no current consumer and "the issue title literally calls it out as a tuple member" is documentation, not justification. Adding the parameter when a real use surfaces is one line of diff and a deprecation-free addition.
- **Suggestion**: Flip Question 1 to (B) — drop `channel=` from `compose_system_prompt`. Resolves Simplifier's "abstractions for a single use case." Keep `medium=` on the drafter unchanged (that one has a current use).

##### N2. Open Question 2 (CUSTOMER_SERVICE access level) — keep 4-level mapping (A)

- **Severity**: NIT
- **Critics**: Simplifier, User
- **Location**: Open Question 2 (L623–L626)
- **Finding**: The argument for collapsing to a 3-level `CONVERSATIONAL` umbrella is tighter design, but the cost is a non-trivial migration in `_resolve_compose_args` to map `(persona=customer-service)` → `(access_level=CONVERSATIONAL)` and an asymmetry where the persona overlay carries action-orientation while the access level is mute. The 1:1 mapping today is in fact the simpler invariant: each persona has exactly one access level it ever runs with. Forcing orthogonality before a real second-cell use case (e.g. teammate-with-customer-service-rails) exists is the same future-proofing pattern Question 1 resolves against in the other direction.
- **Suggestion**: Keep (A) — 4 access levels, 1:1 mapping. Document explicitly in `AccessLevel`'s docstring: "today maps 1:1 with PersonaType; orthogonality is structural intent for future expansion, not a current invariant." When a real second-cell use case appears, a follow-up plan handles the consolidation (it is a small change to a small enum).

##### N3. Open Question 3 (voice follow-up issue timing) — file at PR-merge (A)

- **Severity**: NIT
- **Critics**: Operator
- **Location**: Open Question 3 (L628–L631)
- **Finding**: The placeholder-now option (B) trades accuracy for traceability. Given the cycle-4 plan already drifted line numbers across multiple sites in 14 days, a placeholder issue filed *now* with line numbers will be stale within weeks. The follow-up plan is invariant-bound (Risk 1 / cache invariant) regardless of whether an issue exists; placeholder issues do not increase the chance of action. PR-merge timing means the issue body cites real shipped line numbers and links the merged PR.
- **Suggestion**: Keep (A) — file at PR-merge time. Add to Step 7 (Documentation) a checklist item: "After PR merges, file follow-up issue 'voice consolidation: shared voice.md segment' with link to merged PR and line citations from landed code."

##### N4. Risk 2 mitigation could add the optional grep-based test it mentions

- **Severity**: NIT
- **Critics**: Operator
- **Location**: Risk 2 (L362–L365)
- **Finding**: The risk says "Optional: a grep-based test asserts no `if _session_type == SessionType` ladder remains in either file outside the helper." The Verification table has a similar grep but as a manual diff inspection. Promoting the grep to an automated test (`tests/unit/test_no_picker_ladder.py`) makes Risk 2's mitigation enforced on every CI run, not just at merge.
- **Suggestion**: Add to Step 5 a one-line test that opens `agent/sdk_client.py` and `agent/session_executor.py` as text, asserts that lines matching `r"^\s*(if|elif)\s+_session_type\s*==\s*SessionType\.(PM|TEAMMATE|DEV)\b"` outside the `_resolve_compose_args` body are zero. Keep the existing Verification grep as a belt-and-suspenders check.

### Aggregated Verdict

5 CONCERNs (all with concrete Implementation Notes), 4 NITs, 0 BLOCKERs. The plan is structurally sound at cycle 4: every cited line number verifies, all required sections are present and substantive, the byte-stability strategy is well-reasoned, and the cycle-4 refresh correctly identified line drift. The CONCERNs are about *completeness* rather than *correctness* — three (C1, C2, C3) are gaps in the resolver/test coverage that the build phase needs to embed before writing code; two (C4, C5) are missing baseline-capture steps that prevent tautological tests.

**Verdict**: READY TO BUILD (with concerns). Trigger one revision pass to embed the five Implementation Notes into Step 1, Step 3, Step 4, Test Impact, and Risk 1 before `/do-build`. NITs are guidance for the reviewer's three open questions; they do not block.
