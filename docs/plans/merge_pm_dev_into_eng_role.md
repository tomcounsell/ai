---
status: NeedsRevision
type: chore
appetite: Large
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1633
last_comment_id: 4682868787
revision_applied: false
---

# Merge PM/Dev bridge roles into a single Eng role; collapse SessionType to {eng, teammate}

## Problem

The bridge derives a work session's role from which paired Telegram group a message
arrived in: `PM: {Project}` → `session_type="pm"` (orchestrator, read-only) and
`Dev: {Project}` → `session_type="dev"` (builder, full permissions). Persona resolution
in `bridge/routing.py:resolve_persona()` matches the group name against `projects.json`,
with a `Dev:`/`PM:` title-prefix fallback.

PR #1612 (the granite PTY container cutover, merged 2026-06-11) changed execution so that
**every** bridge-originated work session runs a container that internally owns *both* a
PM-steering and a Dev-builder `claude` TUI. The PM/Dev split now lives inside the container,
making the bridge-level split redundant — and, in the dev case, semantically broken.

`SessionType` collapses to `{eng, teammate}` (GRANITE retained as a CLI-only type; see Resolved
Decisions) — i.e. the *bridge-originated work* discriminator becomes two values, while the CLI-only
`granite` type added by #1635 survives untouched.

**Current behavior:**
- 13 projects across 4 machines carry paired (or partial) `PM:`/`Dev:` groups, forcing
  humans to pick the "right" group before messaging.
- Post-#1612, a message to a `Dev:` group creates a `session_type="dev"` AgentSession that
  spawns a granite container containing its *own* PM+Dev pair, then falls into
  `_handle_dev_session_completion()` (`agent/session_completion.py:1454`) with no parent PM
  to steer — the bridge-level dev type no longer means anything on the execution path.
- The bridge-level persona is largely vestigial for work sessions: the container primes
  personas itself, and both its PTYs run `claude --permission-mode bypassPermissions`, so the
  `SESSION_TYPE`-env permission rails in `agent/hooks/pre_tool_use.py` never reach inside the
  container.
- Dual groups split conversational history and create dual chat-scoped state for what is one
  engineering workstream per project.

**Desired outcome:**
- One `Eng: {Project}` Telegram group per project replaces the `PM:`/`Dev:` pair.
- `SessionType` for bridge-originated work collapses to `{eng, teammate}`: `pm` is renamed to
  `eng`, `dev` is deleted entirely. (`granite` — added post-issue by #1635 — is retained as a
  CLI-only type; see Freshness Check. Confirmed by supervisor decision.)
- The Dev group's Telegram chat history is merged into the Eng chat so nothing is lost;
  subconscious memories require no migration (project-scoped, not chat-scoped).
- Rollout is staged by machine, piloted on **Valor the Cowboy** (projects `valor`, `popoto`)
  before the other three machines.

## Freshness Check

**Baseline commit:** 565bd9f67f06267cd75d05dbc08be27212975220
**Issue filed at:** 2026-06-11T08:01:44Z
**Disposition:** Minor drift

**File:line references re-verified (all against baseline):**
- `bridge/routing.py:339-396` `resolve_persona()` — **holds.** `Dev:`/`PM:` prefix fallback at
  lines 390-393 maps to `PersonaType.DEVELOPER`/`PROJECT_MANAGER`. `_is_team_chat` at line 331
  also checks `("Dev:", "PM:")` prefixes.
- `agent/session_completion.py:1454` `_handle_dev_session_completion` — **holds** (def at 1454).
  Woven into `agent/session_executor.py` (imported L17; called L1915), `agent/session_health.py`,
  and referenced by `agent/output_router.py` — wider blast radius than a single function.
- `config/enums.py:17` `SessionType` — **DRIFTED (new value added).** Now contains
  `PM="pm"`, `TEAMMATE="teammate"`, `DEV="dev"`, **and `GRANITE="granite"`** (lines 28-31).
  `PersonaType` (L35) has `DEVELOPER`, `PROJECT_MANAGER`, `TEAMMATE`, `CUSTOMER_SERVICE`.
- `agent/sdk_client.py:1168` access-level resolution — **holds.** `if session_type == SessionType.PM:
  return PROJECT_MANAGER, PM_READONLY`; `project_mode == "pm"` forcing; `SessionType.DEV` falls
  through to `_resolve_persona`. `compose_system_prompt` keys on `(persona, access_level)` (L1019+).
- `bridge/email_bridge.py:879` persona→PM mapping — **holds.** `session_type = TEAMMATE if
  email_persona in ("teammate","customer-service") else PM`.
- `scripts/migrate_session_type_chat_to_pm.py` — **holds.** KeyField-rename precedent using raw
  Redis `rename` + `hset` + `scan` (KeyField mutation cannot go through normal ORM save) then
  `AgentSession.rebuild_indexes()`. Idempotent, `--dry-run`, "stop the bridge first".
- `tools/telegram_history/__init__.py:~1097` `Chat` delete-recreate — **holds.** `chat_name` is a
  KeyField; rename = `chat.delete()` then `Chat.create(...)` via ORM.
- `tools/sdlc_decompose.py` — **holds** (exists; `pyproject.toml:91` `sdlc-decompose` entry).
- `agent/sdlc_router.py:65,77` `MAX_PARALLEL_DEVS = 3`, `PARALLEL_SAFE_PAIRS` — **holds.**

**Cited sibling issues/PRs re-checked:**
- #1612 (granite PTY cutover) — **MERGED 2026-06-11.** Hard prerequisite now satisfied.
- #1635 (granite CLI session visibility) — **MERGED 2026-06-11T17:14:58Z**, *after* this issue was
  filed. Added `SessionType.GRANITE = "granite"` to `config/enums.py`, used exclusively by
  `valor-granite-loop` (`tools/granite_interactive_tui_poc/cli.py`). The issue author posted a
  comment (2026-06-11) flagging that this issue's "exactly ENG and TEAMMATE" AC now needs to
  decide GRANITE's fate. **This plan's disposition:** retain GRANITE as a CLI-only type, distinct
  from bridge-originated work; narrow the AC to "no `pm`/`dev` value remains" rather than
  "exactly ENG and TEAMMATE". **Confirmed by supervisor decision** — GRANITE retained as a CLI-only
  type; AC narrowed to "no `pm`/`dev` value remains anywhere".
- #652 (CHAT→PM rename + TEAMMATE) — closed/merged; the direct precedent for this rename and the
  origin of `scripts/migrate_session_type_chat_to_pm.py`.
- #1409 (multi-dev fan-out) — merged May 2026; recon found no production invocation / no e2e test,
  so it is deleted here, not migrated.

**Commits on main since issue was filed (touching referenced files):**
- `#1635` granite CLI visibility — touched `config/enums.py` (added GRANITE) — **changed the enum
  this plan rewrites**; reconciled above and in Resolved Decisions #1 (GRANITE retained CLI-only).
- `52740fbb` make granite a hard startup precondition — granite container path; irrelevant to the
  enum/persona rename surface.

**Active plans in `docs/plans/` overlapping this area:** none. `gemma4_ollama_consolidation.md`
and `granite_pty_production_cutover.md` (already shipped) touch the granite container but not the
SessionType/persona/bridge-routing surface this plan rewrites.

**Notes:** The only material drift is the new `SessionType.GRANITE`. Root cause and approach are
unchanged. All file:line pointers verified accurate against baseline.

## Prior Art

- **PR #652**: Rename SessionType.CHAT to PM + add TEAMMATE as first-class type — **succeeded.**
  This is the direct template for the present work: a KeyField rename of `session_type` plus enum
  surgery across hooks/router/CLI/dashboard. It produced `scripts/migrate_session_type_chat_to_pm.py`,
  the exact migration pattern reused here (raw Redis `rename` for the KeyField + `rebuild_indexes()`).
- **PR #1612 / issue #1572**: Granite PTY container production cutover — **merged.** The hard
  prerequisite. It moves the PM/Dev split inside the container, which is *why* the bridge-level split
  is now redundant. Its executor comment (`agent/session_executor.py` region) anticipates this
  follow-on.
- **PR #1635 / issue (granite CLI visibility)**: added `SessionType.GRANITE` — **merged after this
  issue filed.** Forces an explicit GRANITE disposition (retained as CLI-only here).
- **Issue #1409**: Multi-dev fan-out + DAG dispatch — **merged but never invoked in production, no
  e2e test.** Deleted here rather than migrated; "parallel eng containers" can be rebuilt later on
  the surviving child-session machinery.

## Data Flow

End-to-end trace of a bridge work message under the new model:

