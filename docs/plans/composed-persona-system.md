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
  - [`agent/sdk_client.py`](../../agent/sdk_client.py) L3364–L3434 (`get_response_via_harness` path) — split into a persona-resolution block at L3364–L3380 and a `custom_system_prompt` assembly block at L3396–L3433
  - [`agent/session_executor.py`](../../agent/session_executor.py) L1576–L1628 (the harness-route persona resolution)
  Both branch on `SessionType` plus a `transport == "email"` override that swaps in `project.email.persona`. They are similar but not identical and have drifted independently (issue cited only `sdk_client.py`; freshness check found the second site).
- Two prompt-builder functions exist with hard-baked behavior:
  - `load_system_prompt()` — bakes in the developer persona + `WORKER_RULES` + principal context + completion criteria.
  - `load_pm_system_prompt(working_dir)` — bakes in the project-manager persona, *omits* `WORKER_RULES`, appends work-vault `CLAUDE.md`. Documented invariants in its docstring at sdk_client.py:1027–1031.
  - For teammate / customer-service personas, `_load_persona_overlay_with_log()` is called directly with no rails layer at all.
- Channel awareness leaks into the working-agent prompt: persona segments (`identity.md`, `tools.md`) and the developer overlay describe Telegram-specific behaviour, even though most of those concerns only matter at message-drafting time.
- Voice rules (banned phrases, "no empty promises", good/bad reply examples) are scattered across persona overlays and `bridge/message_drafter.py:1295` `DRAFTER_SYSTEM_PROMPT` with no single source of truth. This plan **observes** the duplication but does not consolidate it (see Risk 4 / No-Gos — voice consolidation is deferred to a follow-up plan to keep the byte-stability mitigation clean).
- `email.persona` per-project override is the only "channel changes the prompt" code path and lives inline in *both* pickers, not in a composer.

**Desired outcome:**

A single `compose_system_prompt(persona, access_level, channel=None, **kwargs)` function is the only path that produces a fully assembled agent system prompt. Both pickers collapse to: derive `(persona, access_level, channel)`, call the composer, return. Channel-specific deltas are pushed into the message drafter (the only place that legitimately needs to know whether output is going to Telegram vs email). The four existing persona overlays continue to work; observable prompt bytes for the `(developer, worker, telegram)` and `(project-manager, pm-readonly, telegram)` cells are byte-identical to today's output (preserving #1227's cache stability invariant).

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:18:37Z (today)
**Disposition:** Minor drift — the issue cites `sdk_client.py` L3326–L3395 as the only picker location, but (a) the actual picker has shifted to L3364–L3434 since the issue was filed, and (b) a second equivalent picker exists in `agent/session_executor.py` L1576–L1628 (introduced for the harness route). The plan must collapse BOTH sites and uses the corrected line ranges throughout.

**File:line references re-verified:**

