---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1268
last_comment_id:
---

# Composed Persona System: Single (persona × access-level × channel) Builder

## Problem

The agent's system-prompt assembly conflates three independent axes — *who the agent is* (persona), *what it's allowed to do* (access level), and *where output is going* (channel) — into a hand-coded `if/elif` ladder duplicated across two call sites. Adding any new combination means another branch in two files; channel-specific rules leak into the working-agent prompt where they don't belong.

**Current behavior:**

- Two parallel pickers exist:
  - [`agent/sdk_client.py`](../../agent/sdk_client.py) L3326–L3395 (`get_response_via_harness` path)
  - [`agent/session_executor.py`](../../agent/session_executor.py) L1430–L1486 (the harness-route persona resolution)
  Both branch on `SessionType` plus a `transport == "email"` override that swaps in `project.email.persona`. They are similar but not identical and have drifted independently (issue cited only `sdk_client.py`; freshness check found the second site).
- Two prompt-builder functions exist with hard-baked behavior:
  - `load_system_prompt()` — bakes in the developer persona + `WORKER_RULES` + principal context + completion criteria.
  - `load_pm_system_prompt(working_dir)` — bakes in the project-manager persona, *omits* `WORKER_RULES`, appends work-vault `CLAUDE.md`. Documented invariant in its docstring at sdk_client.py:1022–1026.
  - For teammate / customer-service personas, `_load_persona_overlay_with_log()` is called directly with no rails layer at all.
- Channel awareness leaks into the working-agent prompt: persona segments (`identity.md`, `tools.md`) and the developer overlay describe Telegram-specific behaviour, even though most of those concerns only matter at message-drafting time.
- Voice rules (banned phrases, "no empty promises", good/bad reply examples) are scattered across persona overlays and `bridge/message_drafter.py:1295` `DRAFTER_SYSTEM_PROMPT` with no single source of truth.
- `email.persona` per-project override is the only "channel changes the prompt" code path and lives inline in *both* pickers, not in a composer.

**Desired outcome:**

A single `compose_system_prompt(persona, access_level, channel=None, **kwargs)` function is the only path that produces a fully assembled agent system prompt. Both pickers collapse to: derive `(persona, access_level, channel)`, call the composer, return. Channel-specific deltas are pushed into the message drafter (the only place that legitimately needs to know whether output is going to Telegram vs email). The four existing persona overlays continue to work; observable prompt bytes for the `(developer, worker, telegram)` and `(project-manager, pm-readonly, telegram)` cells are byte-identical to today's output (preserving #1227's cache stability invariant).

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:18:37Z (today)
**Disposition:** Minor drift — the issue cites `sdk_client.py` L3326–L3395 as the only picker location, but a second equivalent picker exists in `agent/session_executor.py` L1430–L1486 (introduced for the harness route). The plan must collapse BOTH sites.

**File:line references re-verified:**

- `agent/sdk_client.py:892` (`load_persona_prompt`) — still holds
- `agent/sdk_client.py:966` (`load_system_prompt`) — still holds
- `agent/sdk_client.py:998` (`load_pm_system_prompt`) — still holds
- `agent/sdk_client.py:3326–3395` (picker) — still holds
- `agent/sdk_client.py:3331` (email override) — still holds
- `bridge/message_drafter.py:1295` (`DRAFTER_SYSTEM_PROMPT`) — still holds
- `config/enums.py` `PersonaType` (4 members) and `SessionType` (3 members) — still holds
- `config/personas/segments/manifest.json` (4 segments) — still holds
- **NEW:** `agent/session_executor.py:1430–1486` — second picker site not cited in the issue but functionally equivalent and must be unified

**Cited sibling issues/PRs re-checked:**