1. **Entry point:** Human sends a message to `Eng: {Project}` Telegram group.
2. **`bridge/routing.py:resolve_persona()`:** Config match resolves `engineer` persona; the title
   prefix fallback now matches `Eng:` only (`Dev:`/`PM:` branches deleted). `_is_team_chat` updates
   its prefix tuple to `("Eng:",)`.
3. **`bridge/telegram_bridge.py`:** Maps resolved engineer persona → `SessionType.ENG`. Creates an
   AgentSession with `session_type="eng"`.
4. **`agent/sdk_client.py` (`_resolve_*` at ~1168 + `compose_system_prompt` at ~1019):** Resolves
   `(PersonaType.ENGINEER, AccessLevel.WORKER, channel)` for `SessionType.ENG`. Eng is the builder
   identity now; the old PM read-only rails are dead on the granite path (container PTYs run
   `bypassPermissions`), so a non-container/CLI eng session resolves `WORKER` (full rails).
   `VALOR_PARENT_SESSION_ID` injection (~1595), currently gated on PM/Teammate, follows the rename to
   gate on ENG/Teammate.
5. **Worker / granite container:** Executes the eng session through the granite PTY container, which
   internally primes its own PM-steering + Dev-builder TUIs (both `bypassPermissions`). The bridge
   no longer steers a separate dev child session; `_handle_dev_session_completion()` and its
   parent-steering path are deleted.
6. **Output:** `TelegramRelayOutputHandler` writes the container's reply to the Redis outbox →
   delivered to the `Eng: {Project}` group.

**Conversational vs. work path (CONCERN, User):** the old `PM:` group doubled as a lightweight
"just ask a question / steer" surface. Under the Eng model every `Eng:` message resolves
`AccessLevel.WORKER` and spawns a granite container, so the lightweight surface moves to the existing
`Teammate: {Project}` group (conversational Teammate persona, no container spin-up). The
group-routing contract becomes: **`Teammate: {Project}` = ask a question / discuss; `Eng: {Project}`
= do work.** This split is documented explicitly in CLAUDE.md and the renamed architecture doc so the
human's group-routing muscle memory has a clear replacement for the retired `PM:` group.

**Migration-time data flow (per machine, atomic):**
1. Telegram rename `PM: {Project}` → `Eng: {Project}` (preserves chat_id, members, history). Archive
   `Dev: {Project}` (or, for Dev-only projects, rename `Dev:` → `Eng:`).
2. Edit vault `projects.json`: replace `pm`/`dev` group declarations with the single `Eng:` group;
   set persona to `engineer`. Validated by `bridge/config_validation.py` at update Step 4.6.
3. `/update` on the machine: pulls code, restarts bridge on validated config.
4. Local Redis: run `session_type` `pm`→`eng` rename migration + (for projects with a separate Dev
   group being archived) the chat-history merge tool to re-key Dev `TelegramMessage` records onto the
   Eng chat_id.

## Architectural Impact

- **New dependencies:** None. Pure internal refactor; no new libraries/services/APIs.
- **Interface changes:**
  - `SessionType` enum: `PM`→`ENG`, `DEV` removed, `GRANITE` retained.
  - `PersonaType`: `DEVELOPER`+`PROJECT_MANAGER` → single `ENGINEER`.
  - `valor-session create --role`: accepts `eng`/`teammate`; rejects `dev`/`pm`.
  - `bridge/routing.resolve_persona`: returns `engineer`; prefix fallback `Eng:` only.
  - Deletes the public `tools/sdlc_decompose.py` CLI (`sdlc-decompose` pyproject entry) and the
    `_handle_dev_session_completion` completion path.
- **Coupling:** **Decreases.** Removes the dev-child parent-steering coupling between bridge sessions
  and the granite container's internal dev; removes the dual-group chat-state duplication.
- **Data ownership:** `Chat`/`TelegramMessage` for a project consolidate onto one Eng chat_id.
  AgentSession discriminator space shrinks. `Memory` ownership unchanged (project_key only).
- **Reversibility:** Code is a clean cutover (no fallback) — reverting means reverting the PR.
  The Telegram group renames and Redis key renames are operational and machine-local; the chat-merge
  re-key is effectively one-way (re-keyed messages would need a reverse migration). Staging on Cowboy
  first bounds blast radius.

## Appetite

**Size:** Large

**Team:** Solo dev, PM (orchestration), code reviewer

**Interactions:**
- PM check-ins: 1-2 (per-machine rollout gating; GRANITE disposition and eng access-level are now
  decided — GRANITE retained CLI-only, eng resolves `AccessLevel.WORKER`)
- Review rounds: 2+ (enum/hook surgery correctness; migration-script dry-run review before live run)

This spans ~22 code files plus two data-migration scripts, two persona-file merges, a multi-doc
update, and a staged per-machine ops rollout. The coding is mechanical but wide; the risk lives in the
KeyField migrations and the eng access-level decision.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1612 merged | `gh pr view 1612 --json state -q .state` (expect `MERGED`) | Granite container is the execution substrate this plan assumes |
| Bridge stoppable on target machine | `./scripts/valor-service.sh status` | Migrations require the bridge stopped before Redis key renames |
| **Worker stoppable on target machine** | `./scripts/valor-service.sh worker-status` | **The worker (`python -m worker`) is a *separate* process that also writes AgentSessions and whose hourly `agent-session-cleanup` + reflection scheduler (`agent/reflection_scheduler.py:571` creates `session_type="pm"` sessions) can write `pm` records mid-migration. It MUST be stopped — and stay down — before the `pm→eng` rename loop (BLOCKER B3).** Use `worker-disable` to also suppress launchd auto-respawn during the migration window. |
| Redis reachable | `python -c "import popoto; popoto.redis_db.get_REDIS_DB().ping()"` | Migration + ORM operations |

Run all checks: `python scripts/check_prerequisites.py docs/plans/merge_pm_dev_into_eng_role.md`

## Solution

### Key Elements

- **Enum collapse (`config/enums.py`):** `SessionType` → `{ENG, TEAMMATE, GRANITE}` (`pm`→`eng`,
  `dev` removed, `granite` retained CLI-only). `PersonaType.DEVELOPER`+`PROJECT_MANAGER` → `ENGINEER`.
  `AccessLevel`/`SessionMode` reconciled so `SessionType.ENG` resolves `AccessLevel.WORKER`.
- **Persona merge:** `config/personas/project-manager.md` + `developer.md` → one
  `config/personas/engineer.md`. Update `manifest.json` / segment references.
- **Bridge routing:** `bridge/routing.py` resolves `engineer`; `Eng:` prefix fallback only;
  `Dev:`/`PM:` branches and `_is_team_chat` prefix tuple deleted. `bridge/telegram_bridge.py` and
  `bridge/email_bridge.py` map to `SessionType.ENG`.
- **SDK client:** `agent/sdk_client.py` `(persona, access_level, channel)` resolution and
  `compose_system_prompt` updated for `(ENGINEER, AccessLevel.WORKER)`. `VALOR_PARENT_SESSION_ID`
  injection re-gated on ENG/Teammate and verified to propagate into the container's pooled PTYs.
- **Dev machinery removal:** delete `_handle_dev_session_completion()` and its *dev-only*
  callers/parent-steering in `session_executor.py` / `session_health.py` / `output_router.py` **and the
  re-export in `agent/agent_session_queue.py:49-53`** (B1) — surgically, leaving the shared
  `_transition_parent` and any surviving `_create_continuation_pm` callers intact; delete
  `tools/sdlc_decompose.py` + its pyproject entry + `MAX_PARALLEL_DEVS`/`PARALLEL_SAFE_PAIRS` in
  `agent/sdlc_router.py`; delete the `--role dev`/`--role pm` paths in `tools/valor_session.py` (+
  `valor_cli.py`, `sdlc_session_ensure.py`, `agent_session_scheduler.py`); remove the PM read-only Bash
  rails in `agent/hooks/pre_tool_use.py` for work sessions; **delete the four `project_mode == "pm"`
  config-side-channel guards in `agent/sdk_client.py` (B2)**; update `ui/data/sdlc.py` display mapping.
- **Data migrations (two scripts):**
  - `scripts/migrate_session_type_pm_to_eng.py` — clone of the #652 precedent; rename
    `session_type=pm` AgentSession Redis keys to `eng` (raw `rename` + `hset` for the embedded
    KeyField), then `AgentSession.rebuild_indexes()`. `--dry-run`, idempotent, **"stop the bridge AND
    worker"**, with a **worker-heartbeat guard** (`sys.exit(1)` on fresh heartbeat, B3). Reads
    `session_type` directly (NOT the deprecated `session_mode`); skips `:dev:` keys as no-ops.
  - `scripts/merge_dev_chat_into_eng.py` — re-key a Dev group's `TelegramMessage` records onto the
    Eng chat_id (KeyField re-key, same raw-`rename` pattern, since chat_id is in the key), with an
    **EXISTS-check before every rename** (skip-on-collision, never clobber — B5) and a dry-run
    collision report; `Chat` rename via ORM **create-then-delete** (Eng created+verified before Dev
    deleted); **pre/post count assertion** for partial-failure recovery. Project-scoped, `--dry-run`,
    idempotent. Same worker-heartbeat guard. Running it is a per-project operator decision.