- `agent/sdk_client.py:892` (`load_persona_prompt`) — still holds
- `agent/sdk_client.py:966` (`load_system_prompt`) — still holds
- `agent/sdk_client.py:998` (`load_pm_system_prompt`) — still holds
- `agent/sdk_client.py:3364–3434` (picker — two blocks: persona resolution at 3364–3380 and `custom_system_prompt` assembly at 3396–3433) — **drifted from issue's cited 3326–3395; corrected here**
- `agent/sdk_client.py:3370–3375` (email-persona override inside the persona-resolution block) — drifted from issue's cited 3331; corrected here
- `bridge/message_drafter.py:1295` (`DRAFTER_SYSTEM_PROMPT`) — still holds
- `bridge/message_drafter.py:1720` (`async def draft_message`) — still holds
- `bridge/message_drafter.py:381` (`_validate_for_medium`) — still holds
- `agent/sdk_client.py:920–947` (load_persona_prompt drift warnings — CRITIQUE / workflow-announcement / dev-session) — still holds; minor drift from cited 919–948
- `agent/sdk_client.py:1800` (`_load_persona_overlay_with_log` definition; exception handler at 1840–1869) — drifted from cited 1837–1853 (the cite was the handler-only range; the function body starts at 1800)
- `agent/sdk_client.py:1027–1031` (`load_pm_system_prompt` "Invariants" docstring section) — drifted from cited 1022–1026
- `agent/sdk_client.py:1037` (work-vault CLAUDE.md read site inside `load_pm_system_prompt`) — still holds
- `config/enums.py` `PersonaType` (4 members) and `SessionType` (3 members) — still holds
- `config/personas/segments/manifest.json` (4 segments) — still holds
- **NEW:** `agent/session_executor.py:1576–1628` — second picker site not cited in the issue but functionally equivalent and must be unified (corrected from rev3's 1430–1486)

**Cited sibling issues/PRs re-checked:**

- #395 — CLOSED. Established persona/session-type split. Successful prior art.
- #1148 — CLOSED. Added PM persona overlay with CRITIQUE/SDLC rules + the loader-warning pattern at sdk_client.py:920–947. Composer must preserve those warnings.
- #1189 — CLOSED. Added the workflow-announcement guard (loader warning at L931–939). Composer must preserve.
- #1227 — CLOSED. Established the byte-stable PM prompt-prefix invariant for Anthropic prompt cache. Composer must preserve byte-for-byte for the PM cell.

**Commits on main since issue was filed (touching referenced files):** none

**Active plans in `docs/plans/` overlapping this area:**

- `pm-persona-hardening.md` (#1007, In Progress) — adds three new sections to the PM overlay file. Scope is the PM overlay *content*, not the loader. **Not blocking.** Composer treats the overlay as opaque text.
- `unify-persona-vocabulary.md` (#599, Draft) — eliminates `ChatMode` enum and renames `qa_*` to `teammate_*`. Plan touches `config/enums.py` and the picker. **Coordination signal:** if `unify-persona-vocabulary.md` lands first, this plan picks up `PersonaType` cleanly; if this plan lands first, that plan rebases against the new composer call site. Both plans agree on `PersonaType` as the canonical enum — there is no semantic conflict.

**Notes:** The two-site picker is the most important freshness finding — without it the plan would only collapse one branch ladder and leave a parallel one in `session_executor.py` to drift again.

## Prior Art

- **#395** (CLOSED) — Multi-persona system. Established PersonaType / SessionType split and the project-manager overlay. Successful; this plan builds on it.
- **#1148** (CLOSED) — PM persona overlay with CRITIQUE/SDLC rules. Added inline loader warnings at `load_persona_prompt` (L920–947). Composer must keep those warnings.
- **#1189** (CLOSED) — Workflow-announcement rule. Added the second loader warning. Composer must keep.
- **#1227** (CLOSED) — PM prompt cache stability via `--exclude-dynamic-system-prompt-sections`. Established the byte-stable prefix property. Composer must preserve byte-for-byte.
- **#599** (DRAFT plan) — Unifying persona vocabulary; not blocking but coordinated.

## Research

External research skipped — this is an internal refactor with no new dependencies, no external library upgrades, and no API contracts changing. All mechanics (file layout, manifest, segment ordering) are repo-internal Python.

## Architectural Impact

- **New types**: `AccessLevel` enum in `config/enums.py` (or equivalent canonical declaration). Members: `WORKER` (full permissions + WORKER_RULES), `PM_READONLY` (PM mode, no WORKER_RULES, with work-vault CLAUDE.md), `TEAMMATE` (conversational, no rails), `CUSTOMER_SERVICE` (action-oriented, no code writes).
- **New function**: `compose_system_prompt(persona, access_level, channel=None, *, project=None, working_directory=None) -> str` in `agent/sdk_client.py` (or a new `agent/persona_composer.py` if extraction is preferred — the plan defaults to keeping it adjacent to the existing loaders to keep the diff focused).
- **Removed/redirected functions**: `load_system_prompt()` and `load_pm_system_prompt()` become thin wrappers that call `compose_system_prompt(...)` with the right tuple. `_load_persona_overlay_with_log()` stays as a logging adapter but now delegates the actual composition to `compose_system_prompt`.
- **Picker collapse**: both `sdk_client.py:3364–3434` (two blocks: persona resolution at 3364–3380 and `custom_system_prompt` assembly at 3396–3433) and `session_executor.py:1576–1628` collapse to a single helper `_resolve_compose_args(session_type, project, transport, ...) -> (persona, access_level, channel)` that lives in one place and is called from both sites.
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

### Flow

**Working-agent prompt assembly:**

Session arrives at executor → `_resolve_compose_args(session_type, project, transport)` returns `(persona, access_level, channel)` → `compose_system_prompt(persona, access_level, channel, project=project, working_directory=working_dir)` returns the full prompt string → string passed to `claude -p` via `--append-system-prompt`.

**Drafter prompt assembly:**

Worker emits agent output → `bridge/message_drafter.py:draft_message(raw_response, session=None, *, medium="telegram", persona=None)` (the existing public entry point at `bridge/message_drafter.py:1720`) → drafter composes `BASE_DRAFTER_PROMPT + MEDIUM_RULES["telegram"]` (or `"email"`, etc.) → calls Haiku via `client.messages.create(system=composed_prompt, ...)`.

**Note on naming:** the function is `draft_message` (not `format_for_chat`) and the parameter is `medium` (not `channel`). The plan uses `medium` consistently because (a) that's the existing parameter name on `draft_message`, (b) `_validate_for_medium(text, medium)` already exists at `bridge/message_drafter.py:381`, and (c) renaming `medium` to `channel` is out of scope. The composer can still be named with `channel` internally because no working-agent prompt cell uses it today (Question 4 / Open Question 1) — but the drafter's public surface stays as `medium=`.

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

- **Picker collapse**: extract `_resolve_compose_args` into a private helper at `agent/sdk_client.py` near `_resolve_persona` (which today lives at L1873). Both call sites (sdk_client.py:3364 entry to the persona-resolution block and session_executor.py:1576) call this helper. The helper encapsulates the email-persona override (`if transport == "email" and project.email.persona: persona = ...`).
- **Backward compatibility**: `load_system_prompt()` and `load_pm_system_prompt(work_dir)` remain as wrappers (one-line implementations that call the composer). All existing call sites continue to work without change. New code path: `compose_system_prompt(...)` direct.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_load_persona_overlay_with_log` already has structured exception handling for missing overlays (sdk_client.py:1840–1869, function defined at L1800). The composer must NOT swallow exceptions silently — it raises `FileNotFoundError` for missing required overlays, matching `load_persona_prompt`'s behavior at sdk_client.py:960–963. Tests assert that the composer raises (not returns empty string) for unknown personas with no fallback.
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

- [ ] `tests/unit/test_persona_loading.py` — UPDATE: `load_system_prompt` and `load_pm_system_prompt` now delegate to `compose_system_prompt`; existing tests (lines 164–186, 301) still pass because the wrappers preserve byte output. Add a new test `test_load_system_prompt_byte_stable_through_composer` that asserts the wrapper output equals the direct composer output for the `(DEVELOPER, WORKER)` cell.
- [ ] `tests/unit/test_sdk_client_sdlc.py` — UPDATE: `WORKER_RULES` constant unchanged; the test that asserts WORKER_RULES is prepended (currently against `load_system_prompt()`) is rewritten to assert against `compose_system_prompt(persona=DEVELOPER, access_level=WORKER)`. Add a regression: `compose_system_prompt(persona=PROJECT_MANAGER, access_level=PM_READONLY, working_directory=...)` does NOT contain `WORKER_RULES` substring (preserves the load_pm_system_prompt invariant from sdk_client.py:1023).
- [ ] `tests/unit/test_message_drafter.py` — UPDATE: drafter system prompt is now `BASE + MEDIUM_RULES["telegram"]`. Existing tests should pass unchanged because the default `medium="telegram"` produces the same text. Add a new test asserting `draft_message(raw_response, session=None, medium="email")` uses different format rules. The tested function is `draft_message` at `bridge/message_drafter.py:1720`; signature is `async def draft_message(raw_response: str, session=None, *, medium: str = "telegram", persona: str | None = None) -> MessageDraft`.
- [ ] `tests/unit/test_drafter_validators.py` — UPDATE: any test that imports `DRAFTER_SYSTEM_PROMPT` directly is updated to import the composed result via the new `_compose_drafter_prompt(channel)` helper.
- [ ] `tests/unit/test_pm_persona_guards.py` — no change. The PM overlay loader-warning tests at sdk_client.py:920–947 are preserved; they're inside `load_persona_prompt`, which stays as the segment-and-overlay assembler.
- [ ] `tests/unit/test_message_drafter_chat_log.py`, `test_message_drafter_linkify.py` — UPDATE only if they import `DRAFTER_SYSTEM_PROMPT` directly; otherwise no change.
- [ ] **NEW** `tests/unit/test_compose_system_prompt.py` — create. Covers: (1) byte-stability for `(DEVELOPER, WORKER)` and `(PROJECT_MANAGER, PM_READONLY)` cells against the wrappers' output captured from main; (2) one test per cell of the (persona × access-level) matrix; (3) startup-lint invariants (PM under 80K chars, no `{{identity.*}}` markers, WORKER_RULES precedes overlay).
- [ ] **NEW** `tests/unit/test_resolve_compose_args.py` — create. Covers: each `(SessionType, project_mode, transport, project.email.persona)` input cell maps to the expected `(persona, access_level, channel)` output. Replaces inline branch-by-branch testing of the two pickers.

No existing integration tests reference the prompt-byte content directly, so no integration test impact.

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
- Refactoring the drafter beyond system-prompt composition.
- Adding new persona overlays (`developer`, `project-manager`, `teammate`, `customer-service` are the only four; new ones are a separate plan).
- Channel-awareness in the working-agent prompt (resolved as "none" per Question 4; revisit only if a concrete need surfaces during build).
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

- [ ] `compose_system_prompt(persona, access_level, channel=None, **kwargs)` exists and is the only path that produces a fully assembled agent system prompt; `load_system_prompt` and `load_pm_system_prompt` are thin wrappers.
- [ ] `AccessLevel` enum is defined in `config/enums.py` with members `WORKER`, `PM_READONLY`, `TEAMMATE`, `CUSTOMER_SERVICE`.
- [ ] `_resolve_compose_args(...)` helper exists and is the only branch ladder mapping `SessionType + project + transport → (persona, access_level, channel)`. Both `agent/sdk_client.py:3364` (entry to the persona-resolution block) and `agent/session_executor.py:1576` call it.
- [ ] **Byte-stability (per-machine)**: `compose_system_prompt(DEVELOPER, WORKER)` is byte-identical to `load_system_prompt()` from main on the local machine; `compose_system_prompt(PROJECT_MANAGER, PM_READONLY, working_directory=W)` is byte-identical to `load_pm_system_prompt(W)` from main on the local machine. Asserted via fixture files at `tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt`. Test SKIPs on machines without a captured baseline (with a clear pointer to `scripts/capture_persona_baseline.py`).
- [ ] Drafter `medium` parameter (already exists on `draft_message` at `bridge/message_drafter.py:1720`) now drives prompt selection; default `"telegram"` produces the same prompt bytes as today.
- [ ] `email.persona` per-project override flows through the composer (not as inline branches in either picker site).
- [ ] All seven open architectural questions are answered in the plan body (above) with rationale.
- [ ] PM/Dev/Teammate/Customer-Service sessions continue to work with no observable behaviour change for the existing four overlays.
- [ ] No new segments added to `config/personas/segments/manifest.json` (voice consolidation is explicitly deferred).
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
- Replace the `if _session_type == SessionType.PM ... elif ... elif ...` blocks at `agent/sdk_client.py:3364–3434` (both the persona-resolution ladder at 3364–3380 and the `custom_system_prompt` assembly ladder at 3396–3433) with a single `_resolve_compose_args(...)` call followed by `compose_system_prompt(*args)`
- Replace the equivalent block at `agent/session_executor.py:1576–1628` with the same call
- Confirm both sites produce the same `custom_system_prompt` value as before for every test cell

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
| No leftover picker ladder | `grep -E "if _session_type == SessionType\.(PM\|TEAMMATE\|DEV)" agent/sdk_client.py agent/session_executor.py \| grep -v _resolve_compose_args` | exit code 1 |
| Byte-stability fixture present (per-machine) | `python -c "import socket; from pathlib import Path; m=socket.gethostname().replace('.','-'); assert (Path('tests/fixtures')/m/'pm_system_prompt_baseline.txt').exists() and (Path('tests/fixtures')/m/'dev_system_prompt_baseline.txt').exists(), f'missing baseline for {m}; run scripts/capture_persona_baseline.py'"` | exit code 0 |
| Manifest unchanged | `git diff main -- config/personas/segments/manifest.json` | empty output |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER  | (cycle-1 critique) | voice.md scope contradiction — plan simultaneously claimed voice.md was a new segment in `manifest.json` (Solution Key Element 5; Question 2 + Question 5 resolutions) AND was NOT created in this plan because doing so breaks byte-stability (Risk 4 + No-Gos). | Solution Key Element on voice rules; Technical Approach Question 2 + Question 5; Risk 3; Risk 4; No-Gos; Update System; Step 2 + Step 4 + Step 6 task notes; Documentation; Success Criteria; Verification table. | Scrubbed all references to creating `config/personas/segments/voice.md` in this plan. Voice consolidation is now uniformly framed as **deferred to a follow-up plan**. The composition order in Question 5 no longer includes a `voice` segment (matches today's `manifest.json` exactly). The drafter's `BASE_DRAFTER_PROMPT` retains today's voice content verbatim — no extraction. A new Success Criterion + Verification check assert `manifest.json` is unchanged. Risk 4's framing is preserved as authoritative. |
| BLOCKER  | (cycle-2 critique) | Byte-stability fixture not machine-stable — single fixture file would fail on bridge machines because composed prompt embeds `working_directory` paths, `{{identity.*}}` overrides from `~/Desktop/Valor/identity.json`, and per-machine work-vault `CLAUDE.md` content. | Risk 1 (full rewrite); Step 1 (capture baselines); Update System; Success Criteria byte-stability bullet; Verification table fixture-present check. | Adopted strategy (c): per-machine snapshots stored at `tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt`, with `machine_name` derived from `socket.gethostname()`. Test SKIPs (not FAILs) on machines without a baseline, pointing developers at `scripts/capture_persona_baseline.py`. Strategies (a) token normalization and (b) structural equality were considered and rejected with explicit rationale (both break the actual #1227 cache invariant, which depends on byte-identical runtime prompts). |
| BLOCKER  | (cycle-2 critique) | `format_for_chat` doesn't exist — plan referenced a non-existent function on the drafter; actual API is `draft_message(raw_response, session=None, *, medium="telegram", persona=None)` at `bridge/message_drafter.py:1720`. | Solution → Flow → Drafter prompt assembly; Solution → Key Elements channel-aware drafter bullet; Technical Approach Question 3; Test Impact `tests/unit/test_message_drafter.py` row; Step 4 (drafter channel-split task). | Replaced every `format_for_chat` reference with `draft_message`. Replaced the parameter name `channel` with `medium` everywhere the drafter's *public surface* is described (Step 4, Test Impact, Flow, Question 3). The composer's internal parameter naming (`channel=None` in the working-agent composer) is unaffected because no working-agent cell uses it today (Question 4 / Open Question 1). The signature `async def draft_message(raw_response: str, session=None, *, medium: str = "telegram", persona: str | None = None) -> MessageDraft` already exists at `bridge/message_drafter.py:1720`–1747; the existing `medium` parameter is wired through to `_validate_for_medium` only today, and this plan extends it to also drive prompt selection (`BASE_DRAFTER_PROMPT + MEDIUM_RULES[medium]`). No new public parameter is introduced. |
| BLOCKER  | (cycle-3 critique) | Stale file:line references — the picker location cited throughout the plan (`sdk_client.py:3326–3395`, `:3331`, `session_executor.py:1430–1486`) had drifted. Actual current locations on main: persona-resolution block at `sdk_client.py:3364–3380`, `custom_system_prompt` assembly block at `:3396–3433`, email-persona override at `:3370–3375`, second-site picker at `session_executor.py:1576–1628`. Several other cited ranges (`load_persona_prompt` warnings 919–948 → 920–947; `_load_persona_overlay_with_log` 1837–1853 → handler 1840–1869 with function defined at 1800; `load_pm_system_prompt` invariants 1022–1026 → 1027–1031) had also drifted by 1–5 lines. | Freshness Check `File:line references re-verified` block (full re-verification against current main); Problem section `Two parallel pickers exist` bullet; Prior Art #1148 entry; Architectural Impact `Picker collapse` bullet; Technical Approach `Picker collapse` bullet; Failure Path Test Strategy `_load_persona_overlay_with_log` bullet; Test Impact `test_pm_persona_guards.py` row; Success Criteria `_resolve_compose_args` bullet; Step 3 task body. | Re-grepped every cited symbol against current `agent/sdk_client.py`, `agent/session_executor.py`, and `bridge/message_drafter.py` HEAD. Updated all 11 stale references to current line numbers. Added `_resolve_persona` (L1873), `draft_message` (L1720), `_validate_for_medium` (L381) to the verified-references list for completeness. Disposition for the Freshness Check block changed to acknowledge the cycle-3 drift was caught and corrected. The freshness-check pattern itself is now durable: every revision cycle re-verifies cited references against current main. |

---

## Open Questions

1. **Channel parameter in composer signature**: the plan resolves Question 4 as "no channel awareness in the working agent." Should `compose_system_prompt` still accept a `channel=None` parameter for forward-compat, or drop it entirely until a concrete need surfaces? Default in the plan: keep the parameter, allow `None`, add a TODO comment that no current cell uses it. Reviewer call.

2. **AccessLevel.CUSTOMER_SERVICE enum member naming**: the customer-service overlay today is loaded via `_load_persona_overlay_with_log("customer-service", ...)` with no rails. If we keep one access level per overlay, we get a 1:1 mapping that arguably defeats the orthogonality goal. Alternative: collapse `TEAMMATE` and `CUSTOMER_SERVICE` access levels into a single `CONVERSATIONAL` level, and let the persona overlay carry the action-orientation difference. Tighter design, but breaks the 1:1 today. Reviewer call.

3. **Follow-up plan for voice consolidation**: this plan defers voice consolidation entirely (see Question 2 / Risk 4). Should a placeholder follow-up issue be filed as part of this plan's PR (so the work is tracked), or should we wait until the composer ships and stabilizes before opening the follow-up? Default in the plan: file the follow-up issue at PR-merge time, not at plan-commit time, so it can reference the actual landed composer.