- #395 — CLOSED. Established persona/session-type split. Successful prior art.
- #1148 — CLOSED. Added PM persona overlay with CRITIQUE/SDLC rules + the loader-warning pattern at sdk_client.py:919–948. Composer must preserve those warnings.
- #1189 — CLOSED. Added the workflow-announcement guard (loader warning at L931–939). Composer must preserve.
- #1227 — CLOSED. Established the byte-stable PM prompt-prefix invariant for Anthropic prompt cache. Composer must preserve byte-for-byte for the PM cell.

**Commits on main since issue was filed (touching referenced files):** none

**Active plans in `docs/plans/` overlapping this area:**

- `pm-persona-hardening.md` (#1007, In Progress) — adds three new sections to the PM overlay file. Scope is the PM overlay *content*, not the loader. **Not blocking.** Composer treats the overlay as opaque text.
- `unify-persona-vocabulary.md` (#599, Draft) — eliminates `ChatMode` enum and renames `qa_*` to `teammate_*`. Plan touches `config/enums.py` and the picker. **Coordination signal:** if `unify-persona-vocabulary.md` lands first, this plan picks up `PersonaType` cleanly; if this plan lands first, that plan rebases against the new composer call site. Both plans agree on `PersonaType` as the canonical enum — there is no semantic conflict.

**Notes:** The two-site picker is the most important freshness finding — without it the plan would only collapse one branch ladder and leave a parallel one in `session_executor.py` to drift again.

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
- **Picker collapse**: both `sdk_client.py:3326–3395` and `session_executor.py:1430–1486` collapse to a single helper `_resolve_compose_args(session_type, project, transport, ...) -> (persona, access_level, channel)` that lives in one place and is called from both sites.
- **Channel-aware drafter**: `bridge/message_drafter.py` receives a `channel` parameter (default `"telegram"` for backward compatibility) that selects channel-specific format rules. The drafter system prompt is split into a base voice section (shared with the working agent) plus a per-channel format section.
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
- **Voice rules consolidation (Question 2)**: a single `config/personas/segments/voice.md` segment is added to the manifest. It contains the banned-phrases, no-empty-promises, and tone rules. Both the working-agent composer (via segment assembly) and the drafter (via direct file load) pull from it.
- **Channel-aware drafter**: `bridge/message_drafter.py` accepts a `channel` parameter and composes its system prompt as `BASE_DRAFTER_PROMPT + CHANNEL_RULES[channel]`. Today's behaviour is the `channel="telegram"` cell.

### Flow

**Working-agent prompt assembly:**

Session arrives at executor → `_resolve_compose_args(session_type, project, transport)` returns `(persona, access_level, channel)` → `compose_system_prompt(persona, access_level, channel, project=project, working_directory=working_dir)` returns the full prompt string → string passed to `claude -p` via `--append-system-prompt`.

**Drafter prompt assembly:**

Worker emits agent output → `bridge/message_drafter.py:format_for_chat(text, channel="telegram")` → drafter composes `BASE_DRAFTER_PROMPT + CHANNEL_RULES["telegram"]` (or `"email"`, etc.) → calls Haiku via `client.messages.create(system=composed_prompt, ...)`.

### Technical Approach

- **Where the composer lives**: keep it adjacent to existing loaders in `agent/sdk_client.py`, alongside `load_persona_prompt`/`load_system_prompt`/`load_pm_system_prompt`. Extracting to a new module is a follow-up if the file gets too large.
- **Question resolutions baked in** (the seven open questions from the issue, resolved here):

  1. **Access-level vs session-type**: `AccessLevel` is **orthogonal** to `SessionType`. `SessionType` is the AgentSession discriminator (decides queueing, child-session shape, output handler); `AccessLevel` is the prompt-rails layer. The mapping today happens to be 1:1 (`pm` → `PM_READONLY`, `dev` → `WORKER`, `teammate` → `TEAMMATE`), but they live separately so future per-project rails (e.g., a teammate session in customer-service mode) don't need new SessionType members. The resolver `_resolve_compose_args` encodes the mapping.

  2. **Voice doc location**: a new shared segment `config/personas/segments/voice.md` listed in `manifest.json`. Both the composer (via segment assembly) and the drafter (via direct read) pull from it. Banned phrases and tone rules live there, NOT in `identity.md` (which keeps role/identity content) and NOT in per-persona overlays. The PM overlay's "no empty promises" rule moves to `voice.md`. The drafter's `DRAFTER_SYSTEM_PROMPT` no longer duplicates voice content; it imports `voice.md` text once and inlines it in the prompt template at module-load time (no runtime IO in the drafter hot path).

  3. **Channel extraction into the drafter**: `bridge/message_drafter.py` composes its system prompt **at module load** as `BASE + CHANNEL_RULES[channel]`. The structured-output `tool_use` schema is unchanged — it stays shared across channels. Channel parameter defaults to `"telegram"` for backward compatibility with all existing call sites; only the future email-channel case needs to pass `channel="email"`.

  4. **Minimum channel-awareness for the working agent**: **None.** The working agent does not need channel context in its system prompt. Reachability/emoji-react decisions are made by the agent based on tool output (e.g., reading recent Telegram chat state via `valor-telegram read`), not encoded in the prompt. This drops the `channel=` parameter from the composer's required signature; it remains as an optional facet **only if a concrete need is proven during build** (Open Question 1 below pins this).

  5. **Composition order and overrides**: **strict additive layering, no redaction.** Order is fixed: `WORKER_RULES (if WORKER) → identity → work-patterns → tools → voice → private-tag → persona overlay → principal context (if WORKER) → completion criteria (if WORKER) → work-vault CLAUDE.md (if PM_READONLY)`. If two layers contradict, the source documents must be fixed; the composer does not silently mediate. A startup lint pass (Question 7) detects contradictions.

  6. **Migration path**: the four existing overlays continue to load via `_resolve_overlay_path` unchanged. The migration is internal to the composer — wrappers preserve their public signatures. Byte-stability test (Success Criterion below) asserts the `(developer, WORKER, None)` and `(project-manager, PM_READONLY, None)` cells produce **byte-identical** output to `load_system_prompt()` and `load_pm_system_prompt(work_dir)` from main today.

  7. **Runtime preview / lint**: a one-off `pytest` test (`test_compose_system_prompt_invariants`) asserts at test time: (a) every cell composes without exception, (b) PM cell stays under 80K chars (cache budget), (c) no `{{identity.*}}` markers remain in the output, (d) `WORKER_RULES` precedes the persona overlay text in the `WORKER` cell. **No runtime cost** — this runs in CI, not on every compose. The validators in `_load_persona_overlay_with_log` and `load_persona_prompt` (the existing CRITIQUE/workflow-announcement substring checks) stay where they are; they catch overlay drift, not composer drift.

- **Picker collapse**: extract `_resolve_compose_args` into a private helper at `agent/sdk_client.py` near `_resolve_persona`. Both call sites (sdk_client.py:3326 and session_executor.py:1430) call this helper. The helper encapsulates the email-persona override (`if transport == "email" and project.email.persona: persona = ...`).
- **Backward compatibility**: `load_system_prompt()` and `load_pm_system_prompt(work_dir)` remain as wrappers (one-line implementations that call the composer). All existing call sites continue to work without change. New code path: `compose_system_prompt(...)` direct.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_load_persona_overlay_with_log` already has structured exception handling for missing overlays (sdk_client.py:1837–1853). The composer must NOT swallow exceptions silently — it raises `FileNotFoundError` for missing required overlays, matching `load_persona_prompt`'s behavior at sdk_client.py:960–963. Tests assert that the composer raises (not returns empty string) for unknown personas with no fallback.
- [ ] Add tests asserting that a missing `voice.md` segment produces a clear error at startup, not silent omission of voice rules.

### Empty/Invalid Input Handling

- [ ] `compose_system_prompt(persona="invalid", ...)` raises `ValueError` with a list of valid persona names.
- [ ] `compose_system_prompt(persona=PersonaType.DEVELOPER, access_level="not-an-AccessLevel", ...)` raises `TypeError`.
- [ ] `compose_system_prompt(persona=..., access_level=PM_READONLY, working_directory=None)` raises `ValueError("PM_READONLY requires working_directory")`. PM_READONLY without a working dir is a programmer error today (the wrapper crashes at `Path(None)`).
- [ ] Empty / whitespace-only overlay file: composer logs a WARNING and proceeds (matches today's behavior; overlay files always contain content but tests assert the failure mode).

### Error State Rendering

- [ ] If `voice.md` is missing the composer raises at startup; the worker logs `[persona-compose-failed]` and the session refuses to start (no silent fallback to a voice-less prompt). Test asserts the log line is emitted.
- [ ] If the picker resolves to an unknown `(persona, access_level)` pair, the composer raises before any IO; test asserts no partial prompt is returned.

## Test Impact

- [ ] `tests/unit/test_persona_loading.py` — UPDATE: `load_system_prompt` and `load_pm_system_prompt` now delegate to `compose_system_prompt`; existing tests (lines 164–186, 301) still pass because the wrappers preserve byte output. Add a new test `test_load_system_prompt_byte_stable_through_composer` that asserts the wrapper output equals the direct composer output for the `(DEVELOPER, WORKER)` cell.
- [ ] `tests/unit/test_sdk_client_sdlc.py` — UPDATE: `WORKER_RULES` constant unchanged; the test that asserts WORKER_RULES is prepended (currently against `load_system_prompt()`) is rewritten to assert against `compose_system_prompt(persona=DEVELOPER, access_level=WORKER)`. Add a regression: `compose_system_prompt(persona=PROJECT_MANAGER, access_level=PM_READONLY, working_directory=...)` does NOT contain `WORKER_RULES` substring (preserves the load_pm_system_prompt invariant from sdk_client.py:1023).
- [ ] `tests/unit/test_message_drafter.py` — UPDATE: drafter system prompt is now `BASE + CHANNEL_RULES["telegram"]`. Existing tests should pass unchanged because the default `channel="telegram"` produces the same text. Add a new test asserting `format_for_chat(..., channel="email")` uses different format rules.
- [ ] `tests/unit/test_drafter_validators.py` — UPDATE: any test that imports `DRAFTER_SYSTEM_PROMPT` directly is updated to import the composed result via the new `_compose_drafter_prompt(channel)` helper.
- [ ] `tests/unit/test_pm_persona_guards.py` — no change. The PM overlay loader-warning tests at sdk_client.py:919–948 are preserved; they're inside `load_persona_prompt`, which stays as the segment-and-overlay assembler.
- [ ] `tests/unit/test_message_drafter_chat_log.py`, `test_message_drafter_linkify.py` — UPDATE only if they import `DRAFTER_SYSTEM_PROMPT` directly; otherwise no change.
- [ ] **NEW** `tests/unit/test_compose_system_prompt.py` — create. Covers: (1) byte-stability for `(DEVELOPER, WORKER)` and `(PROJECT_MANAGER, PM_READONLY)` cells against the wrappers' output captured from main; (2) one test per cell of the (persona × access-level) matrix; (3) startup-lint invariants (PM under 80K chars, no `{{identity.*}}` markers, WORKER_RULES precedes overlay).
- [ ] **NEW** `tests/unit/test_resolve_compose_args.py` — create. Covers: each `(SessionType, project_mode, transport, project.email.persona)` input cell maps to the expected `(persona, access_level, channel)` output. Replaces inline branch-by-branch testing of the two pickers.

No existing integration tests reference the prompt-byte content directly, so no integration test impact.

## Rabbit Holes

- **A new runtime permission system tied to AccessLevel.** The hook system at `agent/hooks/pre_tool_use.py` already enforces PM read-only restrictions via `SESSION_TYPE`. AccessLevel is **prompt-only**; do not refactor the hook layer to use it. Out of scope.
- **Refactoring `bridge/message_drafter.py` beyond the channel split.** The drafter is 1860 lines with substantial format logic, structured-output schemas, fallback chains, and chat-log formatting. The plan touches ONLY the system-prompt composition; everything else stays.
- **Moving `WORKER_RULES` content into a segment file.** The current constant is short (13 lines) and lives next to the composer. Moving it to `config/personas/rails/worker.md` adds file IO without value. Defer.
- **A new "voice doc" with good/bad reply *examples*.** The fazm pattern includes good-vs-bad example pairs. This plan adds the **rules** (banned phrases, tone) to `voice.md` but defers reply *examples* — they're a quality concern that needs separate validation. Out of scope for this refactor.
- **Renaming `_session_type` / picker variables.** Cosmetic. Stays out.

## Risks

### Risk 1: Byte-stability regression for the PM cell breaks #1227's prompt cache

**Impact:** PM session TTFT regresses from <90s (warm) to 15–20min (cold). Catastrophic UX hit.
**Mitigation:** `test_compose_system_prompt.py::test_pm_cell_byte_stable` snapshots the current `load_pm_system_prompt(work_dir)` output (in a fixture file at `tests/fixtures/pm_system_prompt_baseline.txt`, generated once from main). The test asserts byte-equality of the new composer output. The build cannot proceed until this test passes. Includes a check that the `--exclude-dynamic-system-prompt-sections` integration is unaffected.

### Risk 2: Two-site picker drift returns

**Impact:** A future change to the email override is added to one picker site and not the other; behaviour diverges between the `get_response_via_harness` path and the `session_executor` harness path.
**Mitigation:** the `_resolve_compose_args` helper is the single source of truth; both call sites import it. A unit test (`test_resolve_compose_args.py`) is the primary regression. Optional: a grep-based test asserts no `if _session_type == SessionType` ladder remains in either file outside the helper.

### Risk 3: Voice rules consolidation breaks the drafter's existing format rules

**Impact:** Drafter output silently changes — bullets vs prose, "no empty promises" warnings change, format regressions ship to production.
**Mitigation:** voice content is moved verbatim from current sources (PM overlay, developer overlay, `DRAFTER_SYSTEM_PROMPT` quality rules) into `voice.md`. The drafter prompt's *format rules* (bullets, "---" separator, ">> " prefix) stay in `DRAFTER_SYSTEM_PROMPT`. Tests in `test_drafter_validators.py` already cover format invariants — they must remain green.

### Risk 4: Adding `voice.md` to the segment manifest changes the developer / teammate / customer-service prompt bytes

**Impact:** The `(DEVELOPER, WORKER)` cell is no longer byte-stable against today's `load_system_prompt()` because a new segment is inserted into the assembled prompt. This breaks Risk 1's mitigation.
**Mitigation:** **two-step rollout.** Step 1 (this plan) introduces the composer with byte-stability for the existing four cells — voice content is NOT yet promoted to a segment; it stays where it is in each overlay. The composer reads `voice.md` only when the drafter requests it. Step 2 (follow-up plan, NOT this issue) deprecates the duplicated voice rules in overlays. This keeps Risk 1 and Risk 3 mitigations independent.

## Race Conditions

No race conditions identified — prompt composition is synchronous, single-threaded, and runs once per session at startup. All file IO is read-only against immutable-during-session files. The composer is called from both `sdk_client.py:get_response_via_harness` and `session_executor.py` execute paths, but each call is independent and there is no shared mutable state.

## No-Gos (Out of Scope)

- AccessLevel as a runtime hook-enforcement system (see Rabbit Holes).
- Promoting voice rules to a segment file in this plan (deferred to a follow-up to keep Risk 1 mitigation clean).
- Adding good/bad reply *examples* in `voice.md` (rules only; examples are a separate quality plan).
- Refactoring the drafter beyond system-prompt composition.
- Adding new persona overlays (`developer`, `project-manager`, `teammate`, `customer-service` are the only four; new ones are a separate plan).
- Channel-awareness in the working-agent prompt (resolved as "none" per Question 4; revisit only if a concrete need surfaces during build).
- Runtime composer caching / memoization (each compose is < 50ms; not worth a cache layer).

## Update System

No update system changes required — this is a purely internal refactor. The `update` skill at `scripts/remote-update.sh` already pulls `config/personas/` and the new `voice.md` segment is delivered via normal git pull. The only consideration: when this lands on bridge machines, the first PM session after pull warms a fresh prompt cache (one-time 15–20min cold start). The plan's byte-stability mitigation (Risk 1) ensures this happens **only** on the deploy itself, not on every subsequent session.

## Agent Integration

No agent integration required — this is internal to the agent prompt-composition layer. The agent receives the composed prompt via `claude -p --append-system-prompt`; nothing the agent calls (no MCP servers, no CLI tools, no `pyproject.toml` scripts) changes. Tests in `tests/unit/` cover the composer directly without bridge integration.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/composed-persona-system.md` describing: the composer signature, the (persona × access-level) matrix, where to add a new access-level, the byte-stability invariant, and how the drafter's channel split interacts. Reference the seven resolved questions and their answers.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/pm-dev-session-architecture.md` to reference the composer as the prompt source for both PM and Dev sessions (replaces the implicit pointer to `load_pm_system_prompt`).

### External Documentation Site

No external docs site for this repo — skip.

### Inline Documentation

- [ ] Docstring on `compose_system_prompt` describes all parameters, the invariant ordering, and the byte-stability guarantee for the existing four cells.
- [ ] Update docstring on `load_system_prompt` and `load_pm_system_prompt` to note they delegate to the composer.
- [ ] Comment block above `_resolve_compose_args` enumerates every (input → output) cell mapping for grep-able reference.

## Success Criteria

- [ ] `compose_system_prompt(persona, access_level, channel=None, **kwargs)` exists and is the only path that produces a fully assembled agent system prompt; `load_system_prompt` and `load_pm_system_prompt` are thin wrappers.
- [ ] `AccessLevel` enum is defined in `config/enums.py` with members `WORKER`, `PM_READONLY`, `TEAMMATE`, `CUSTOMER_SERVICE`.
- [ ] `_resolve_compose_args(...)` helper exists and is the only branch ladder mapping `SessionType + project + transport → (persona, access_level, channel)`. Both `agent/sdk_client.py:3326` and `agent/session_executor.py:1430` call it.
- [ ] **Byte-stability**: `compose_system_prompt(DEVELOPER, WORKER)` is byte-identical to `load_system_prompt()` from main; `compose_system_prompt(PROJECT_MANAGER, PM_READONLY, working_directory=W)` is byte-identical to `load_pm_system_prompt(W)` from main. Asserted via fixture files in `tests/fixtures/`.
- [ ] Drafter accepts `channel` parameter; default `"telegram"` produces the same prompt as today.
- [ ] `email.persona` per-project override flows through the composer (not as inline branches in either picker site).
- [ ] All seven open architectural questions are answered in the plan body (above) with rationale.
- [ ] PM/Dev/Teammate/Customer-Service sessions continue to work with no observable behaviour change for the existing four overlays.
- [ ] Tests pass (`pytest tests/unit/test_compose_system_prompt.py tests/unit/test_resolve_compose_args.py tests/unit/test_persona_loading.py tests/unit/test_sdk_client_sdlc.py tests/unit/test_message_drafter.py`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Lint clean (`python -m ruff check .`).
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

### 1. Capture byte-stability baselines

- **Task ID**: spike-baseline
- **Depends On**: none
- **Validates**: tests/fixtures/pm_system_prompt_baseline.txt, tests/fixtures/dev_system_prompt_baseline.txt
- **Assigned To**: byte-stability-tester
- **Agent Type**: test-engineer
- **Parallel**: true
- Run a one-off Python script that calls today's `load_system_prompt()` and `load_pm_system_prompt("/Users/tomcounsell/src/work-vault/AI Valor Engels System")` (or whatever working_dir the dev has locally) and writes the byte-exact output to `tests/fixtures/dev_system_prompt_baseline.txt` and `tests/fixtures/pm_system_prompt_baseline.txt`
- Commit the fixtures so the byte-stability test has a stable reference even after the composer ships

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

### 3. Collapse picker sites

- **Task ID**: build-picker-collapse
- **Depends On**: build-composer
- **Validates**: tests/unit/test_resolve_compose_args.py (create)
- **Assigned To**: composer-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the `if _session_type == SessionType.PM ... elif ... elif ...` block at `agent/sdk_client.py:3326–3395` with a single `_resolve_compose_args(...)` call followed by `compose_system_prompt(*args)`
- Replace the equivalent block at `agent/session_executor.py:1430–1486` with the same call
- Confirm both sites produce the same `custom_system_prompt` value as before for every test cell

### 4. Channel-aware drafter

- **Task ID**: build-drafter-channel
- **Depends On**: spike-baseline
- **Validates**: tests/unit/test_message_drafter.py (update), tests/unit/test_drafter_validators.py (update)
- **Assigned To**: drafter-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `channel: str = "telegram"` parameter to `format_for_chat` (and any other public drafter entry points)
- Refactor `DRAFTER_SYSTEM_PROMPT` into `BASE_DRAFTER_PROMPT` (channel-agnostic) + `CHANNEL_RULES = {"telegram": ..., "email": ...}` (initially: `"telegram"` is today's exact text, `"email"` is a stub identical to `"telegram"` minus Telegram-only format rules)
- Add helper `_compose_drafter_prompt(channel)` that returns `BASE_DRAFTER_PROMPT + CHANNEL_RULES[channel]`
- All drafter call sites that don't pass `channel=` get `"telegram"` by default — no behaviour change

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

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-composer
- **Assigned To**: composer-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/composed-persona-system.md`
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
| No leftover picker ladder | `grep -E "if _session_type == SessionType\.(PM\|TEAMMATE\|DEV)" agent/sdk_client.py agent/session_executor.py \| grep -v _resolve_compose_args` | exit code 1 |
| Byte-stability fixture present | `test -f tests/fixtures/pm_system_prompt_baseline.txt && test -f tests/fixtures/dev_system_prompt_baseline.txt` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Channel parameter in composer signature**: the plan resolves Question 4 as "no channel awareness in the working agent." Should `compose_system_prompt` still accept a `channel=None` parameter for forward-compat, or drop it entirely until a concrete need surfaces? Default in the plan: keep the parameter, allow `None`, add a TODO comment that no current cell uses it. Reviewer call.

2. **AccessLevel.CUSTOMER_SERVICE enum member naming**: the customer-service overlay today is loaded via `_load_persona_overlay_with_log("customer-service", ...)` with no rails. If we keep one access level per overlay, we get a 1:1 mapping that arguably defeats the orthogonality goal. Alternative: collapse `TEAMMATE` and `CUSTOMER_SERVICE` access levels into a single `CONVERSATIONAL` level, and let the persona overlay carry the action-orientation difference. Tighter design, but breaks the 1:1 today. Reviewer call.

3. **Where the `voice.md` segment file lives**: the plan defers promoting voice rules to a segment file (Risk 4). Should `voice.md` be created as a stub now (empty file in the manifest) so the follow-up plan is purely a content move, or should it not exist until the follow-up creates it?