- **Staged per-machine rollout:** code lands on main once; each machine migrates atomically
  (Telegram renames + vault `projects.json` edit + `/update` + local Redis migrations). Order: Cowboy
  (valor, popoto) pilot → Captain (cuttlefish, psyoptimal, royop) → Pirate (mondayflowers, gato,
  satsol, pba + 4 Dev-only) → Bald (cyndra).

### Flow

`Eng: {Project}` group → human message → `resolve_persona` (engineer) → `SessionType.ENG`
AgentSession → granite container (own PM+Dev TUIs) → reply delivered to `Eng: {Project}`.

Migration: stop bridge → Telegram rename PM→Eng / archive-or-rename Dev → edit vault `projects.json`
→ `/update` → run `migrate_session_type_pm_to_eng.py` → (if Dev group archived) run
`merge_dev_chat_into_eng.py` → restart bridge → verify `valor-telegram read --chat "Eng: {Project}"`.

### Technical Approach

- **No legacy fallbacks** (NO LEGACY CODE): delete `Dev:`/`PM:` branches outright; no feature gate,
  no dual-path. Staging is per-machine ops, not code.
- **KeyField migrations use raw Redis `rename`** — the sanctioned exception, because a KeyField value
  is embedded in the Redis key string and cannot be changed through a normal ORM `save()`. The
  `validate_no_raw_redis_delete.py` hook only fires on inline **Bash** commands containing both a
  Popoto marker and a forbidden read/delete op; it does not scan committed `.py` migration scripts, so
  the precedent pattern is permitted in a script file. All *non-KeyField* data work (e.g. `Chat`
  recreate, reading records) goes through the ORM.
- **Sequence the enum change carefully:** `config/enums.py` is imported nearly everywhere; land the
  enum rename, persona merge, routing, SDK-client, CLI, hook, and dashboard edits together so the tree
  is never half-migrated (the test suite must be green on the same commit).
- **Deploy ordering — migration BEFORE `/update` (BLOCKER B4, decided):** code lands on main once,
  removing `SessionType.PM`. But each machine migrates Redis at a different time. If a machine runs
  `/update` (pulling the `PM`-less code) *before* its `pm→eng` Redis migration, the worker will pop
  existing `session_type=pm` records against code where `SessionType.PM` no longer exists —
  `session_type == SessionType.PM` against a deleted member simply **never matches** (no crash), so the
  record falls through to the default/`else` persona+access-level path in `sdk_client.py:1168` and
  `session_executor.py` → **silent wrong behavior**, not a loud failure. **Resolution (chosen over a
  one-release `PM = "eng"` alias shim, per NO LEGACY CODE):** the per-machine runbook **mandates the
  ordering: stop bridge+worker → run `migrate_session_type_pm_to_eng.py` → THEN `/update`**. The
  migration drains all `pm` records to `eng` while the old `SessionType.PM` member still exists in the
  *currently-running* (pre-update) code, so every comparison is valid throughout the rename. No alias
  shim is introduced. This ordering is baked into the Update System runbook.
- **GRANITE retained (decided):** narrow the "exactly ENG and TEAMMATE" criterion to "no `pm`/`dev`
  value remains anywhere"; GRANITE stays for `valor-granite-loop`.
- **Eng access level (decided):** `(ENGINEER, …)` resolves `AccessLevel.WORKER` in
  `agent/sdk_client.py:1168`. Eng is the builder identity now; the old PM read-only rails are dead on
  the granite path (container PTYs run `bypassPermissions`), and a non-container/CLI eng session gets
  full `WORKER` rails.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `try/except` in `bridge/routing.py`, `agent/sdk_client.py` resolution, and both migration
      scripts. Migration scripts already log per-key errors and count them — assert the error counter
      increments and the script exits non-zero when a rename fails (precedent has `stats["errors"]`).
- [ ] Verify the `resolve_persona` fallback path (unknown/no-prefix chat) still resolves a sane
      default after the `Dev:`/`PM:` branches are removed — test asserts the new default, not a swallow.

### Empty/Invalid Input Handling
- [ ] `valor-session create --role dev` / `--role pm` must **reject** with a clear error (not silently
      default to eng). Test asserts non-zero exit + message.
- [ ] Migration scripts on an empty Redis (no `pm` keys / no Dev chat) must no-op cleanly (`--dry-run`
      and live), exit 0, report zero renames — assert idempotency on a second run.
- [ ] `resolve_persona` with empty/whitespace chat title resolves the documented default.

### Error State Rendering
- [ ] An eng session that errors must still deliver a PM-persona-safe Telegram message (no raw
      `SessionType`/enum string leaks) — guard against the renamed enum surfacing in user output.
- [ ] Migration scripts surface failures to the operator (stderr + non-zero exit), never a silent
      partial migration.

## Test Impact

- [ ] `tests/unit/test_pm_session_permissions.py::TestPMBashRestriction` — REPLACE/DELETE: the PM
      read-only Bash rails in `pre_tool_use.py` are removed for work sessions; rewrite to assert the
      new eng behavior or delete if the rail is fully gone.
- [ ] Tests asserting `SessionType.PM` / `SessionType.DEV` (enum membership, routing, scheduler,
      dashboard) — UPDATE: switch to `SessionType.ENG`; assert `DEV`/`PM` no longer exist.
- [ ] `tests/unit/granite_container/test_cli.py` (GRANITE type) — VERIFY-UNCHANGED: GRANITE retained;
      these must still pass.
- [ ] Multi-dev fan-out tests for `tools/sdlc_decompose.py` / `MAX_PARALLEL_DEVS` /
      `PARALLEL_SAFE_PAIRS` — DELETE: feature removed.
- [ ] `_handle_dev_session_completion` tests — DELETE: function removed.
- [ ] Child-session tests (`waiting_for_children`, `_finalize_parent_sync`,
      `VALOR_PARENT_SESSION_ID`) — VERIFY-UNCHANGED + ADD: the pattern survives; add/keep a test
      proving `VALOR_PARENT_SESSION_ID` propagates into container-spawned children after the rename.
- [ ] `bridge/routing.py` persona-resolution tests (`Dev:`/`PM:`/`Eng:` prefixes) — UPDATE: assert
      `Eng:` resolves engineer; `Dev:`/`PM:` no longer special.
- [ ] `valor-session` role tests — UPDATE: `eng`/`teammate` accepted; `dev`/`pm` rejected.
- [ ] `bridge/email_bridge.py` persona→session_type tests — UPDATE: `else` branch yields `ENG`.
- [ ] Migration scripts — ADD: new unit/integration tests for `migrate_session_type_pm_to_eng.py` and
      `merge_dev_chat_into_eng.py` (dry-run, idempotency, error path), project-scoped to a `test-`
      prefix, ORM-only cleanup.

## Rabbit Holes

- **Rebuilding multi-dev fan-out as "parallel eng containers."** Out of scope. Delete the dead
  machinery; the child-session pattern survives if anyone wants to rebuild it later.
- **Re-architecting the granite container's internal PM/Dev steering.** This plan touches only the
  *bridge-level* role; the container's internals are #1612's domain and stay untouched.
- **Designing a general "rename any KeyField" migration framework.** Two purpose-built scripts cloned
  from the #652 precedent are sufficient; don't generalize.
- **Reverse-migrating chat history.** The Dev→Eng re-key is treated as one-way; don't build undo
  tooling — the Cowboy pilot is the safety gate.
- **Auditing every project's Telegram group state remotely.** The per-machine operator performs the
  renames on their own machine; the plan documents the steps, it does not try to drive Telegram for
  all 13 projects centrally.

## Risks

### Risk 1: Half-migrated enum leaves the tree red
**Impact:** `config/enums.py` is imported across ~22 files; a partial rename breaks imports and tests
everywhere.
**Mitigation:** Land enum + persona + routing + SDK-client + CLI + hook + dashboard edits in one
coherent change; `pytest tests/unit/` must be green on that commit before the data migrations.

### Risk 2: KeyField migration corrupts AgentSession or chat history
**Impact:** A botched raw `rename` could orphan records or split history.
**Mitigation:** Both scripts default to `--dry-run`, are idempotent, require the bridge stopped, and
call `rebuild_indexes()`. Operator reviews dry-run output before the live run. Cowboy pilot first.

### Risk 3: GRANITE disposition wrong → AC contradiction or broken CLI
**Impact:** Deleting GRANITE breaks `valor-granite-loop`; keeping it silently violates a literal
"exactly ENG and TEAMMATE" AC.
**Mitigation:** GRANITE explicitly retained as CLI-only (supervisor-confirmed); AC narrowed to
"no `pm`/`dev` value remains anywhere". `valor-granite-loop` and its tests stay green.

### Risk 4: Eng access-level decision changes harness behavior subtly
**Impact:** `(ENGINEER, AccessLevel.WORKER)` resolution affects non-container/CLI eng sessions' rails.
**Mitigation:** Access level decided as `WORKER` (supervisor-confirmed); add a test asserting
`SessionType.ENG` resolves `AccessLevel.WORKER`.

### Risk 5: `VALOR_PARENT_SESSION_ID` silently drops in container PTYs
**Impact:** Container-spawned children lose parent linkage → broken auto-resume.
**Mitigation:** Dedicated verification task + test that the env var propagates into pooled PTYs after
the rename (issue AC item).

### Risk 6: `project_mode == "pm"` config side channel survives the rename (BLOCKER B2)
**Impact:** `agent/sdk_client.py` branches on `project.get("mode") == "pm"` at four sites independently
of `SessionType`; a project still carrying `"mode": "pm"` silently keeps PM rails / suppresses
`WORKER_RULES` / skips SDLC classification under the new Eng model.
**Mitigation:** Two-part fix — (1) Task 3 deletes the four `project_mode == "pm"` literal guards so the
Eng model has one code path; (2) the per-machine runbook strips `"mode": "pm"` from vault projects.json
(the mode validator normalizes the absence to `"dev"`, so it is safe).

### Risk 7: `pm` records popped against `PM`-less code mid-rollout (BLOCKER B4)
**Impact:** If a machine `/update`s (removing `SessionType.PM`) before its Redis migration, the worker
pops `session_type=pm` records that never match any comparison → silent fall-through to the wrong
persona/access-level (no crash).
**Mitigation:** The runbook mandates **migration before `/update`** on every machine, so all `pm`
records are drained to `eng` while `SessionType.PM` still exists in the running code. No alias shim.

### Risk 8: Redis `RENAME` clobbers an existing Eng record (BLOCKER B5)
**Impact:** `merge_dev_chat_into_eng.py` re-keys `TelegramMessage` keys; `RENAME` silently `DEL`s the
destination, and same-`msg_id` keys across the Dev/Eng chats collide on the rewritten chat_id segment
(`msg_id` is `AutoKeyField`), destroying existing Eng records.
**Mitigation:** EXISTS-check before every rename (skip + log on collision, never clobber); dry-run
emits a full collision report; create-then-delete `Chat` order; pre/post count assertion. Running the
merge at all is a per-project operator decision (archiving already preserves history).

## Race Conditions

### Race 1: Migration runs while the bridge OR worker is live
**Location:** `scripts/migrate_session_type_pm_to_eng.py`, `scripts/merge_dev_chat_into_eng.py`
**Trigger:** The bridge **or the worker** creates/writes an AgentSession or TelegramMessage mid-rename.
The worker is the more dangerous concurrent writer: its hourly `agent-session-cleanup` reflection and
`agent/reflection_scheduler.py:571` (which creates `session_type="pm"` sessions) run independently of
the bridge, so a `pm` record can appear *during* the rename loop, leaving an unmigrated record or
rebuilding indexes on partial state.
**Data prerequisite:** No concurrent writer to the keys being renamed.
**State prerequisite:** **Bridge AND worker stopped** on the target machine (BLOCKER B3).
**Mitigation:** The per-machine runbook stops **both** the bridge (`valor-service.sh stop`-equivalent)
and the worker (`valor-service.sh worker-disable` — stop *and* suppress launchd respawn) before
migration, and restarts both after. **Defense-in-depth:** both migration scripts read the worker
heartbeat key at startup (`register_worker_pid` / `_write_worker_heartbeat`, freshness governed by
`HEARTBEAT_FRESHNESS_WINDOW`) and `sys.exit(1)` with a clear error if a *fresh* worker heartbeat is
detected — so the migration refuses to run against a live worker even if the operator forgets the
stop step. `rebuild_indexes()` runs after all renames so the index reflects the final key set.

### Race 2: Telegram rename vs. in-flight message to the old group
**Location:** Telegram + `bridge/routing.py`
**Trigger:** A message lands on `PM:`/`Dev:` during the rename window.
**Data prerequisite:** Group renamed (chat_id preserved) before bridge restarts on new config.
**State prerequisite:** Bridge stopped during the rename/config-edit window.
**Mitigation:** The atomic per-machine sequence stops the bridge before the Telegram rename and vault
edit, so no message is routed against a half-applied config.

## No-Gos (Out of Scope)

- [ORDERED] Per-machine production rollout beyond the Cowboy pilot (Captain, Pirate, Bald) — gated on
  the pilot succeeding (end-to-end `Eng:` message → container → reply verified) and human go-ahead;
  each machine's Telegram renames + vault edit + local Redis migration are operator-gated events.
- [EXTERNAL] The Telegram group renames/archives themselves — they require a human with Telegram admin
  rights on each machine's account; the plan provides the runbook, the agent cannot click Telegram for
  all projects centrally.
- [SEPARATE-SLUG] Rebuilding parallel-eng-container fan-out on the surviving child-session machinery —
  not filed; explicitly deferred as a future capability, not part of this cutover. (If pursued, file a
  fresh issue; this plan only deletes the dead #1409 machinery.)
- [OPERATOR-DECISION] **Running** `merge_dev_chat_into_eng.py` per project is an operator choice, not a
  blanket requirement. The default is to **archive** the Dev group (history preserved in-place, readable
  via `valor-telegram --chat-id`); the merge re-key is only run where a single consolidated Eng channel
  is operationally important, and only after reviewing the script's dry-run collision report.

## Update System

No update-script **code** changes required. The feature is delivered to each machine by the existing
`/update` flow, but the **per-machine step ordering is load-bearing** and must be followed exactly.

**Per-machine runbook (ordered — migration BEFORE `/update`, per BLOCKER B4):**

1. **Stop the bridge AND the worker (BLOCKER B3).** `./scripts/valor-service.sh` stop the bridge and
   `./scripts/valor-service.sh worker-disable` (stop *and* suppress launchd auto-respawn — a plain
   `worker-stop` may be relaunched by `KeepAlive=true`). The worker must stay down for the whole
   migration window because its hourly cleanup + `reflection_scheduler.py` create `session_type="pm"`
   records outside the bridge.
2. **Telegram:** rename `PM: {Project}` → `Eng: {Project}` (preserves chat_id/history); archive
   `Dev: {Project}` (or rename `Dev:`→`Eng:` for Dev-only projects).
3. **Run the Redis migration(s) against the still-current (pre-update) code (BLOCKER B4):**
   `python scripts/migrate_session_type_pm_to_eng.py --dry-run` then live. This drains all `pm` records
   to `eng` *while `SessionType.PM` still exists in the running code*, so no record is ever compared
   against a deleted enum member. (Optionally, per the operator's per-project decision, run
   `python scripts/merge_dev_chat_into_eng.py --dry-run` then live — review the collision report first.)
   Both scripts refuse to run if a fresh worker heartbeat is detected (defense-in-depth for step 1).
4. **Edit vault `projects.json`:** replace `PM:`/`Dev:` group declarations with the single `Eng:`
   group, set persona `engineer`, **and remove any `"mode": "pm"` key (BLOCKER B2)** — the mode
   validator normalizes a missing/unknown mode to `"dev"`, so stripping it is the safe operational
   complement to deleting the `project_mode == "pm"` code guards in Task 3. Validated by
   `bridge/config_validation.py::validate_projects_config` at update Step 4.6.
5. **`/update`** on the machine: pulls the merged (`PM`-less) code, `env_sync`/restart proceed normally.
   Because step 3 already drained the `pm` records, the new code never encounters a `session_type=pm`
   record.
6. **Re-enable + restart the worker** (`worker-start` re-enables launchd respawn) and the bridge;
   verify end-to-end via `valor-telegram read --chat "Eng: {Project}"`.

This ordering is the chosen resolution to BLOCKER B4 over a one-release `PM = "eng"` alias shim (NO
LEGACY CODE). This plan's `single-machine-ownership.md` doc update keeps the ownership examples
consistent with the new `Eng:` group shape.

## Agent Integration

No new agent-facing tool surface. This is a bridge-internal refactor: the agent reaches engineering
work through the same Telegram path, now via one `Eng: {Project}` group instead of paired groups. The
`valor-session` CLI (an existing `[project.scripts]` entry) changes its accepted `--role` values
(`eng`/`teammate`); the `sdlc-decompose` CLI entry is **removed** from `pyproject.toml [project.scripts]`.
`valor-granite-loop` is unchanged. Integration tests verify: (a) a message to an `Eng:` group creates a
`session_type="eng"` session and round-trips to a Telegram reply through the container; (b)
`valor-session create --role eng` works and `--role dev`/`--role pm` are rejected.

## Documentation

### Feature Documentation
- [ ] Rename `docs/features/pm-dev-session-architecture.md` → `docs/features/eng-session-architecture.md`
      (NO LEGACY naming rule; supervisor-confirmed) and rewrite it to describe the single Eng role and
      `{eng, teammate}` (+ CLI-only granite) session types. Update all inbound references to the old path.
- [ ] Remove/replace `docs/features/sdlc-parallel-execution.md` (multi-dev fan-out deleted).
- [ ] Update `docs/features/single-machine-ownership.md` examples to the `Eng:` group shape.
- [ ] Update `docs/features/README.md` index table for any renamed/removed pages.
- [ ] In the renamed `eng-session-architecture.md` **and** `CLAUDE.md`, document the conversational-vs-work
      group contract (CONCERN, User): **`Teammate: {Project}` = ask a question / discuss; `Eng: {Project}`
      = do work** — the documented replacement for the retired `PM:` lightweight surface.

### Inline Documentation
- [ ] Update `config/enums.py` `SessionType`/`PersonaType` docstrings (remove pm/dev prose; describe
      eng + retained CLI-only granite).
- [ ] Update `config/personas/engineer.md` (merged persona) docstring/header.

### Config & Command Surfaces
- [ ] Update `config/projects.example.json` to the single `Eng:` group + `engineer` persona shape.
- [ ] Update `CLAUDE.md` command tables / architecture prose mentioning PM→Dev session spawning,
      `--role dev`/`--role pm`, and `sdlc-decompose`.

## Success Criteria

- [ ] `SessionType` contains `ENG`, `TEAMMATE`, `GRANITE` (CLI-only); **no `pm` or `dev` value remains
      anywhere** in code (enums, hooks, router, CLI, dashboard, persona files, tests).
- [ ] `bridge/routing.py` resolves an `engineer` persona from config and an `Eng:` title prefix;
      `Dev:`/`PM:` fallbacks are gone.
- [ ] `valor-session create` accepts roles `eng` and `teammate` only; `--role dev`/`--role pm` are
      rejected with a clear error.
- [ ] `SessionType.ENG` resolves `AccessLevel.WORKER` in `agent/sdk_client.py` (test asserts the
      resolved access level).
- [ ] `scripts/migrate_session_type_pm_to_eng.py` renames existing `session_type=pm` AgentSession Redis
      records to `eng` and rebuilds indexes; `--dry-run`, idempotent, runnable per machine.
- [ ] `scripts/merge_dev_chat_into_eng.py` **exists and is correct** (EXISTS-checked rename, create-then-delete
      Chat order, count assertion, dry-run collision report). **Authoring the script is the mandatory AC;
      *running* it is a per-project operator decision (CONCERN, Simplifier/User)** — archiving the Dev
      group already preserves its history in-place (readable via `valor-telegram --chat-id`), and a
      one-way re-key collides two timelines on one chat_id (out-of-order display if Dev's last message
      is newer than Eng's). For any project where the operator *chooses* to merge,
      `valor-telegram read --chat "Eng: {Project}"` returns the merged history after the run.
- [ ] `python -m tools.memory_search status --project valor` is healthy post-migration with no memory
      count change (proving zero memory impact).
- [ ] `tools/sdlc_decompose.py`, its `pyproject.toml` entry, `MAX_PARALLEL_DEVS`/`PARALLEL_SAFE_PAIRS`,
      and `_handle_dev_session_completion()` are deleted; child-session tests
      (`waiting_for_children`, `_finalize_parent_sync`) still pass.
- [ ] `VALOR_PARENT_SESSION_ID` injection follows the rename and is verified to propagate to
      container-spawned children (test or documented verification).
- [ ] Pilot completed on Valor the Cowboy: `Eng: Valor` and `Eng: Popoto` groups live, end-to-end
      message → container → Telegram reply verified, before any other machine migrates.
- [ ] Docs updated (see Documentation section): architecture, parallel-execution removal, ownership
      examples, persona docs, `projects.example.json`, `CLAUDE.md`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (enum-persona-routing)**
  - Name: `core-builder`
  - Role: Enum collapse, persona merge, bridge routing, SDK-client resolution, email-bridge mapping.
  - Agent Type: builder
  - Resume: true

- **Builder (dev-machinery-removal)**
  - Name: `removal-builder`
  - Role: Delete `_handle_dev_session_completion`, sdlc_decompose + fan-out constants, dev/pm CLI roles,
    PM read-only hook rails, dashboard display mapping.
  - Agent Type: builder
  - Resume: true

- **Builder (migrations)**
  - Name: `migration-builder`
  - Role: Author `migrate_session_type_pm_to_eng.py` + `merge_dev_chat_into_eng.py` from the #652
    precedent, with tests.
  - Agent Type: migration-specialist
  - Resume: true

- **Validator (parent-linkage)**
  - Name: `linkage-validator`
  - Role: Verify `VALOR_PARENT_SESSION_ID` propagates into container-spawned children after rename.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: All Documentation-section tasks.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Full success-criteria + verification-table sweep.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Collapse enums and merge personas
- **Task ID**: build-enums-persona
- **Depends On**: none
- **Validates**: `tests/unit/` enum/persona tests; `tests/unit/granite_container/test_cli.py` (GRANITE unchanged)
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- `config/enums.py`: `SessionType` → `{ENG, TEAMMATE, GRANITE}` (`pm`→`eng`, remove `DEV`, keep
  `GRANITE`); `PersonaType.DEVELOPER`+`PROJECT_MANAGER` → `ENGINEER`; update docstrings.
- Merge `config/personas/project-manager.md` + `developer.md` → `config/personas/engineer.md`; update
  `manifest.json`/segment references.

### 2. Rewire bridge routing, SDK client, email bridge
- **Task ID**: build-routing
- **Depends On**: build-enums-persona
- **Validates**: `bridge/routing.py` persona tests; email-bridge persona tests
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- `bridge/routing.py`: resolve `engineer`; `Eng:` prefix fallback only; delete `Dev:`/`PM:` branches +
  `_is_team_chat` prefix tuple.
- `bridge/telegram_bridge.py` + `bridge/email_bridge.py`: map to `SessionType.ENG`.
- `agent/sdk_client.py`: `(ENGINEER, AccessLevel.WORKER)` resolution in `compose_system_prompt`
  + `_resolve_*` (~1168); re-gate `VALOR_PARENT_SESSION_ID` (~1595) on ENG/Teammate.

### 3. Remove dev machinery
- **Task ID**: build-removal
- **Depends On**: build-enums-persona
- **Validates**: child-session tests still pass; fan-out/dev-completion tests deleted
- **Assigned To**: removal-builder
- **Agent Type**: builder
- **Parallel**: true
- **Pre-removal audit (BLOCKER B1):** run
  `grep -rn "_handle_dev_session_completion\|_create_continuation_pm\|_transition_parent" agent/`
  and enumerate every caller/importer BEFORE writing the removal diff. The dev-completion path is
  **entangled with shared parent-child machinery** — the removal must be surgical:
  - **`_transition_parent` (`session_completion.py:171`) MUST NOT be deleted** — it is the generic
    parent-status transition, called by `session_health.py:1955,2015` for *all* parent-child
    completion, not just dev. Leave it in place.
  - **`_create_continuation_pm` (`session_completion.py:277`) has surviving callers inside
    `_handle_dev_session_completion` itself (`session_completion.py:1669,1709,1724`).** Audit whether
    any caller survives outside the dev-completion path before deleting; if all callers live inside
    the deleted function, delete it too — otherwise keep it.
  - **`agent/agent_session_queue.py:49-53` re-exports all three symbols** (`_handle_dev_session_completion`,
    `_create_continuation_pm`, `_transition_parent`) for backward compatibility. Deleting
    `_handle_dev_session_completion` without fixing this re-export **breaks module load at import
    time → fails every session enqueue, not just dev completions.** Update the re-export block to drop
    only the deleted symbol(s) and keep the survivors.
  - Also audit `agent/hooks/pre_tool_use.py:503` and `agent/output_router.py:13,118` which reference
    `_handle_dev_session_completion` in comments/logic.
- Delete `_handle_dev_session_completion()` + its dev-only callers/parent-steering in
  `session_executor.py` (import L17, call L1915), `session_health.py`, `output_router.py`, **and the
  re-export in `agent/agent_session_queue.py`** — surgically, per the audit above.
- **Parent-sync machinery disposition (CONCERN, Consistency):** `_finalize_parent_sync` /
  `waiting_for_children` are general child-session machinery, **not** dev-specific — they survive the
  removal. `_handle_dev_session_completion` was only one *trigger* of the parent-sync path; the
  container-completion path and `session_health.py`'s `_transition_parent` calls remain. Task 5
  (validate-linkage) confirms the surviving path still finalizes parents. The child-session tests
  (`waiting_for_children`, `_finalize_parent_sync`) are therefore **kept and must still pass**; only
  tests exercising the dev-completion *trigger* are deleted.
- Delete `tools/sdlc_decompose.py` + `pyproject.toml` `sdlc-decompose` entry +
  `MAX_PARALLEL_DEVS`/`PARALLEL_SAFE_PAIRS` in `agent/sdlc_router.py`.
- `tools/valor_session.py` (+ `valor_cli.py`, `sdlc_session_ensure.py`, `agent_session_scheduler.py`):
  accept `eng`/`teammate`, reject `dev`/`pm`.
- Remove PM read-only Bash rails for work sessions in `agent/hooks/pre_tool_use.py`.
- **Reconcile the `project_mode == "pm"` config side channel (BLOCKER B2):** `agent/sdk_client.py`
  reads `project.get("mode", "dev")` and branches on `project_mode == "pm"` at lines 1173, 2119, 3164
  (plus the `has_worker_rules = project_mode != "pm"` at 3605 and the SDLC-skip guards at 3082, 3277).
  This is independent of `SessionType`, so after the rename a project still carrying `"mode": "pm"` in
  projects.json silently keeps PM rails / suppresses `WORKER_RULES` / skips SDLC classification. Fix:
  delete the four `project_mode == "pm"` literal guards (and the `!= "pm"` complements) so the Eng
  model has a single code path. The mode validator (`sdk_client.py:3160`) already normalizes any
  unrecognized mode to `"dev"`, so stripping `"mode": "pm"` from a vault projects.json is safe —
  the per-machine runbook step that removes it (see Update System) is the operational complement.
- `ui/data/sdlc.py`: update display mapping (`pm`→Engineer; drop dev internal-sender entries).

### 4. Author migration scripts + tests
- **Task ID**: build-migrations
- **Depends On**: build-enums-persona
- **Validates**: new migration tests (dry-run, idempotency, error path, EXISTS-check collision skip,
  worker-heartbeat-guard exit, create-then-delete Chat order, pre/post count assertion), project-scoped
- **Informed By**: `scripts/migrate_session_type_chat_to_pm.py` (#652 precedent),
  `tools/telegram_history/__init__.py:~1097` (Chat delete-recreate)
- **Assigned To**: migration-builder
- **Agent Type**: migration-specialist
- **Parallel**: true
- `scripts/migrate_session_type_pm_to_eng.py`: raw `rename` + `hset` for the `session_type` KeyField,
  then `AgentSession.rebuild_indexes()`; `--dry-run`, idempotent, **"stop the bridge AND worker"**.
  - **Worker-heartbeat guard (BLOCKER B3):** at startup, read the worker heartbeat key
    (`register_worker_pid` / `HEARTBEAT_FRESHNESS_WINDOW`); `sys.exit(1)` with a clear message if a
    fresh worker heartbeat exists. Both migration scripts share this guard.
  - **Read `session_type` directly, NOT `session_mode` (CONCERN, Archaeologist):** the #652 precedent
    script branches on the deprecated `session_mode` field, a no-op since #1026. Header comment must
    state: *"Unlike #652, do NOT read session_mode — deprecated no-op since #1026; read session_type
    directly."* Idempotency for non-target records: `if ":dev:" in key_str:
    stats["skipped_dev_record"] += 1; continue` — `dev` deletion is code-side (Task 3), not a Redis
    rename, so dev keys are left untouched here.
- `scripts/merge_dev_chat_into_eng.py`: re-key Dev `TelegramMessage` records onto Eng chat_id (rename
  pattern), `Chat` rename via ORM; project-scoped, `--dry-run`, idempotent. `TelegramMessage` uses
  `msg_id = AutoKeyField()` + `chat_id = KeyField()` (`models/telegram.py:23-24`), so the re-keyed key
  carries the chat_id segment — same-`msg_id` keys across the two chats collide on the rewritten
  segment. Required guards:
  - **EXISTS-check before every `rename` (BLOCKER B5):** Redis `RENAME` silently overwrites (implicit
    `DEL`) the destination if it exists, destroying an existing Eng `TelegramMessage`. Before each
    rename, `redis_client.exists(new_key)`; non-zero = collision → **log and skip, never clobber**.
    `--dry-run` must enumerate **all** prospective collisions in a report so the operator sees them
    before any live run.
  - **`Chat` rename order — create-then-delete, not delete-then-create (CONCERN, Adversary):** the
    ORM delete+create is non-atomic; a kill between the two permanently loses the Eng `Chat` record
    and orphans every re-keyed message. **Create the Eng `Chat` first, verify it exists, then delete
    the Dev `Chat` as the final step.** `chat_name` is a KeyField and `chat_id` a UniqueKeyField, so a
    `create()` with a colliding `chat_name` errors at the Popoto level (detectable, not silent).
  - **Pre/post count assertion + checkpoint idempotency (CONCERN, Operator):** capture the total Dev-chat
    `TelegramMessage` key count pre-run; after the run, assert the count under the Eng chat_id segment
    equals (pre-existing Eng count + migrated Dev count minus skipped collisions); mismatch →
    `sys.exit(1)` prompting re-run. Idempotency guard: skip any key already bearing the target Eng
    `chat_id` segment (mirror the precedent's `skipped_already_migrated`). This gives a mid-scan-kill
    a safe resume.

### 5. Verify parent-session linkage
- **Task ID**: validate-linkage
- **Depends On**: build-routing, build-removal
- **Assigned To**: linkage-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `VALOR_PARENT_SESSION_ID` propagates into container-spawned (pooled-PTY) children after the
  rename; add/keep a test asserting it.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-routing, build-removal, build-migrations
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Execute all Documentation-section tasks (architecture rewrite, parallel-execution removal, ownership
  examples, persona docs, `projects.example.json`, `CLAUDE.md`).

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-routing, build-removal, build-migrations, validate-linkage, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion (including docs); confirm no `pm`/`dev`
  value or `Dev:`/`PM:` fallback remains; generate final report. (Production rollout beyond Cowboy
  pilot is operator-gated, per No-Gos.)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No `pm` session value | `grep -rn 'SessionType.PM\b\|"pm"' config/enums.py` | output does not contain SessionType.PM |
| No `dev` session value | `grep -rn 'SessionType.DEV\b' config/ agent/ bridge/ tools/ ui/` | exit code 1 |
| No `Dev:`/`PM:` fallback | `grep -rn 'startswith("Dev:")\|startswith("PM:")' bridge/routing.py` | exit code 1 |
| sdlc-decompose removed | `grep -n 'sdlc-decompose\|sdlc_decompose' pyproject.toml` | exit code 1 |
| GRANITE retained | `grep -n 'GRANITE' config/enums.py` | output contains GRANITE |
| Migration dry-run runs | `python scripts/migrate_session_type_pm_to_eng.py --dry-run` | exit code 0 |
| No `project_mode == "pm"` guard | `grep -n 'project_mode == "pm"\|project_mode != "pm"' agent/sdk_client.py` | exit code 1 |
| Migration refuses live worker | (manual) start worker, run migration | `sys.exit(1)` with fresh-heartbeat error |
| Merge dry-run reports collisions | `python scripts/merge_dev_chat_into_eng.py --dry-run --project test-x` | exit 0; collisions enumerated, no rename performed |
| agent_session_queue imports load | `python -c "import agent.agent_session_queue"` | exit code 0 (re-export block fixed) |

## Critique Results

**Verdict:** NEEDS REVISION (5 blockers) — war room run 2026-06-12.
**Revision applied 2026-06-12:** all 5 blockers folded into tasks/sections, all 6 concerns and the 1 nit
addressed (none deferred). The "Addressed By" / "Implementation Note" columns below record the
disposition; the changes are now live in the body (Task 3 for B1/B2/Consistency; Prerequisites +
Race Conditions + Update System for B3; Technical Approach + Update System for B4; Task 4 + Risks for
B5 and the migration concerns; Data Flow + Documentation for the conversational-path concern; Success
Criteria + No-Gos for the merge-demotion concern; Problem statement for the nit).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic | `agent/agent_session_queue.py:49-53` imports `_handle_dev_session_completion` + `_create_continuation_pm` + `_transition_parent`, but Task 3 (build-removal) lists only `session_executor.py`, `session_health.py`, `output_router.py`. Deleting the function without fixing this import breaks module load at startup — fails every session enqueue, not just dev completions. | Task 3 — add `agent/agent_session_queue.py` to the file list | `grep -rn "_handle_dev_session_completion\|_create_continuation_pm\|_transition_parent" agent/` before the removal diff. `_transition_parent` is shared (called by `session_health.py:1955,2015` for general parent-child completion) and must NOT be deleted; `_create_continuation_pm` may have surviving callers (`session_completion.py:1669,1709,1724`) — audit before deletion. The removal must be surgical to the dev-completion path only. |
| BLOCKER | Skeptic, Operator | `project_mode == "pm"` is a config-driven side channel at `sdk_client.py:1173, 2119, 3164, 3647` (read from `project.get("mode")` in projects.json), forcing PM rails / suppressing WORKER_RULES / skipping SDLC classification independent of `SessionType`. The plan never mentions it; after the rename a project still carrying `"mode": "pm"` silently keeps old PM-mode behavior. | New: reconcile `project_mode == "pm"` path + add runbook step to strip `"mode": "pm"` from vault projects.json | The mode-validation gate normalizes unknown modes to `"dev"`, so removing `"mode": "pm"` from projects.json safely falls back. Two-part fix: (1) per-machine runbook step to remove `"mode": "pm"` during the vault edit, (2) either rename/delete the four `project_mode == "pm"` literal guards or document precisely which behavior survives under the Eng model. |
| BLOCKER | Operator, Adversary | Runbook says "stop the bridge" but the **worker** (`python -m worker`) is a separate process that also writes AgentSession and runs `agent-session-cleanup` every 300s + reflection scheduler creating `session_type="pm"` sessions. A concurrent write/scan during the pm→eng `rename` loop produces an unmigrated record or rebuilds indexes on partial state. | Prerequisites + Race Conditions — add "stop the worker"; migration scripts assert no fresh worker heartbeat | Add `./scripts/valor-service.sh worker-stop` to the runbook alongside bridge stop. Both scripts read the worker PID/heartbeat key (`register_worker_pid`) at startup and `sys.exit(1)` if fresh. `reflection_scheduler.py` is the specific hot path creating `pm` sessions outside the bridge. |
| BLOCKER | Operator | Deploy ordering: code lands on main once and all machines `/update` (removing `SessionType.PM`), but each migrates Redis at a different time. Between `/update` and running the migration, the worker pops existing `session_type=pm` records against code with no `SessionType.PM` member — `StrEnum` comparisons never match, falling through to wrong persona/access-level. | Technical Approach / rollout — sequence migration before `/update`, or add a one-release `PM="eng"` alias shim | `session_type == SessionType.PM` against a deleted member never matches (constant gone), so old `pm` records fall to the default/`else` path in `sdk_client.py:1168` and `session_executor.py` rather than crashing — silent wrong behavior. Either run `migrate_session_type_pm_to_eng.py` before `/update` pulls new code on each machine, or add a deprecated `PM = "eng"` alias removed in a follow-on PR. |
| BLOCKER | Adversary | Redis `RENAME` silently overwrites the destination key (implicit DEL) if it exists. `TelegramMessage` records share key segments; a Dev message colliding with an existing Eng record (same `AutoKeyField` msg_id sequence, or cross-posted message_id) silently destroys the Eng record. Idempotency claim also undermined on re-run. | Task 4 — `merge_dev_chat_into_eng.py` must `EXISTS`-check before each rename | Before each `rename`, `redis_client.exists(new_key)`; non-zero = collision → log + skip (or field-merge), never clobber. Dry-run must report all prospective collisions. `TelegramMessage` uses `AutoKeyField` for `msg_id`, so same-value keys across two chats collide on the rewritten chat_id segment — not exotic. |
| CONCERN | Adversary | `Chat` rename via ORM `delete()` + `create()` is a non-atomic two-step; a kill between them permanently loses the Eng `Chat` record, orphaning all re-keyed messages and breaking `valor-telegram read --chat "Eng: {Project}"`. | Task 4 — reverse order: create Eng Chat, verify, then delete Dev Chat | `chat_name` is a KeyField, `chat_id` a UniqueKeyField; `create()` with an existing `chat_name` collision-errors at the Popoto level (detectable). Defer the Dev `Chat` delete to a final step once the Eng record is confirmed present. |
| CONCERN | Operator | No defined partial-failure recovery for the live chat-merge run. The precedent counts errors per-key but doesn't stop on first error or checkpoint; a mid-scan kill leaves a mix of re-keyed and original records with no resume cursor and `rebuild_indexes()` un-run. | Task 4 — pre/post count assertion + idempotency guard keyed on Eng chat_id segment | Capture total Dev-chat TelegramMessage key count pre-run; assert post-run count under Eng chat_id equals it; mismatch = non-zero exit prompting re-run. Idempotency guard: skip keys already bearing the target Eng `chat_id` segment (mirror precedent's `skipped_already_migrated`). Verify key schema against `tools/telegram_history/__init__.py`. |
| CONCERN | Archaeologist | The #652 precedent script branches on the deprecated `session_mode` field (no-op since #1026). A dev naively cloning it would mis-handle records; the new script must read `session_type` directly and treat `dev` records as no-ops (dev deletion is code-side, not a Redis rename). Plan's Task 4 doesn't call out this divergence. | Task 4 — note "read session_type directly, not session_mode; skip `:dev:` keys" | Script header comment: "unlike #652, do NOT read session_mode — deprecated no-op since #1026." Idempotency: `if ":dev:" in key_str: stats["skipped_dev_record"] += 1; continue`. |
| CONCERN | Simplifier, User | `merge_dev_chat_into_eng.py` re-keys full Dev history as a one-way risky migration to satisfy "nothing is lost," but **archiving** the Dev group already preserves history in-place (readable via `valor-telegram --chat-id`). The merge only adds value where a single consolidated Eng channel is operationally important. | Success Criteria / No-Gos — demote the merge from a mandatory AC to a per-project operator decision | One-way re-key collides two histories on one chat_id; if Dev's last message is newer than Eng's, the merged timeline appears out of order in `valor-telegram read` (sorted by timestamp). Authoring the script (Task 4) stays scoped; gate *running* it behind a human per-project decision. |
| CONCERN | User | The plan removes the read-only PM orchestration surface entirely: every `Eng:` message resolves `AccessLevel.WORKER` and spawns a granite container. There's no documented lightweight "just ask a question / steer" path in a dedicated group to replace the old `PM:` behavior. | Data Flow / Documentation — confirm container responsiveness or document Teammate group as the conversational path | `Teammate: {Project}` groups preserve the conversational path; CLAUDE.md / the architecture doc must state which group is "ask a question" vs "do work" so the human's group-routing muscle memory has a documented replacement. |
| CONCERN | Consistency | Success Criterion asserts `_handle_dev_session_completion()` is deleted AND "child-session tests (`waiting_for_children`, `_finalize_parent_sync`) still pass," but that function drives `_finalize_parent_sync` / `waiting_for_children`. The plan never specifies what machinery those tests exercise post-deletion. | Task 3 — specify whether the parent-sync machinery is relocated to the container path or the tests are deleted | Add to Task 3 scope either (a) "`_finalize_parent_sync`/`waiting_for_children` relocated to the container completion path — tests updated" or (b) "those tests are deleted since the PM parent-steering trigger is gone." Asserting the outcome without the mechanism is the gap. |
| NIT | Consistency | Issue title + Problem statement say `SessionType` "collapses to `{eng, teammate}`" (two values); every other section + Resolved Decisions retains `{ENG, TEAMMATE, GRANITE}` (three). The framing is never amended to match the body. | Problem statement — one-line edit | Change "collapses to `{eng, teammate}`" to "collapses to `{eng, teammate}` (GRANITE retained as CLI-only; see Resolved Decisions)." |

### Cycle 2 — war room re-run 2026-06-12

**Verdict:** NEEDS REVISION (4 blockers)

This cycle critiqued the **revised** plan (commit 054af5ed). The five cycle-1 blockers were
verified individually: **B1 (re-export), B4 (migration-before-/update), B5 (RENAME EXISTS-check)
hold.** Two cycle-1 resolutions were found **incomplete**, and three **new** blockers surfaced
(all independently verified against the live codebase before recording):

| Severity | Critic | Finding | Suggested Fix | Implementation Note |
|----------|--------|---------|---------------|---------------------|
| BLOCKER | Archaeologist | `merge_dev_chat_into_eng.py` (Task 4) clones the #652 raw-`RENAME` pattern onto `TelegramMessage` but never calls `TelegramMessage.rebuild_indexes()`. Every `rebuild_indexes()` in the plan (lines 78, 117, 241, 366, 424, 684) targets `AgentSession`. Reads go through `TelegramMessage.query.filter(chat_id=...)` (`tools/telegram_history/__init__.py:457,516,560,1151`); a raw `RENAME` of the data-hash key does NOT move the message into the Eng `chat_id` field index or the `timestamp` SortedField partition. Result: after merge, `filter(chat_id="<eng_id>")` returns only pre-existing Eng messages, so Success Criterion line 536 ("`valor-telegram read --chat "Eng:"` returns merged history") silently fails. | Task 4 — add `TelegramMessage.rebuild_indexes()` as the final step of the merge script; replace the raw-key count assertion with an ORM `query.filter(chat_id=...)` readback. | The existing pre/post assertion (lines 709-714) counts raw *keys*, which a `RENAME` updates correctly, so it passes while the field/sorted-set indexes are stale. The assertion must read back through `TelegramMessage.query.filter(chat_id=...)` AFTER `rebuild_indexes()`; verify the partitioned SortedField (`partition_by="chat_id"`) is rebuilt, not just the KeyField index — out-of-order timeline display (line 535) also depends on the sorted-set partition. |
| BLOCKER | Operator, Adversary | The worker-heartbeat guard (B3 defense-in-depth) is specified against a non-existent key shape. Plan says scripts read "the worker heartbeat key (`register_worker_pid`/`_write_worker_heartbeat`, freshness governed by `HEARTBEAT_FRESHNESS_WINDOW`)" and `sys.exit(1)` on a *fresh* heartbeat (lines 421-424, 685-687, 469). But `register_worker_pid` writes `worker:registered_pid:{hostname}:{pid}` with a **24h TTL** (`session_health.py:79`) whose **value is the PID, not a timestamp**; `HEARTBEAT_FRESHNESS_WINDOW`=90 (`session_health.py:219`) governs a different per-session field. As written the guard cannot compute freshness, or false-blocks legitimate migrations for up to 24h after a clean worker stop. | Rewrite the guard against `data/last_worker_connected` mtime (the file the worker writes every health interval), `sys.exit(1)` if `(now - mtime) < threshold`; drop `HEARTBEAT_FRESHNESS_WINDOW`/`register_worker_pid` framing. A `pgrep -f "python -m worker"` / `os.kill(pid,0)` liveness check is a valid additional signal. | File path is `Path(__file__).parent.parent / "data" / "last_worker_connected"` (`session_health.py:2058`); resolve relative to the migration script's repo root, not cwd. The registered-PID key is host-scoped (`socket.gethostname()`) so it won't false-positive on a peer machine, but its 24h TTL makes it useless for "is the worker live right now." |
| BLOCKER | Adversary | The **email bridge** (`python -m bridge.email_bridge`) is a *third* independent process (started/stopped via `email-start`/`email-stop`, `scripts/valor-service.sh:985`) that enqueues `session_type=SessionType.PM` AgentSessions (`bridge/email_bridge.py:879`) whenever inbound email resolves a non-teammate persona. The plan's concurrent-writer analysis (Race 1, Prerequisites, runbook step 1) names only the Telegram bridge and the worker. An email arriving during the `pm→eng` rename writes a fresh `pm` record mid-loop — the exact B3 failure via an unguarded third process; the worker-heartbeat guard cannot catch it (email bridge writes `email:relay:last_poll_ts`, not the worker key). | Add "stop the email bridge (`./scripts/valor-service.sh email-stop`)" to Prerequisites, Race 1 state prerequisite, and runbook step 1. | The migration startup guard must also assert no fresh email-bridge heartbeat: read `email:relay:last_poll_ts` or `pgrep -f bridge.email_bridge` and `sys.exit(1)` if live. Stopping only bridge+worker leaves the `pm`-writing path at `email_bridge.py:879` open. |
| BLOCKER | User | The cycle-1 conversational-path resolution claims the lightweight "ask a quick question" surface "moves to the existing `Teammate: {Project}` group" as an equivalent drop-in for the retired `PM:` group, but (a) Teammate-persona groups are @mention-gated passive listeners (`bridge/routing.py` config path) — an un-mentioned question gets silent storage, no response, whereas the old `PM:` group responded to direct messages; and (b) no `Teammate: {Project}` *group* exists today (teammate is reached via DMs / `dm_persona`, `config/projects.example.json:47`), so the runbook silently requires creating a new per-project group it never lists. After the `PM:`→`Eng:` rename there is no quick-question surface unless this gap is closed. | Either (a) document the @mention requirement + point quick questions at the existing DM teammate path, or (b) add a runbook step + `projects.json` example to create the `Teammate:` group per project; and document the un-mentioned-message behavior. | `resolve_persona` returns `TEAMMATE` for a group only if `projects.json` declares that group's persona `"teammate"`; a no-prefix group with no persona field falls through to the `is_team_chat` @mention path, not the teammate path. The runbook `projects.json` edit (line 470) must add the `Teammate:` group entry if that group is the intended replacement. |
| CONCERN | Skeptic, Consistency | The B2 resolution undercounts the `project_mode == "pm"` surgery. Plan (B2 row, Task 3, Solution, Risk 6) says "four `project_mode == "pm"` guards at 1173, 2119, 3164, **3647**" — but verified reality is **three** `== "pm"` guards (1173, 2119, 3164; 3647 is a *comment*) and **four** `!= "pm"` complements (3082, 3277, 3605, **3693** — 3693 is never listed). A builder following the enumeration leaves 3693 in place. The line-757 verification grep is count-agnostic and WILL catch a leftover 3693, so it does not ship silently broken — hence CONCERN not BLOCKER — but the implementation guidance is wrong. | Fix the count at the source (B2 row) and propagate: "three `== "pm"` guards (1173, 2119, 3164) and four `!= "pm"` complements (3082, 3277, 3605, 3693)"; drop "four" as a descriptor of the `==` guards. | At 3693 the `!= "pm"` collapses to always-true once `mode=pm` is stripped, so the cross-repo SDLC `GH_REPO` branch should drop the `project_mode != "pm" and` prefix entirely. 3647 is a comment — do not count it as a guard. |
| NIT | Skeptic | Verification "No `dev` session value" check (line 752) greps `config/ agent/ bridge/ tools/ ui/` but not `tests/`, while Success Criteria line 521 asserts no `pm`/`dev` value remains "anywhere ... including ... tests." A lingering `SessionType.DEV` in a test file passes this gate. | Add `tests/` to the line-752 grep scope. | — |
| NIT | Adversary | The #652 precedent renames via unanchored `key_str.replace(":chat:", ...)`. Cloned literally for `:pm:`→`:eng:`, `pm` is a shorter, more collision-prone substring; corrupts any key where `:pm:` appears outside the session_type segment (near-zero in practice — no KeyField realistically holds exactly `pm`). | Replace only the session_type segment positionally; assert exactly one occurrence before renaming. | — |

**Cap reasoning:** Redis `critique_cycle_count` reads 0 due to the post-data-loss rebuild, but this is
genuinely cycle 2 (the durable record is this table). Two consecutive NEEDS REVISION verdicts do not
trip a cap here; the new blockers are concrete, verified, and each carries a ready-to-apply
Implementation Note, so a targeted revision pass is the correct next step (not MAJOR REWORK — the
architecture and approach are sound; these are migration-correctness and rollout-completeness gaps).

---

## Resolved Decisions

All three open questions have been resolved by supervisor decision (2026-06-12); their resolutions are
folded into the sections above.

1. **GRANITE disposition — CONFIRMED.** Retain `SessionType.GRANITE` as a CLI-only type (used by
   `valor-granite-loop`); it is distinct from bridge-originated work. The acceptance criterion is
   narrowed from "exactly ENG and TEAMMATE" to "no `pm`/`dev` value remains anywhere". Matches the
   #1635 drift reconciliation in the Freshness Check.

2. **Eng access level — CONFIRMED option (a): `AccessLevel.WORKER`.** `(ENGINEER, …)` resolves
   `AccessLevel.WORKER` in `agent/sdk_client.py:1168`. Eng is the builder identity now; the old PM
   read-only rails are dead on the granite path (container PTYs run `bypassPermissions`), so a
   non-container/CLI eng session gets full `WORKER` rails. A test asserts the resolved access level.

3. **Architecture doc fate — CONFIRMED: rename.** `docs/features/pm-dev-session-architecture.md` →
   `docs/features/eng-session-architecture.md` (NO LEGACY naming rule); rewrite content and update all
   inbound references to the old path.
