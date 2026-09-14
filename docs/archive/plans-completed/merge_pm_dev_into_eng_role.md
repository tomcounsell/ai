---
status: In Review
type: chore
appetite: Large
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1633
pr: https://github.com/tomcounsell/ai/pull/1691
last_comment_id: IC_kwDOEYGa088AAAABFx7oMw
revision_applied: true
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
  lines 390-393 maps to `PersonaType.DEVELOPER`/`PROJECT_MANAGER`. `is_team_chat` at line 327
  (public name, no leading underscore) also checks `("Dev:", "PM:")` prefixes.
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
  `valor-granite-loop` (`tools/granite_loop/cli.py`). The issue author posted a
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
   prefix fallback now matches `Eng:` only (`Dev:`/`PM:` branches deleted). `is_team_chat` updates
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

**Conversational vs. work path (CONCERN, User; corrected by BLOCKER C2-B4):** the old `PM:` group
doubled as a lightweight "just ask a question / steer" surface that **responded to direct messages**.
Under the Eng model every `Eng:` message resolves `AccessLevel.WORKER` and spawns a granite container.
The cycle-1 resolution's claim that the quick-question surface "moves to the existing `Teammate:
{Project}` group" was **wrong on two counts, verified against live config**:
1. **No `Teammate: {Project}` *group* exists today.** The teammate persona is reached via **DMs**
   (`dm_persona: "teammate"`, `config/projects.example.json:47`), not a per-project group. The runbook
   would have to *create* a new per-project group it never listed.
2. **Group teammate routing is @mention-gated.** `projects.example.json:7` documents that a no-prefix
   group resolves teammate **mention-only**: `resolve_persona` returns `TEAMMATE` for a group only if
   `projects.json` declares that group's persona `"teammate"`, and even then an *un-mentioned* message
   in a group falls through the `is_team_chat` @mention path → silent storage, **no response**. The old
   `PM:` group answered direct (un-mentioned) messages; a teammate group would not.

**Chosen resolution (decided): the `Eng: {Project}` group handles conversational messages too.** There
is no separate quick-question group. An `Eng:` message that is a plain question is still routed to the
engineer session, which answers conversationally before (or instead of) doing work — the granite
container is the single responsive surface per project. Quick *DM* questions continue to hit the
existing `dm_persona: "teammate"` path unchanged. The group-routing contract becomes: **`Eng: {Project}`
= one engineering surface for both questions and work (responds to direct messages); teammate DMs =
casual Q&A.** This avoids inventing a per-project `Teammate:` group the runbook would otherwise have to
create. Documented explicitly in CLAUDE.md and the renamed architecture doc so the human's
group-routing muscle memory has a clear, *real* replacement for the retired `PM:` group.

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
| **Email bridge stoppable on target machine** | `./scripts/valor-service.sh email-status` | **The email bridge (`python -m bridge.email_bridge`) is a *third* independent process that enqueues `session_type=SessionType.PM` AgentSessions (`bridge/email_bridge.py:879`) whenever inbound email resolves a non-teammate persona. An email arriving mid-migration writes a fresh `pm` record outside both the bridge and the worker (BLOCKER C2-B3). It MUST be stopped (`email-stop`) for the whole migration window.** |
| Redis reachable | `python -c "import popoto; popoto.redis_db.get_REDIS_DB().ping()"` | Migration + ORM operations |

Run all checks: `python scripts/check_prerequisites.py docs/plans/merge_pm_dev_into_eng_role.md`

## Solution

### Key Elements

- **Enum collapse (`config/enums.py`):** `SessionType` → `{ENG, TEAMMATE, GRANITE}` (`pm`→`eng`,
  `dev` removed, `granite` retained CLI-only). `PersonaType.DEVELOPER`+`PROJECT_MANAGER` → `ENGINEER`.
  `AccessLevel`/`SessionMode` reconciled so `SessionType.ENG` resolves `AccessLevel.WORKER`.
- **`AccessLevel.PM_READONLY` reconciled (BLOCKER C7-B3, decided — re-gate, not silent-drop):** the
  member is **deleted** (no resolver returns it once SessionType.PM and `project_mode=="pm"` go away —
  unreachable dead code under NO LEGACY CODE), but the **work-vault `CLAUDE.md` business-context layer
  it carried is preserved** by folding the append into the `AccessLevel.WORKER` branch of
  `compose_system_prompt` and re-gating the `load_eng_system_prompt` (formerly `load_pm_system_prompt`)
  SDLC-orchestration load in `session_executor.py` onto the eng/WORKER path. Non-container CLI eng
  sessions still compose system prompts via `compose_system_prompt` and keep the vault layer; the
  granite container path manages its own context. This is a deliberate decision recorded so the builder
  does not silently strip business context — see Task 2 for the exact surgery and the full PM_READONLY
  consumer audit.
- **Persona merge:** `config/personas/project-manager.md` + `developer.md` → one
  `config/personas/engineer.md`. Update `config/personas/segments/manifest.json` / segment references.
- **Bridge routing:** `bridge/routing.py` resolves `engineer`; `Eng:` prefix fallback only;
  `Dev:`/`PM:` branches and `is_team_chat` prefix tuple deleted. `bridge/telegram_bridge.py` and
  `bridge/email_bridge.py` map to `SessionType.ENG`.
- **SDK client:** `agent/sdk_client.py` `(persona, access_level, channel)` resolution and
  `compose_system_prompt` updated for `(ENGINEER, AccessLevel.WORKER)`. `VALOR_PARENT_SESSION_ID`
  injection re-gated on ENG/Teammate and verified to propagate into the container's pooled PTYs.
- **Dev machinery removal:** delete `_handle_dev_session_completion()` and its *dev-only*
  callers/parent-steering in `session_executor.py` / `session_health.py` / `output_router.py` **and the
  re-export in `agent/agent_session_queue.py:49-53`** (B1) — surgically, leaving the shared
  `_transition_parent` (kept; also imported by `tools/agent_session_scheduler.py:417-419`, C4-B2) and
  any surviving `_create_continuation_pm` callers intact; also flip the hardcoded
  `session_type == "pm"` delivery literal at `agent/output_router.py:159` to `"eng"` (C4-B1); **rename the
  six (verified seven) bare-string `session_type="pm"/"dev"` literal sites outside the enum-grep's reach
  (BLOCKER C5-B1): writers `reflection_scheduler.py:571` and `sustainability.py:610`, the metric-label
  source `sdk_client.py:2408` (the `cold_start_metrics.py:37` hit is a docstring example) + its
  docstrings, and readers `sdlc_stage_marker.py:99`, `sdlc_session_ensure.py:83/139/188`,
  `_sdlc_utils.py:87/97/162` — the three `sdlc_*` reader files are renamed, NOT deleted by the fan-out
  removal**; delete
  `tools/sdlc_decompose.py` + its pyproject entry + `MAX_PARALLEL_DEVS`/`PARALLEL_SAFE_PAIRS` in
  `agent/sdlc_router.py`; delete the `--role dev`/`--role pm` paths in `tools/valor_session.py` (+
  `valor_cli.py`, `sdlc_session_ensure.py`, `agent_session_scheduler.py`); remove the PM read-only Bash
  rails in `agent/hooks/pre_tool_use.py` for work sessions; **delete the three `project_mode == "pm"`
  guards + four `!= "pm"` complements in `agent/sdk_client.py` (B2; seven sites, 1173/2119/3164 and
  3082/3277/3605/3693 — line 3647 is a comment, not a guard)**; update `ui/data/sdlc.py` display mapping.
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
- [ ] **`ClassificationType.QUESTION` messages to an eng session resolve to nudge/deliver, never spawn a
      child dev session (CONCERN C4-C3).** The QUESTION fast-path (`sdk_client.py:3166,3202` — the critique
      cited 3265, verified actually ~3164-3210 in the classification block) must survive the B2 deletion of
      the `project_mode == "pm"` guards unchanged: a plain question still classifies as QUESTION and answers
      conversationally rather than entering the SDLC/child-dev path.
- [ ] **Reply-to steering routes to the existing session, not a new one (CONCERN C5-C4, User).** A
      reply-to message against a running `Eng:` session must land in `AgentSession.queued_steering_messages`
      on that session (steered at the turn boundary), NOT spawn a fresh `eng` session. Asserts the bridge's
      reply-to session-continuation is keyed on Telegram thread ID and survives the `PM:`→`Eng:` rename;
      pairs with the Cowboy-pilot Success Criterion.
- [ ] **#887 contamination guards still fire for ENG after the `== "dev"`→`== "eng"` rename (BLOCKER
      C6-B2).** The `session_executor.py` setup-path guards renamed in Task 3 must keep their protective
      behavior under the Eng role: (a) a **slugged** ENG session still gets worktree isolation — worktree
      provisioning runs and the main-checkout guard (line 885) raises if it resolves to the repo root; and
      (b) a **slugless** ENG session with a null `agent_session_id` (and null slug) is still **rejected at
      setup** — the line-652 executor-guard finalizes it `failed` rather than synthesizing a slug from a
      `None` aid (the #1272 crash this guard prevents). Test asserts both paths fire for `session_type="eng"`.

### Error State Rendering
- [ ] An eng session that errors must still deliver a PM-persona-safe Telegram message (no raw
      `SessionType`/enum string leaks) — guard against the renamed enum surfacing in user output.
- [ ] Migration scripts surface failures to the operator (stderr + non-zero exit), never a silent
      partial migration.

## Test Impact

- [ ] `tests/unit/test_pm_session_permissions.py::TestPMBashRestriction` — REPLACE/DELETE: the PM
      read-only Bash rails in `pre_tool_use.py` are removed for work sessions; rewrite to assert the
      new eng behavior or delete if the rail is fully gone.
- [ ] **`tests/unit/test_compose_system_prompt.py` (BLOCKER C7-B3) — REPLACE:** the #1227 byte-stability
      fixtures pin the composed system prompt for the `(PROJECT_MANAGER, PM_READONLY)` and
      `(DEVELOPER, WORKER)` cells. Both `(persona, access_level)` pairs are deleted; rewrite the fixtures
      for the single `(ENGINEER, WORKER)` cell — including the case where a `working_directory` with a
      `CLAUDE.md` is supplied (asserts the re-gated vault layer is appended on the WORKER branch) and the
      case where it is absent (asserts the layer is skipped without raising). Re-baseline any golden
      output. Also update `tests/unit/test_resolve_compose_args.py` if it asserts the removed
      PM_READONLY validation raise.
- [ ] Tests asserting `SessionType.PM` / `SessionType.DEV` (enum membership, routing, scheduler,
      dashboard) — UPDATE: switch to `SessionType.ENG`; assert `DEV`/`PM` no longer exist.
- [ ] `tests/unit/granite_container/test_cli.py` (GRANITE type) — VERIFY-UNCHANGED: GRANITE retained;
      these must still pass.
- [ ] Multi-dev fan-out tests for `tools/sdlc_decompose.py` / `MAX_PARALLEL_DEVS` /
      `PARALLEL_SAFE_PAIRS` — DELETE: feature removed.
- [ ] `_handle_dev_session_completion` tests — DELETE: function removed.
- [ ] `tests/unit/test_output_router.py` `determine_delivery_action` nudge-path test (BLOCKER C4-B1) —
      UPDATE: switch the `session_type="pm"` + `classification_type="sdlc"` → `nudge_continue` case to
      `session_type="eng"`, so the renamed delivery branch is exercised.
- [ ] **Bare-string `session_type` literal renames (BLOCKER C5-B1)** — UPDATE: tests pinned to the
      renamed reader/writer/label sites assert `session_type="pm"`/`"dev"` and will break on the rename.
      Verified affected: `tests/unit/test_sdlc_stage_marker.py`, `tests/unit/test_sdlc_utils.py`,
      `tests/unit/test_sdlc_session_ensure.py` (+ `tests/integration/test_sdlc_session_ensure_integration.py`,
      `tests/integration/test_sdlc_cross_repo_resolution.py`), `tests/unit/test_check_ttft.py` (TTFT
      metric label), and any reflection-scheduler / sustainability-digest test asserting the enqueued
      `session_type`. Switch every `"pm"`/`"dev"` expectation to `"eng"`. Run the BLOCKER C5-B1 grep gate
      (extended to `tests/`) to confirm none remain.
- [ ] `tests/unit/test_continuation_pm.py` and `tests/integration/test_continuation_pm_handoff.py`
      (BLOCKER C4-B2) — DELETE/REPLACE: both import `_create_continuation_pm` directly from
      `agent.agent_session_queue`; disposition must stay consistent with the Task 3 decision on
      `_create_continuation_pm` (delete the tests if the symbol is deleted; otherwise rewrite for the
      surviving caller).
- [ ] Child-session tests (`waiting_for_children`, `_finalize_parent_sync`,
      `VALOR_PARENT_SESSION_ID`) — VERIFY-UNCHANGED + ADD: the pattern survives; add/keep a test
      proving `VALOR_PARENT_SESSION_ID` propagates into container-spawned children after the rename.
- [ ] **`tests/unit/test_bridge_dispatch_contract.py` (BLOCKER C7-B1) — NO CHANGE (correction):** the
      cycle-7 critique claimed this test "imports `SessionType.PM` at line 200" and asked for an UPDATE.
      **Verified against live `main`: the file has ZERO `SessionType` references** — it is an AST contract
      test asserting `dispatch_telegram_session` enqueues-then-records-dedup, and the live-handler call at
      line 200 passes no `session_type` kwarg. Changing the `bridge/dispatch.py:87` default from
      `SessionType.PM` to `SessionType.ENG` does not touch this test's assertions, so **no edit is
      required** here. The `bridge/dispatch.py` fix is covered by the Task 2 file-list bullet and the
      `SessionType.PM\b` Verification grep.
- [ ] `bridge/routing.py` persona-resolution tests (`Dev:`/`PM:`/`Eng:` prefixes) — UPDATE: assert
      `Eng:` resolves engineer; `Dev:`/`PM:` no longer special.
- [ ] `valor-session` role tests — UPDATE: `eng`/`teammate` accepted; `dev`/`pm` rejected.
- [ ] `bridge/email_bridge.py` persona→session_type tests — UPDATE: `else` branch yields `ENG`.
- [ ] Migration scripts — ADD: new unit/integration tests for `migrate_session_type_pm_to_eng.py` and
      `merge_dev_chat_into_eng.py` (dry-run, idempotency, error path), project-scoped to a `test-`
      prefix, ORM-only cleanup.
- [ ] **`models/agent_session.py` alias/property/worker_key tests (BLOCKER C6-B1)** — each verified
      against live `main`:
      - `tests/unit/test_enums.py` — UPDATE: lines 12-60 assert `SessionType.PM`/`SessionType.DEV` values,
        membership, and `from models.agent_session import SESSION_TYPE_DEV, SESSION_TYPE_PM` alias equality
        (54-60). Switch every `PM`/`DEV` to `ENG`; assert `PM`/`DEV` no longer exist; rewrite the alias
        import/equality block to `SESSION_TYPE_ENG` (drop `SESSION_TYPE_DEV`).
      - `tests/integration/test_agent_session_queue_session_type.py` — UPDATE/REPLACE: imports
        `SESSION_TYPE_DEV, SESSION_TYPE_PM` (line 16) and asserts them at 53/79/197-198. Switch to
        `SESSION_TYPE_ENG`; `create_pm`→`create_eng` / `create_child`-default assertions follow the factory
        rename.
      - `tests/e2e/test_context_propagation.py` — UPDATE: imports both aliases (14-15), asserts
        `is_pm`/`is_dev` (235-236) and `filter(session_type=SESSION_TYPE_PM/DEV)` (256-257). Rewrite to
        `SESSION_TYPE_ENG` / `is_eng`; collapse the PM-vs-dev discriminator assertions to the single Eng type.
      - `tests/unit/test_agent_session.py` — UPDATE: `worker_key` tests (def L180+) cover PM-project_key
        (182), slugged-dev (231/239), PM-at-PLAN/ISSUE/CRITIQUE-stage→project_key (296/304/314), and
        `_PM_WORKTREE_STAGES` membership (277). Rename to `_ENG_WORKTREE_STAGES`; re-key the PM/dev cases to
        the single ENG branch; **the dev fall-through cases (slugged-dev-by-slug) now exercise the ENG
        worktree-stage path, NOT an always-slug path** — assert a slugless ENG at a PLAN-type stage returns
        `project_key` (the new Success Criterion below).
      - `tests/unit/test_steer_child.py` — UPDATE: stubs `parent.session_type="pm"` + `parent.is_dev=False`
        (17-19), `child.session_type="dev"` + `child.is_dev=True` (29-31), and `chat.is_dev=False` (175).
        Rewrite to the ENG vocabulary / `is_eng`, matching the `steer_child.py:94` guard rewrite.
      - `tests/integration/test_steering.py` — UPDATE: creates `session_type="dev"` (1476) and
        `session_type="pm"` (1494) fixtures. Switch both to `"eng"`.
- [ ] **Factory/property-call test files (CONCERN C8-5)** — each verified against live `main` (reference
      counts confirmed by `grep -c`):
      - `tests/e2e/test_nudge_loop.py` — UPDATE: `.create_pm()` → `.create_eng()` (**3** refs).
      - `tests/e2e/test_queue_isolation.py` — UPDATE: `.create_pm()` → `.create_eng()` (**8** refs).
      - `tests/e2e/test_session_lifecycle.py` — UPDATE: `.create_pm()` → `.create_eng()`, `.is_pm` →
        `.is_eng` (**9** refs total).
      - `tests/e2e/test_error_boundaries.py` — UPDATE: `.create_pm()` → `.create_eng()` (**5** refs).
      - `tests/integration/test_bridge_routing.py` — UPDATE: `.is_pm`/`.is_dev` → `.is_eng`, and flip the
        three `assert ... session_type == "pm"` asserts at **lines 133/161/176** → `"eng"` (**11** refs
        total).
      - `tests/unit/test_session_completion_dev_spawn_no_truncation.py` — UPDATE/DELETE: imports
        `_create_continuation_pm` **from `agent.session_completion`** (line 23, def at
        `session_completion.py:277`) and calls it at line 105. **Correction vs critique:** the critique
        row did not name the import module; verified live the import is `from agent.session_completion
        import _create_continuation_pm`, **not** `agent.agent_session_queue` (that is `test_continuation_pm.py`,
        the C4-B2 row). Disposition follows Task 3's keep/delete decision for `_create_continuation_pm`:
        delete the test if the symbol is deleted, otherwise update the call.
      - `tests/unit/test_pm_session_factory.py` — DELETE or REPLACE entirely: it asserts `create_pm`/
        `create_dev` exist, that `--role pm` appears in `sdk_client.py`, and that `project-manager.md`
        contains the fan-out section — **all four assertions invert** once this plan ships (factories
        renamed, `--role pm` rejected, persona merged into `engineer.md`, fan-out deleted). 17 matching
        lines verified live.

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
**Impact:** `agent/sdk_client.py` branches on `project.get("mode") == "pm"` at seven sites independently
of `SessionType` (three `== "pm"` guards at 1173/2119/3164, four `!= "pm"` complements at
3082/3277/3605/3693; line 3647 is a comment); a project still carrying `"mode": "pm"` silently keeps PM
rails / suppresses `WORKER_RULES` / skips SDLC classification under the new Eng model.
**Mitigation:** Two-part fix — (1) Task 3 deletes all three `== "pm"` guards and all four `!= "pm"`
complements so the Eng model has one code path; (2) the per-machine runbook strips `"mode": "pm"` from
vault projects.json (the mode validator normalizes the absence to `"dev"`, so it is safe).

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

### Race 1: Migration runs while the bridge, worker, OR email bridge is live
**Location:** `scripts/migrate_session_type_pm_to_eng.py`, `scripts/merge_dev_chat_into_eng.py`
**Trigger:** The Telegram bridge, the worker, **or the email bridge** creates/writes an AgentSession or
TelegramMessage mid-rename. There are **three** independent concurrent writers of `session_type="pm"`
records:
- The **worker** is the most dangerous: its hourly `agent-session-cleanup` reflection and
  `agent/reflection_scheduler.py:571` (which creates `session_type="pm"` sessions) run independently of
  the bridge, so a `pm` record can appear *during* the rename loop.
- The **email bridge** (`python -m bridge.email_bridge`, BLOCKER C2-B3) is a *third* process — started/
  stopped via `email-start`/`email-stop` (`scripts/valor-service.sh`) — that enqueues
  `session_type=SessionType.PM` AgentSessions (`bridge/email_bridge.py:879`) whenever inbound email
  resolves a non-teammate persona. An email arriving mid-rename writes a fresh `pm` record outside both
  the bridge and the worker; the worker-liveness guard cannot catch it (the email bridge writes
  `email:last_poll_ts`, not the worker heartbeat file).
Either path leaves an unmigrated record or rebuilds indexes on partial state.
**Data prerequisite:** No concurrent writer to the keys being renamed.
**State prerequisite:** **Telegram bridge, worker, AND email bridge stopped** on the target machine
(BLOCKER B3 + C2-B3).
**Mitigation:** The per-machine runbook stops **all three** writers — the Telegram bridge, the worker
(`valor-service.sh worker-disable` — stop *and* suppress launchd respawn), **and the email bridge
(`valor-service.sh email-stop`, BLOCKER C2-B3)** — before migration, and restarts them after.
**Defense-in-depth:** both migration scripts check `data/last_worker_connected` mtime at startup
(the file `_write_worker_heartbeat` rewrites every health tick, `agent/session_health.py:2058`) and
`sys.exit(1)` with a clear error if `(now - mtime) < threshold` (a *live* worker), so the migration
refuses to run against a live worker even if the operator forgets the stop step. They additionally
assert no live email bridge (`pgrep -f bridge.email_bridge` or a fresh `email:last_poll_ts`,
BLOCKER C2-B3) and `sys.exit(1)` if one is detected. (Do NOT key the worker guard off
`worker:registered_pid:*` — value is the PID, 24h TTL, cannot compute freshness; see Task 4.)
`rebuild_indexes()` runs after all renames so the index reflects the final key set.

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

0. **Heads-up to other group members first (NIT, User).** If any other humans are members of the
   `PM: {Project}` / `Dev: {Project}` groups, post a brief notice before renaming so the rename does
   not look like a group vanished — e.g. *"Consolidating PM:/Dev: → Eng: — one group, same purpose."*
1. **Stop the Telegram bridge, the worker, AND the email bridge (BLOCKER B3 + C2-B3).**
   `./scripts/valor-service.sh` stop the bridge, `./scripts/valor-service.sh worker-disable` (stop *and*
   suppress launchd auto-respawn — a plain `worker-stop` may be relaunched by `KeepAlive=true`), and
   `./scripts/valor-service.sh email-stop` (the email bridge enqueues `session_type=PM` records at
   `bridge/email_bridge.py:879` on inbound non-teammate email — a third concurrent `pm` writer). All
   three must stay down for the whole migration window: the worker's hourly cleanup +
   `reflection_scheduler.py` and the email bridge's inbound handler each create `session_type="pm"`
   records outside the Telegram bridge.
   **Email-bridge launchd asymmetry (NIT C7-N1):** `worker-disable` suppresses launchd respawn for the
   worker, but `email-stop` is a transient `bootout` — on machines that have installed the optional
   email-bridge launchd plist (`scripts/install_email_bridge.sh`), `KeepAlive=true` may relaunch the
   email bridge mid-migration, and the migration's `pgrep`/`email:last_poll_ts` guard is point-in-time,
   so a respawned bridge could write a fresh `pm` record after the guard passed. **On machines with the
   email-bridge launchd plist installed, unload it (`launchctl bootout` the
   `com.valor.email-bridge` label, or use the disable variant) before migrating, and re-load/re-enable
   it in step 6.** On machines without the plist, `email-stop` is sufficient.
2. **Telegram:** rename `PM: {Project}` → `Eng: {Project}` (preserves chat_id/history); archive
   `Dev: {Project}` (or rename `Dev:`→`Eng:` for Dev-only projects).
3. **Run the Redis migration(s) against the still-current (pre-update) code (BLOCKER B4):**
   `python scripts/migrate_session_type_pm_to_eng.py --dry-run` then live. This drains all `pm` records
   to `eng` *while `SessionType.PM` still exists in the running code*, so no record is ever compared
   against a deleted enum member. (Optionally, per the operator's per-project decision, run
   `python scripts/merge_dev_chat_into_eng.py --dry-run` then live — review the collision report first.)
   Both scripts refuse to run if a fresh `data/last_worker_connected` mtime OR a live `bridge.email_bridge`
   process is detected (defense-in-depth for step 1; the worker guard reads the heartbeat *file* mtime,
   not the 24h-TTL `worker:registered_pid:*` key — see Task 4).
4. **Edit vault `projects.json`:** replace `PM:`/`Dev:` group declarations with the single `Eng:`
   group, set persona `engineer`, **and remove any `"mode": "pm"` key (BLOCKER B2)** — the mode
   validator normalizes a missing/unknown mode to `"dev"`, so stripping it is the safe operational
   complement to deleting the `project_mode == "pm"` code guards in Task 3. **Also update any
   `pm_briefing.target_groups` entries from `PM: {Project}` to `Eng: {Project}` in the same vault edit
   (CONCERN C4-C1)** — `pm_briefing.target_groups` (see `config/projects.example.json:72`,
   `["PM: My Project"]`) names the group the daily PM voice briefing is delivered to via the Telegram
   outbox; leaving it pointing at the renamed/archived `PM:` group silently sends briefings to a dead
   group. Validated by `bridge/config_validation.py::validate_projects_config` at update Step 4.6.
5. **`/update`** on the machine: pulls the merged (`PM`-less) code, `env_sync`/restart proceed normally.
   Because step 3 already drained the `pm` records, the new code never encounters a `session_type=pm`
   record.
6. **Re-enable + restart the worker** (`worker-start` re-enables launchd respawn), the Telegram bridge,
   **and the email bridge (`email-start`)**; **on machines where step 1 unloaded the email-bridge launchd
   plist, re-load/re-enable it now (NIT C7-N1)**; verify end-to-end via
   `valor-telegram read --chat "Eng: {Project}"`.
7. **Post-restart steady-state canary — confirm no writer re-mints `pm`/`dev` records (CONCERN C5-C1,
   Operator).** Message delivery (step 6) proves the read path; it does **not** prove the renamed writers
   (`reflection_scheduler.py`, `sustainability.py`, the email bridge) stopped minting wrong-typed
   sessions. On the Cowboy pilot, after the worker has been up through **at least one reflection cycle**
   (so the hourly scheduler has run), run this ORM-only check (never raw Redis):
   `python -c "from models.agent_session import AgentSession; bad=[s for s in AgentSession.query.all() if s.session_type in ('pm','dev')]; print(len(bad)); raise SystemExit(1 if bad else 0)"`.
   Exit 0 = clean. **Break-glass canary:** if any `pm`/`dev` session reappears (exit 1), a writer site was
   missed — `worker-disable` immediately and investigate (re-run the BLOCKER C5-B1 grep gate to find the
   un-renamed literal).

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
- [ ] In the renamed architecture doc, document the **`AccessLevel.PM_READONLY` removal + work-vault
      `CLAUDE.md` re-gate (BLOCKER C7-B3)**: `PM_READONLY` is deleted; the per-project work-vault
      `CLAUDE.md` business-context layer now rides the `(ENGINEER, AccessLevel.WORKER)` cell of
      `compose_system_prompt`, and `load_pm_system_prompt` is renamed `load_eng_system_prompt`. State
      that this preserves the business-context behavior the old PM persona had — it is not a silent
      capability drop.
- [ ] Remove/replace `docs/features/sdlc-parallel-execution.md` (multi-dev fan-out deleted).
- [ ] Update `docs/features/single-machine-ownership.md` examples to the `Eng:` group shape.
- [ ] Update `docs/features/README.md` index table for any renamed/removed pages.
- [ ] In the renamed `eng-session-architecture.md` **and** `CLAUDE.md`, document the corrected
      conversational-vs-work group contract (CONCERN/User, BLOCKER C2-B4): **`Eng: {Project}` is one
      engineering surface that responds to direct messages for BOTH quick questions and work** (the
      granite container answers conversationally before/instead of doing work); **teammate DMs
      (`dm_persona`) remain the casual Q&A path.** Do NOT document a `Teammate: {Project}` group as the
      quick-question surface — no such group exists and group teammate routing is @mention-gated
      (`config/projects.example.json:7`). This is the real replacement for the retired `PM:` lightweight
      surface.

### Inline Documentation
- [ ] Update `config/enums.py` `SessionType`/`PersonaType` docstrings (remove pm/dev prose; describe
      eng + retained CLI-only granite).
- [ ] Update `config/personas/engineer.md` (merged persona) docstring/header.

### Config & Command Surfaces
- [ ] Update `config/projects.example.json` to the single `Eng:` group + `engineer` persona shape,
      **including the `pm_briefing.target_groups` example (line 72, `["PM: My Project"]` → `["Eng: My Project"]`)
      (CONCERN C4-C1)** and the `projects.telegram.groups` doc string at line 7.
- [ ] Update `CLAUDE.md` command tables / architecture prose mentioning PM→Dev session spawning,
      `--role dev`/`--role pm`, and `sdlc-decompose`.
- [ ] **Update the three skill files that invoke `valor-session create --role dev` → `--role eng`
      (CONCERN C8-3; lines verified against live `main`):**
  - [ ] `.claude/skills-global/sdlc/SKILL.md:228` (`valor-session create --role dev --parent ...`) —
        **HIGHEST priority: this is a *global* skill, hardlinked to `~/.claude/skills/` on every machine
        by `scripts/update/hardlinks.py`, so a stale `--role dev` here ships everywhere and breaks the
        SDLC dispatch the instant the CLI starts rejecting `dev`.**
  - [ ] `.claude/skills/x-com/SKILL.md:45` (`valor-session create --role dev --project-key valor ...`).
  - [ ] `.claude/skills/linkedin/SKILL.md:85` (`--role dev \` in a multi-line invocation).

## Success Criteria

- [ ] `SessionType` contains `ENG`, `TEAMMATE`, `GRANITE` (CLI-only); **no `pm` or `dev` value remains
      anywhere** in code (enums, hooks, router, CLI, dashboard, persona files, tests). **Mechanically
      enforced** by two greps that must both exit 1: the `SessionType.PM`/`SessionType.DEV` enum-name
      rows AND the codebase-wide bare-string `session_type` literal gate (BLOCKER C5-B1, Verification
      table) — the latter is what catches the writer/reader/label sites
      (`reflection_scheduler.py`, `sustainability.py`, `sdk_client.py:2408`, the three `sdlc_*` readers)
      that carry `"pm"`/`"dev"` as plain strings invisible to the enum-name greps.
- [ ] `bridge/routing.py` resolves an `engineer` persona from config and an `Eng:` title prefix;
      `Dev:`/`PM:` fallbacks are gone.
- [ ] `valor-session create` accepts roles `eng` and `teammate` only; `--role dev`/`--role pm` are
      rejected with a clear error.
- [ ] `SessionType.ENG` resolves `AccessLevel.WORKER` in `agent/sdk_client.py` (test asserts the
      resolved access level).
- [ ] **`AgentSession.worker_key` preserves per-project serialization for ENG (BLOCKER C6-B1):** a
      slugless ENG session, and a slugged ENG session at a main-checkout stage (PLAN/ISSUE/CRITIQUE/MERGE),
      both return `project_key` from `worker_key` (not the slug) — proving ENG inherited the PM
      stage-aware branch and the dead slug-always dev fall-through is gone. A slugged ENG at a
      worktree-compatible stage (BUILD/TEST/PATCH/REVIEW/DOCS) returns the slug. Asserted via
      `tests/unit/test_agent_session.py` against the renamed `_ENG_WORKTREE_STAGES`.
- [ ] **`import models.agent_session` succeeds with `config/enums.py` collapsed (BLOCKER C6-B1):** the
      module-level `SESSION_TYPE_*` aliases (lines 81/83) no longer reference a deleted enum member, so
      `python -c "import models.agent_session"` exits 0 — worker, bridge, email bridge, and test collection
      all import cleanly.
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
- [ ] **Conversational-question behavior verified on `Eng:` (CONCERN C4-C3):** during the Cowboy pilot,
      a plain factual question (e.g., "what branch is this repo on?") sent to `Eng: Valor` receives a
      conversational reply **without opening a GitHub issue or creating a PR** — confirming the single
      Eng surface answers questions as well as doing work.
- [ ] **Work-request behavior verified on `Eng:` — the inverse paired AC (CONCERN C6, User):** during the
      Cowboy pilot, a concrete work request (e.g., "fix the login bug") sent to `Eng: Valor` **spawns a
      granite container execution** — verify via `python -m tools.valor_session list` that an `eng` session
      was created and left `pending`/`running`, and a Telegram reply arrives in the `Eng:` group. This
      guards against the conversational-first path swallowing real work requests, the inverse of the
      question-does-not-spawn-work criterion above.
- [ ] **`pm_briefing` delivery verified post-rename if the pilot project uses it (CONCERN C4-C1):** if
      `valor`/`popoto` has `pm_briefing.enabled`, confirm the daily briefing still lands in the renamed
      `Eng: {Project}` group after `target_groups` is updated.
- [ ] **Reply-to steering survives the `Eng:` rename (CONCERN C5-C4, User):** with a work session already
      running in `Eng: Valor`, send a **reply-to** message against that session's thread and verify
      (a) the bridge **routes it to steering** — it lands in `AgentSession.queued_steering_messages` on the
      *existing* session rather than spawning a new `eng` session — and (b) the steered reply is delivered
      back to the `Eng:` group. This proves the bridge's reply-to session-continuation (keyed on Telegram
      thread ID, not group name) survives the `PM:`→`Eng:` group rename; check via
      `python -m tools.valor_session status --id <ID>` (pending steering messages) on the live session.
- [ ] **Background-job alerts reach `Eng: Valor` post-rename (BLOCKER C7-B4):** the grep gate proves no
      `"Dev: Valor"` delivery literal survives in `reflections/ scripts/ agent/`; additionally, on the
      Cowboy pilot, trigger at least one background-job alert path and confirm it lands in the renamed
      group — e.g. `python scripts/nightly_regression_tests.py --dry-run` (or a live tick) sends to
      `Eng: Valor`, verified via `valor-telegram read --chat "Eng: Valor"`. Guards against
      nightly-test / Sentry-triage / docs-audit / memory-consolidation / hibernate-wake alerts silently
      going dark into the archived `Dev: Valor` group.
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
- **Validates**: `tests/unit/` enum/persona tests; `tests/unit/granite_container/test_cli.py` (GRANITE unchanged);
  **`tests/unit/test_enums.py` (alias-equality), `tests/unit/test_agent_session.py` (`worker_key`/`_ENG_WORKTREE_STAGES`),
  and the `import models.agent_session` smoke (BLOCKER C6-B1) — the ENG alias/worker_key surgery must keep these green;
  `tests/unit/test_compose_system_prompt.py` (re-baselined for `(ENGINEER, WORKER)` after the PM_READONLY removal, BLOCKER C7-B3)**
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- `config/enums.py`: `SessionType` → `{ENG, TEAMMATE, GRANITE}` (`pm`→`eng`, remove `DEV`, keep
  `GRANITE`); `PersonaType.DEVELOPER`+`PROJECT_MANAGER` → `ENGINEER`; **delete `AccessLevel.PM_READONLY`
  (config/enums.py:67) and its docstring block (lines 54-58) (BLOCKER C7-B3)** — after Task 2/Task 3
  remove every resolver that returns it, the member and its rails become unreachable dead code (NO
  LEGACY CODE). The behavior it carried (the work-vault `CLAUDE.md` context layer) is **re-gated onto
  the WORKER branch for eng sessions** in Task 2 — see the BLOCKER C7-B3 bullet there; update docstrings.
- **`ui/data/machine.py:52-57` — `persona_order` dict keyed on deleted `PersonaType` members (BLOCKER
  C7-B2):** the dashboard machine-page sort builds a dict literal **inside a function body** —
  `persona_order = {PersonaType.PROJECT_MANAGER: 0, PersonaType.DEVELOPER: 1, PersonaType.TEAMMATE: 2}`
  (lines 53-55, used at line 57). Because it is a runtime literal, not a module-level constant, it
  raises `AttributeError` only on the **first dashboard machine-page render** — deferred past import
  smoke tests and likely past CI. **Rekey to `{PersonaType.ENGINEER: 0, PersonaType.TEAMMATE: 1}`**
  (collapse the two deleted PM/DEV entries into the single ENGINEER rank). Verified live: dict at
  52-57 (critique cited 50-57 — minor drift, corrected here).
- **`scripts/capture_persona_baseline.py:9` (BLOCKER C7-B2, docstring) — UPDATE:** the
  `(DEVELOPER, WORKER)` / `(PROJECT_MANAGER, PM_READONLY, work_dir)` baseline-cell label lives in the
  module **docstring** (line 9), not a runtime dict, so it does not crash — but it names the deleted
  `(persona, access_level)` pairs. Rewrite the docstring cell labels to `(ENGINEER, WORKER)` (the single
  surviving cell) so the baseline-capture prose matches the collapsed enum. Verified live: the only
  `PROJECT_MANAGER`/`DEVELOPER` reference in the file is this docstring line.
- **`models/agent_session.py` — the core ORM model; absent from prior cycles' file list and from every
  Verification grep scope (BLOCKER C6-B1, flagged by all 7 critics). Its module-level aliases evaluate at
  import, so leaving them un-renamed crashes `import models.agent_session` — and therefore worker, bridge,
  email bridge, and test collection — the instant `config/enums.py` drops `PM`/`DEV` (`AttributeError`, no
  graceful degradation). Per-site dispositions (line numbers verified against live `main`):**
  - **Module-level aliases (lines 81, 83):** `SESSION_TYPE_PM = SessionType.PM` → `SESSION_TYPE_ENG =
    SessionType.ENG`; **delete** `SESSION_TYPE_DEV = SessionType.DEV` (the `DEV` member is gone). Keep
    `SESSION_TYPE_TEAMMATE` (line 82). Update every in-file reference to the renamed/removed aliases below.
  - **`worker_key` property (def line 472) — the #828 main-checkout race guard (BLOCKER C6-B1 item 2):**
    the `if self.session_type == SessionType.PM:` branch (line 494) does stage-aware serialization
    (slug only at worktree-compatible stages via `_pm_stage_is_worktree_compatible()`, line 505; else
    `project_key`), while the dev fall-through (lines 500-503) is **slug-keyed always**. Post-rename ENG
    sessions must NOT fall into the dev path — that would drop per-project serialization at main-checkout
    stages (PLAN/ISSUE/CRITIQUE/MERGE/None), letting two ENG sessions with the same slug at different
    stages run concurrently on the main checkout (the exact race the PM branch prevents). **ENG inherits
    the PM branch logic verbatim:** rename the branch to `if self.session_type == SessionType.ENG:`, keep
    the slug-at-worktree-stages-else-project_key logic, and **delete the dead dev fall-through** (lines
    500-503). Rename the helper `_pm_stage_is_worktree_compatible` → `_eng_stage_is_worktree_compatible`
    (def line 505) and the allowlist `_PM_WORKTREE_STAGES` → `_ENG_WORKTREE_STAGES` (line 469); update the
    docstring (lines 477-486) from "PM sessions" / "Dev sessions" to the single Eng path.
  - **Properties (lines 1207-1219):** `is_pm` (def 1207) → `is_eng` (`self.session_type ==
    SESSION_TYPE_ENG`); **delete** `is_dev` (def 1217) — the `DEV` member is gone and `scripts/steer_child.py`
    is the only external consumer (handled in Task 3). Keep `is_teammate` (1211). Update any in-repo
    callers of `is_pm`/`is_dev`.
  - **Factory methods (def lines, verified — the cycle-6 critique's 1322/1375/1430 were approximate):**
    `create_pm` (def 1305) → `create_eng` (body `session_type=SESSION_TYPE_ENG`, line 1322);
    `create_local` (def 1369) default `session_type: str = SESSION_TYPE_DEV` (line 1375) → default
    `SESSION_TYPE_ENG`; `create_child` (def 1396) body `session_type=SESSION_TYPE_DEV` (line 1430) →
    `SESSION_TYPE_ENG`; `create_dev` (def 1444, a backward-compat wrapper for `create_child`) — fold/rename
    to `create_eng` semantics or delete if no surviving caller (audit alongside Task 3).
  - **Docstrings/comments:** the **module-level docstring (lines 1-20, CONCERN C8-4)** — verified live to
    carry `session_type="pm"` at **line 10** ("PM session (session_type=\"pm\"): Read-only ...") and
    `session_type="dev"` at **line 14** ("Dev session (session_type=\"dev\"): Full-permission ...");
    rewrite both to the single `eng` path — **plus** the class docstring (lines 87-112: "pm/teammate/or
    dev" prose, the `PM session`/`Dev session` permission-model blocks at lines 93/99, the
    `create_pm`/`create_dev` factory list) and the field comment at line 144 (`# "pm", "teammate", or
    "dev" — discriminator`) → describe `eng`/`teammate`. **Why the module docstring matters mechanically:**
    the bare-literal Verification grep (`session_type\s*=\s*["']?(pm|dev)`) matches **4 hits in this file**
    — lines 10, 14, 93, 99 — and `grep -v` strips comment/docstring false positives only when they carry
    `#`/`::`; the prose `session_type="pm"` lines do not, so they would survive the `grep -v` and keep the
    bare-literal gate from reaching exit 1 unless the module docstring is rewritten too.
- **Pre-edit test-call enumeration hint (CONCERN C8-5):** before renaming the `create_pm`/`is_pm`/`is_dev`/
  `create_dev` model surface, run
  `grep -rn '\.create_pm\b\|\.is_pm\b\|\.is_dev\b\|\.create_dev\b' tests/ --include="*.py"` to enumerate
  every test call site up front — the seven test files in Test Impact (`test_nudge_loop`, `test_queue_isolation`,
  `test_session_lifecycle`, `test_error_boundaries`, `test_bridge_routing`,
  `test_session_completion_dev_spawn_no_truncation`, `test_pm_session_factory`) plus the BLOCKER C6-B1 set
  all surface here. Map each to its Test Impact disposition before editing.
- Merge `config/personas/project-manager.md` + `developer.md` → `config/personas/engineer.md`; update
  `config/personas/segments/manifest.json` (correct path — manifest lives under `segments/`, not directly
  under `config/personas/`) / segment references.

### 2. Rewire bridge routing, SDK client, email bridge
- **Task ID**: build-routing
- **Depends On**: build-enums-persona
- **Validates**: `bridge/routing.py` persona tests; email-bridge persona tests; **`VALOR_PARENT_SESSION_ID`
  propagates into container-spawned (pooled-PTY) children after the rename (folded in from former Task 5,
  CONCERN C4-C4) — add/keep a test asserting it**; **`tests/unit/test_compose_system_prompt.py` — the
  re-gated work-vault `CLAUDE.md` layer now appends on the `(ENGINEER, WORKER)` branch (BLOCKER C7-B3)**;
  **`bridge/dispatch.py` import smoke — the `SessionType.ENG` default param must not raise (BLOCKER C7-B1)**
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- `bridge/routing.py`: resolve `engineer`; `Eng:` prefix fallback only; delete `Dev:`/`PM:` branches +
  `is_team_chat` prefix tuple.
- `bridge/telegram_bridge.py` + `bridge/email_bridge.py`: map to `SessionType.ENG`.
- **`bridge/dispatch.py:87` — `def`-time default param crash (BLOCKER C7-B1):** the
  `dispatch_telegram_session(..., session_type: str = SessionType.PM, ...)` default binds at **import
  time**, and `bridge/telegram_bridge.py:107` imports `bridge.dispatch`, so the bridge raises
  `AttributeError` at startup the instant Task 1 deletes `SessionType.PM` — before serving a single
  message. This file was in **no** prior task list. **Change the default to `SessionType.ENG`.** Before
  editing, run `grep -n 'SessionType\.' bridge/dispatch.py` to confirm no other member defaults in the
  file (verified live: line 87 is the sole `SessionType.` reference). The existing `SessionType.PM\b`
  Verification grep over `bridge/` already backstops this, but the task-level assignment is the fix.
- `agent/sdk_client.py`: `(ENGINEER, AccessLevel.WORKER)` resolution in `compose_system_prompt`
  + `_resolve_*` (~1168); re-gate `VALOR_PARENT_SESSION_ID` (~1595) on ENG/Teammate.
- **Re-gate the work-vault `CLAUDE.md` context layer onto the eng/WORKER path (BLOCKER C7-B3; decided —
  preserve the layer, do not silently drop it):** with `SessionType.ENG` resolving
  `(ENGINEER, AccessLevel.WORKER)`, the persona falls through `_resolve_persona`→`engineer`→
  `_access_level_for_persona`→`WORKER` (engineer is not PM/TEAMMATE/CS, so it hits the default
  `return AccessLevel.WORKER`, verified live at sdk_client.py:1186-1194), and the
  `if access_level == AccessLevel.PM_READONLY:` work-vault-`CLAUDE.md`-append block at
  **sdk_client.py:1109-1116** becomes unreachable. That block is the **only** site that injects the
  per-project work-vault `CLAUDE.md` business-context layer; deleting PM_READONLY without re-gating
  would **silently strip business context** from every eng session the PM persona used to receive.
  **Concrete surgery (verified against live `main`):**
  - The `AccessLevel.WORKER` branch (lines 1098-1107) `return`s early, *before* the PM_READONLY block —
    so re-gating cannot be a simple gate rename. **Fold the work-vault `CLAUDE.md` append INTO the
    WORKER branch:** when `access_level == AccessLevel.WORKER` and a `working_directory` is provided and
    a `CLAUDE.md` exists there, append it to the composed WORKER prompt (the same
    `Path(working_directory) / "CLAUDE.md"` read the PM_READONLY block did at 1110-1115), then return.
    Make `working_directory` optional for WORKER (no longer required) — the eng path may run without a
    vault dir, in which case the layer is simply skipped (no raise). **Delete** the
    `access_level == AccessLevel.PM_READONLY` validation raise at sdk_client.py:1087-1091 and the whole
    PM_READONLY append block at 1109-1116.
  - Update the `compose_system_prompt` docstring (lines 1040-1068) that still says the vault layer
    applies "only when `access_level == AccessLevel.PM_READONLY`" and references the
    `(PROJECT_MANAGER, PM_READONLY, work_dir)` cell → describe the `(ENGINEER, WORKER)` cell.
  - **Audit and clean every remaining `PM_READONLY` consumer** so the deletion is total (verified live
    references): sdk_client.py:1040, 1044, 1058, 1068 (docstrings), 1087-1091 (validation raise),
    1109-1116 (append block), 1141, 1153 (docstrings), 1169, 1174 (resolver returns — gone with the
    SessionType.PM / project_mode=="pm" deletions in Task 2/Task 3), 1187 (`_access_level_for_persona`
    PROJECT_MANAGER→PM_READONLY mapping — the PROJECT_MANAGER branch is removed with the persona merge),
    1243 (docstring/example), 3646 (`if _access_level == AccessLevel.PM_READONLY:` guard). None may
    survive once the member is deleted.
- **`agent/session_executor.py:1658` — mirror the re-gate (BLOCKER C7-B3):** the
  `if _composed_access_level == AccessLevel.PM_READONLY:` guard (line 1658) that calls
  `load_pm_system_prompt(str(working_dir))` (imported at line 1526, called 1660) gates the SDLC-
  orchestration system-prompt load the same way. **Re-gate it on the eng/WORKER path** (e.g.
  `_composed_access_level == AccessLevel.WORKER`) so eng sessions still load it, and **rename
  `load_pm_system_prompt` → `load_eng_system_prompt`** (its name no longer reflects the persona) at its
  definition and both references here. Update the `[pm-persona-missing]` warning label accordingly.
- **Parent-linkage check (folded in from former Task 5, CONCERN C4-C4):** confirm
  `VALOR_PARENT_SESSION_ID` propagates into container-spawned (pooled-PTY) children after the rename;
  add/keep a test asserting it.

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
  - **Four additional `SessionType.PM` sites in this same file must be renamed to `SessionType.ENG`
    (CONCERN C8-1; verified against live `main`):** beyond the re-export block, `agent_session_queue.py`
    carries the bare-`pm` work discriminator at four sites the re-export-only scope missed —
    - **Lines 223 and 1085** — `def`-time default params `session_type: str = SessionType.PM` (in
      `_push_agent_session` and `enqueue_agent_session`) → `SessionType.ENG`. These **bind at import time**
      (the same class of crash as the `bridge/dispatch.py:87` C7-B1 fix), so leaving them un-renamed means
      every enqueue without an explicit `session_type` silently defaults to the deleted `PM` member.
    - **Lines 385 and 1146** — `elif session_type == SessionType.PM: _wk = project_key` inline
      worker_key branches (each carries a `# KEEP IN SYNC with AgentSession.worker_key` comment) →
      `SessionType.ENG`, preserving the surrounding `TEAMMATE`/`ENG`/`else` decision tree (the
      `if session_type == SessionType.TEAMMATE:` branch at 383/1144 is unchanged).
    - **Post-edit check for the builder:** `grep -n 'SessionType' agent/agent_session_queue.py` — expected
      survivors are only `TEAMMATE` and `ENG` (plus the line-134 import).
    - **Disputed-finding note:** this was a contested cycle-8 finding (Skeptic voted BLOCKER; three other
      critics dismissed it as mechanically gated by the existing `SessionType.PM\b` Verification grep over
      `agent/`). Resolved here by **explicit enumeration** so the builder needs no re-investigation — the
      grep gate would catch it, but the four-site list removes any ambiguity about which sites and which
      replacement.
  - Also audit `agent/hooks/pre_tool_use.py:503` and `agent/output_router.py:13,118` which reference
    `_handle_dev_session_completion` in comments/logic.
  - **Expand the audit grep beyond `agent/` (BLOCKER C4-B2):** run
    `grep -rn "_handle_dev_session_completion\|_create_continuation_pm\|_transition_parent" agent/ tools/ tests/`
    — the `agent/`-only scope misses two live callers: **`tools/agent_session_scheduler.py:417-419`**
    (`from agent.agent_session_queue import _transition_parent` then
    `_transition_parent(parent_session, "waiting_for_children")` — `_transition_parent` is a KEEPER; the
    hazard is careless restructuring of the `agent/agent_session_queue.py:49-53` re-export block →
    ImportError kills the scheduler at startup) and **`tests/unit/test_continuation_pm.py` +
    `tests/integration/test_continuation_pm_handoff.py`** (both import `_create_continuation_pm` directly
    from `agent.agent_session_queue`). The `_transition_parent` re-export MUST survive the removal; the
    `python -c "import agent.agent_session_queue"` verification only protects the scheduler if it does.
- **Fix the hardcoded `session_type == "pm"` delivery branch (BLOCKER C4-B1):**
  `agent/output_router.py:159` (in `determine_delivery_action`, def at L79) reads
  `if session_type == "pm" and classification_type == "sdlc": return "nudge_continue"` — a bare string
  literal, NOT `SessionType.PM`. After the rename, eng sessions carry `"eng"` and this branch never
  fires, so SDLC sessions get `deliver` instead of `nudge_continue`, silently breaking the pipeline
  auto-continue loop. (Note: the critique cited line 636; the literal is actually at **line 159** —
  verified against live `main`.) Change `"pm"` → `"eng"` (StrEnum equality means `SessionType.ENG == "eng"`,
  either form works). No other grep in the existing Verification table catches this — the `"pm"` grep
  scanned only `config/enums.py`.
- Delete `_handle_dev_session_completion()` + its dev-only callers/parent-steering in
  `session_executor.py` (import L17, the `if _session_type == "dev"` gate at L1914 + its call at L1915),
  `session_health.py`, `output_router.py`, **and the re-export in `agent/agent_session_queue.py`** —
  surgically, per the audit above. (The L1914 gate is the **only** `== "dev"` site that dies with this
  deletion; the other six setup-path `== "dev"` guards are RENAMED — see the executor `== "dev"`
  enumeration below, BLOCKER C6-B2.) In
  `agent/output_router.py`, additionally change the `session_type == "pm"` delivery literal at **line 159**
  to `"eng"` (BLOCKER C4-B1, above). **Must-not-break callers of the re-export block:**
  `session_health.py:1955,2015` (`_transition_parent`) and **`tools/agent_session_scheduler.py:417-419`**
  (`_transition_parent`) — leave `_transition_parent` exported.
- **Rename the six (verified seven) bare-string `session_type="pm"/"dev"` literal sites (BLOCKER
  C5-B1):** the cycle-4 `output_router.py:159` bug class repeats at scale — `session_type` literals that
  are **not** `SessionType.PM`/`SessionType.DEV` references, so the enum-name greps in the Verification
  table never see them. Post-rename they silently re-mint wrong-typed sessions (writers) or return zero
  results (readers). Each site was re-verified against live `main`; dispositions below. **None of the
  three `tools/sdlc_*` reader files are deleted by Task 3's fan-out removal** — Task 3 deletes only
  `tools/sdlc_decompose.py`; `sdlc_stage_marker.py`, `sdlc_session_ensure.py`, and `_sdlc_utils.py` are
  part of the live `sdlc-tool` family (stage-query / verdict / dispatch / meta-set), so they are
  **renamed, not deleted**.
  - **Writers (re-mint wrong-typed records forever post-restart):**
    - `agent/reflection_scheduler.py:571` — real `_push_agent_session(..., session_type="pm")` (verified;
      holds). Rename to `"eng"`. Previously cited in the plan only as a process to *stop* during
      migration (Race 1 / Prerequisites) — it is **also** a code site that must be renamed.
    - `agent/sustainability.py:610` — real `AgentSession.create_and_enqueue(..., session_type="dev")`
      (verified; holds). Rename to `"eng"`. (Its prompt body at `:603` also names the `'Dev: Valor'`
      chat — a cosmetic string the docs/runbook rename covers; the load-bearing fix is the
      `session_type` kwarg.)
    - `agent/cold_start_metrics.py:37` — **NOT a code site: it is a docstring `Usage::` example** (the
      `:17` hit is the JSON-schema docstring block). The real metric-label source is
      **`agent/sdk_client.py:2408`**: `_session_type_tag = "pm" if system_prompt else "other"`, passed
      into `record_ttft` via `_ttft_meta`. This is a **metrics label** written to
      `logs/cold_start_metrics.jsonl`, not a session create and not a worker query. Per the critique's
      "change to match what the worker queries", change the literal to `"eng"` and update the
      `cold_start_metrics.py` docstring examples (`:17`, `:37`) + the `session_type` param doc (`:78`,
      which lists `"pm"/"dev"`) to the `eng`/`teammate` vocabulary.
  - **Readers (query/filter — silently return zero post-migration, no crash):**
    - `tools/sdlc_stage_marker.py:99` — `AgentSession.query.count(session_type="pm")` (verified; holds).
      Rename to `"eng"`.
    - `tools/sdlc_session_ensure.py:139` (create `session_type="pm"`) and `:188`
      (`AgentSession.query.filter(session_type="pm", status="running")` running-PM gate) — **plus the
      `:83` `getattr(resolved, "session_type", None) == "pm"` gate the critique did not enumerate** and
      the `:170` docstring line. All four verified; rename every `"pm"` literal to `"eng"`. Distinct from
      the `--role` CLI rejection already listed for this file.
    - `tools/_sdlc_utils.py:97` (`AgentSession.query.filter(session_type="pm")`) — **plus the `:87` and
      `:162` `getattr(s, "session_type", None) == "pm"` gates the critique cited only `:97` for.** All
      three verified; rename to `"eng"`.
    - **Three additional live reader gates the comparison spot-check surfaced (not in the critique's six,
      but caught by the new `== "pm"`/`!= "pm"` Verification row, so they must be renamed for the plan to
      stay internally consistent):** `agent/session_pickup.py:119` (`session_type != "pm"` resume-hydration
      gate), `tools/sdlc_stage_query.py:66` (`== "pm"` PM-session preference), and
      `tools/stage_states_helpers.py:99` (`== "pm"` canonical stage_states owner). All verified live; rename
      every `"pm"` to `"eng"`.
  - **`agent/session_executor.py` `== "dev"` gates — seven sites, per-site disposition (BLOCKER C6-B2):**
    the prior cycles' claim that "`781,1914` vanish with the dev-completion deletion" was **wrong** — only
    one of the seven gates lives in the dev-completion path. Each comparison reaches `"dev"` via an alias
    variable (`_stype_pre` line 649, `_stype_early` line 826, `_stype` line 883, `_session_type` for the
    completion gate — all `getattr(session, "session_type", None)`), so they are invisible to the enum
    greps AND the `session_type=` assignment grep, and need the comparison spot-check extended to this file.
    Sites verified against live `main`:
    - **Line 652** (`if _stype_pre == "dev" and _slug_pre is None and _aid_pre is None:`) — the
      slugless-session rejection guard (#887/#1272 contamination protection; finalizes the session
      `failed`). **RENAME `== "dev"` → `== "eng"`** so the #887 guard still fires for eng sessions.
    - **Line 781** (`if not slug and getattr(session, "session_type", None) == "dev":`) — synthetic-slug
      synthesis for slugless work sessions (#1272), funnels them through worktree provisioning.
      **RENAME → `== "eng"`.**
    - **Line 828** (`_stype_early == "dev"` in the stageless-worktree branch-trust block) — #887 worktree
      branch-resolution guard. **RENAME → `== "eng"`.**
    - **Line 855** (`if _stype == "dev":` inside the worktree-creation `except`) — #887 FATAL guard that
      refuses to fall back to the main checkout on worktree-provisioning failure. **RENAME → `== "eng"`.**
    - **Line 885** (`_stype == "dev"` in the main-checkout protection guard) — #887 guard that raises if a
      slugged session resolves to the repo root. **RENAME → `== "eng"`.**
    - **Line 910** (`if _stype == "dev" and slug and WORKTREES_DIR in str(working_dir):`) — #1377
      branch-mismatch guard (`verify_worktree_branch`). **RENAME → `== "eng"`.**
    - **Line 1914** (`if _session_type == "dev" and not task.error:`) — the **only** gate inside the
      dev-completion path; it guards the `_handle_dev_session_completion(...)` call. **DELETED with the
      function** (per the deletion bullet below) — not renamed.
    The `:770, 1906, 1912` hits are **comments**, not code (the comparison spot-check `grep -v` drops them).
  - **Pre-removal `== "dev"` audit step (BLOCKER C6-B2):** before writing any executor diff, run
    `grep -n '_stype\b\|_stype_pre\b\|_stype_early\b\|_session_type\b\|"dev"' agent/session_executor.py`
    and map every hit to RENAME (the six setup-path guards: 652/781/828/855/885/910) or DELETED-WITH-FUNCTION
    (the single completion gate: 1914) before editing. Re-verify any new gate that appears in context.
  - **Rename the hardcoded `"Dev: Valor"` background-job delivery targets → `"Eng: Valor"` (BLOCKER
    C7-B4):** post-archive of the `Dev: Valor` group these `--chat "Dev: Valor"` / `chat="Dev: Valor"`
    send-targets still *resolve* — into a group nobody reads — so nightly-test failures, Sentry triage,
    docs audits, SDLC-progress pings, memory-consolidation alerts, and worker hibernate/wake notices all
    go dark. These are **delivery targets**, distinct from the persona-routing `"Dev:"` prefix removed by
    Task 2. **Eight verified delivery-target literals (grep'd live across the whole repo, the critique's
    six was an undercount):**
    - `reflections/docs_auditor.py:844` (`["valor-telegram", "send", "--chat", "Dev: Valor", message]`)
    - `reflections/sentry_triage.py:428` (same shape)
    - `reflections/sdlc_progress.py:210` (same shape)
    - `scripts/memory_consolidation.py:340` (same shape, `telegram_msg`)
    - `scripts/nightly_regression_tests.py:25` — module-level `TELEGRAM_CHAT = "Dev: Valor"`
    - `agent/sustainability.py:239` and `:250` — `f"Send a Telegram message to the 'Dev: Valor' chat..."`
      hibernate/wake prompt bodies (single-quoted, embedded in the prompt the worker executes)
    - `agent/sustainability.py:603` — `"Send via valor-telegram to the 'Dev: Valor' chat."` (already
      noted in the bare-literal block as the cosmetic companion to the `:610` `session_type` kwarg;
      rename the chat string here too so the alert lands in `Eng: Valor`)
    Rename every one to `"Eng: Valor"`. **Scope is `reflections/ scripts/ agent/` only.** Do NOT
    blanket-rename the `"Dev: Valor"` occurrences in `tests/` — those are persona-**routing** fixtures
    (`find_project_for_chat`, `_resolve_persona`, `is_team_chat` prefix tests) that exercise the OLD
    `Dev:` routing being deleted by Task 2; they are handled by the Task 2 routing-test updates, not this
    delivery-target rename. The `"Dev: Valor"` strings in docs/CLAUDE.md/skill examples are covered by
    the Documentation section's CLAUDE.md/tool-doc updates.
  - **`scripts/steer_child.py:94` (BLOCKER C6-B1 item 5):** `if not child.is_dev:` — `is_dev` is **deleted**
    from `agent_session.py` (Task 1), so this guard must follow. Replace with the eng equivalent
    (`if not child.is_eng:`) or remove the guard if it no longer makes sense under the single Eng role.
    `scripts/` was outside every prior grep scope; it is added to the Verification rows below.
- **Parent-sync machinery disposition (CONCERN, Consistency):** `_finalize_parent_sync` /
  `waiting_for_children` are general child-session machinery, **not** dev-specific — they survive the
  removal. `_handle_dev_session_completion` was only one *trigger* of the parent-sync path; the
  container-completion path and `session_health.py`'s `_transition_parent` calls remain. The
  parent-linkage check folded into Task 2 (formerly Task 5) confirms the surviving path still finalizes
  parents. The child-session tests
  (`waiting_for_children`, `_finalize_parent_sync`) are therefore **kept and must still pass**; only
  tests exercising the dev-completion *trigger* are deleted.
- Delete `tools/sdlc_decompose.py` + `pyproject.toml` `sdlc-decompose` entry +
  `MAX_PARALLEL_DEVS`/`PARALLEL_SAFE_PAIRS` in `agent/sdlc_router.py`.
- `tools/valor_session.py` (+ `valor_cli.py`, `sdlc_session_ensure.py`, `agent_session_scheduler.py`):
  accept `eng`/`teammate`, reject `dev`/`pm`.
- Remove PM read-only Bash rails for work sessions in `agent/hooks/pre_tool_use.py`.
- **Reconcile the `project_mode == "pm"` config side channel (BLOCKER B2; count corrected by CONCERN
  C2):** `agent/sdk_client.py` reads `project.get("mode", "dev")` and branches on the mode independently
  of `SessionType`, so after the rename a project still carrying `"mode": "pm"` in projects.json silently
  keeps PM rails / suppresses `WORKER_RULES` / skips SDLC classification. The verified surgery surface is
  **three `project_mode == "pm"` guards (lines 1173, 2119, 3164)** and **four `project_mode != "pm"`
  complements (lines 3082, 3277, 3605, 3693)** — seven sites total. (Line **3647 is a comment**, not a
  guard — do not count or edit it as code.) Fix: delete all three `== "pm"` guards and all four `!= "pm"`
  complements so the Eng model has a single code path. At line 3693 the `!= "pm"` collapses to
  always-true once `mode=pm` is stripped, so drop the `project_mode != "pm" and` prefix entirely from
  that cross-repo SDLC `GH_REPO` branch (lines 3082 and 3277 are the same shape — drop their prefixes
  too). The mode validator (`sdk_client.py:3160`) already normalizes any unrecognized mode to `"dev"`,
  so stripping `"mode": "pm"` from a vault projects.json is safe — the per-machine runbook step that
  removes it (see Update System) is the operational complement.
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
  - **Anchor the `:pm:`→`:eng:` rewrite positionally (NIT, Adversary):** the #652 precedent renames via
    an unanchored `key_str.replace(":chat:", ...)`. Cloned literally, `pm` is a shorter, more
    collision-prone substring. Replace **only the session_type key segment** (split the key on `:`,
    rewrite the segment, rejoin) and **assert exactly one `:pm:` occurrence before renaming** —
    `sys.exit(1)` if zero or >1, so a malformed key never gets a corrupted multi-replace. (Realistically
    no KeyField holds exactly `pm` outside the session_type segment, but the positional rewrite removes
    the risk entirely.)
  - **Worker-liveness guard via `data/last_worker_connected` mtime (BLOCKER B3; corrected by C2-B2;
    threshold pinned by CONCERN C8-2):** at startup, stat the worker heartbeat **file**
    `data/last_worker_connected` (written every health tick by `_write_worker_heartbeat`,
    `agent/session_health.py:2058`) and `sys.exit(1)` with a clear message if `(now - mtime) < threshold`
    (a fresh worker is live). **Pin `threshold = 600` seconds** with a code comment citing the source
    constant: the heartbeat is rewritten every `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300` seconds
    (`agent/session_health.py:206`, verified live), so the guard threshold must be **2 × the write
    interval = 600s** — anything under 300s false-passes a worker that was running moments ago (its last
    tick may be up to one full interval old even while alive), and a threshold ≥ 600s guarantees a single
    missed tick never reads as "stopped". The `pgrep -f "python -m worker"` / `os.kill(pid, 0)`
    cross-check below remains the **primary** liveness signal; the mtime guard is defense-in-depth.
    Resolve the path relative to the
    migration script's repo root (`Path(__file__).parent.parent / "data" / "last_worker_connected"`),
    **not** cwd. Do **NOT** key off `register_worker_pid` / `HEARTBEAT_FRESHNESS_WINDOW`: that key is
    `worker:registered_pid:{hostname}:{pid}` with a **24h TTL** and a **value of the PID, not a
    timestamp** (`session_health.py:2033-2048`, `WORKER_REGISTERED_PID_TTL_SECONDS`), so it cannot
    compute freshness and would false-block legitimate migrations for up to 24h after a clean worker
    stop; `HEARTBEAT_FRESHNESS_WINDOW`=90 governs a different per-session progress field. A
    `pgrep -f "python -m worker"` / `os.kill(pid, 0)` liveness check is a valid *additional* signal. Both
    migration scripts share this guard.
  - **Code-version ordering guard — assert `SessionType.PM` still exists (CONCERN C5-C2, Consistency):**
    the liveness guards (worker heartbeat, email-bridge pgrep) catch a *live writer* but **not** an
    aborted-then-retried wrong ordering where `/update` already ran and removed `SessionType.PM` from the
    installed code (BLOCKER B4's failure mode). After the liveness checks, the migrate script must
    `from config.enums import SessionType` and assert `hasattr(SessionType, "PM")`; if absent, `sys.exit(1)`
    with: *"Run this migration BEFORE /update — SessionType.PM has already been removed from the installed
    code; pm records can no longer be matched. See Update System runbook step 3."* This makes the
    migration-before-`/update` ordering self-enforcing instead of runbook-only. (The `merge_dev` script
    does not touch `session_type`, so this guard is the migrate script's alone.)
  - **Read `session_type` directly, NOT `session_mode` (CONCERN, Archaeologist):** the #652 precedent
    script branches on the deprecated `session_mode` field, a no-op since #1026. Header comment must
    state: *"Unlike #652, do NOT read session_mode — deprecated no-op since #1026; read session_type
    directly."* Idempotency for non-target records: `if ":dev:" in key_str:
    stats["skipped_dev_record"] += 1; continue` — `dev` deletion is code-side (Task 3), not a Redis
    rename, so dev keys are left untouched here.

**Critical-path scope of Task 4 is `migrate_session_type_pm_to_eng.py` only.** It is the sole migration
the per-machine rollout (and every downstream task dependency) hangs off — the `pm→eng` session rename is
mandatory wherever `pm` records exist. The chat-history merge below is operator-optional (No-Gos
[OPERATOR-DECISION]; archiving already preserves Dev history in-place) and is therefore broken out into
its own sub-section so it carries no critical-path weight. The migration-builder still authors **both**
scripts (authoring `merge_dev_chat_into_eng.py` remains a Success Criterion); only its *position on the
critical path* changes.

### 4b. Optional operator tool: merge Dev chat history (CONCERN C5-C3, Simplifier)
- **Task ID**: build-migrations (same builder/task; documented as a sub-deliverable, not a separate
  scheduling node — nothing depends on it)
- **Criticality**: operator-optional — **running** it is a per-project decision (No-Gos
  [OPERATOR-DECISION]); **authoring** it is required (Success Criteria). No downstream task depends on it.
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
  - **`TelegramMessage.rebuild_indexes()` is mandatory after the renames (BLOCKER C2-B1):** the raw
    `RENAME` moves only the data-hash key — it does **not** move the message into the `chat_id` KeyField
    index or, critically, into the `timestamp` SortedField partition (`models/telegram.py:29`
    `timestamp = SortedField(type=float, partition_by="chat_id")`). All reads go through
    `TelegramMessage.query.filter(chat_id=...)` (`tools/telegram_history/__init__.py:457,516,560,1151`),
    so without an index rebuild `filter(chat_id="<eng_id>")` returns only the *pre-existing* Eng messages
    and the merged Dev history is invisible to `valor-telegram read` — Success Criterion line ~536 silently
    fails. **Call `TelegramMessage.rebuild_indexes()` as the final step of the script** (every other
    `rebuild_indexes()` in this plan targets `AgentSession`; this one is `TelegramMessage`). Verify the
    rebuild repartitions the `chat_id`-partitioned SortedField, not just the KeyField index — out-of-order
    timeline display also depends on the sorted-set partition being correct.
  - **Pre/post count assertion via the ORM query path, NOT raw key counts (CONCERN, Operator + BLOCKER
    C2-B1):** capturing raw *key* counts passes even while the field/sorted-set indexes are stale (a
    `RENAME` updates the key but not the index), so a raw-key assertion gives false confidence. Capture
    the pre-run Dev-chat message count via `TelegramMessage.query.filter(chat_id="<dev_id>").count()`;
    after the renames **and after `rebuild_indexes()`**, assert
    `TelegramMessage.query.filter(chat_id="<eng_id>").count()` equals (pre-existing Eng count + migrated
    Dev count − skipped collisions); mismatch → `sys.exit(1)` prompting re-run. This validates the actual
    read path operators rely on. Idempotency guard: skip any key already bearing the target Eng `chat_id`
    segment (mirror the precedent's `skipped_already_migrated`). This gives a mid-scan kill a safe resume.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-routing, build-removal, build-migrations
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Execute all Documentation-section tasks (architecture rewrite, parallel-execution removal, ownership
  examples, persona docs, `projects.example.json`, `CLAUDE.md`).

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-routing, build-removal, build-migrations, document-feature
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
| No `pm` session value | `grep -rn 'SessionType.PM\b' config/ agent/ bridge/ tools/ ui/ models/ scripts/ tests/ --include="*.py"` | exit code 1 (CONCERN C4-C2: scope widened from `config/enums.py` to match the `dev` row and the "no value remains anywhere" criterion; **`models/` + `scripts/` added in cycle 6 — BLOCKER C6-B1**) |
| No bare `session_type` pm/dev literal (codebase-wide, BLOCKER C5-B1) | `grep -rnE 'session_type\s*=\s*["'"'"']?(pm\|dev)["'"'"']' agent/ bridge/ tools/ scripts/ models/ --include="*.py" \| grep -vE '#\|::\|→\|"session_type='` | no matches (exit 1). This is the mechanical enforcement of "no pm/dev value remains anywhere" for the bare-string sites the `SessionType.PM`/`SessionType.DEV` rows can't see. The pattern is keyed on `session_type` (so a chance `"pm"` elsewhere is ignored) and catches assignment + filter-kwarg sites (`reflection_scheduler.py:571`, `sustainability.py:610`, `sdk_client.py:2408`, `sdlc_stage_marker.py:99`, `sdlc_session_ensure.py:139,188`, `_sdlc_utils.py:97`); the `grep -v` strips comment (`#`), docstring (`::`), arrow-prose (`→`), and log-f-string (`"session_type=`) false positives surfaced during verification. **`models/` added in cycle 6 (BLOCKER C6-B1)** — `agent_session.py` carries `session_type=SESSION_TYPE_*` alias references (caught by the `SESSION_TYPE_PM`/`SESSION_TYPE_DEV` model row above), not bare `"pm"/"dev"` literals, but the scope is widened so a future literal cannot slip in. **The `== "pm"`/`!= "pm"`/`== "dev"` comparison gates (`sdlc_session_ensure.py:83`, `_sdlc_utils.py:87,162`, `session_executor.py:652/781/828/855/885/910`) are not caught by this `=`-anchored pattern** — they are covered by their own Task-3 rename bullets, and the `grep -rnE 'session_type[^=]*(==\|!=) *"(pm\|dev)"'` comparison row below is the complementary spot-check (also exit 1). |
| No `dev` session value | `grep -rn 'SessionType.DEV\b' config/ agent/ bridge/ tools/ ui/ models/ scripts/ tests/` | exit code 1 (**`models/` + `scripts/` added in cycle 6 — BLOCKER C6-B1**) |
| No deleted `PersonaType` members (BLOCKER C7-B2) | `grep -rn 'PersonaType.PROJECT_MANAGER\|PersonaType.DEVELOPER' config/ agent/ bridge/ tools/ ui/ models/ scripts/ tests/ --include="*.py"` | exit code 1 (catches the `ui/data/machine.py` runtime `persona_order` dict the prior Verification rows missed — they checked only `SessionType` + `session_type` literals, never `PersonaType` member refs; verified live consumers: `sdk_client.py`, `routing.py`, `telegram_bridge.py`, `ui/data/machine.py`, plus tests) |
| No `AccessLevel.PM_READONLY` (BLOCKER C7-B3) | `grep -rn 'PM_READONLY\|load_pm_system_prompt' config/ agent/ bridge/ tools/ ui/ models/ scripts/ tests/ --include="*.py"` | exit code 1 (member deleted, `load_pm_system_prompt`→`load_eng_system_prompt`; the work-vault `CLAUDE.md` layer now rides the `(ENGINEER, WORKER)` branch) |
| No `SESSION_TYPE_PM`/`SESSION_TYPE_DEV`/`is_pm`/`is_dev` in model + scripts (BLOCKER C6-B1) | `grep -rnE 'SESSION_TYPE_PM\b\|SESSION_TYPE_DEV\b\|\bis_pm\b\|\bis_dev\b' models/agent_session.py scripts/steer_child.py` | exit code 1 (the renamed/removed `agent_session.py` aliases + properties and the `steer_child.py` `is_dev` consumer leave no trace) |
| No `Dev:`/`PM:` fallback | `grep -rn 'startswith("Dev:")\|startswith("PM:")' bridge/routing.py` | exit code 1 |
| No `"Dev: Valor"` delivery target (BLOCKER C7-B4) | `grep -rn '"Dev: Valor"\|'"'"'Dev: Valor'"'"'' reflections/ scripts/ agent/ --include="*.py"` | exit code 1 (all eight background-job send-targets renamed to `Eng: Valor`; `tests/` deliberately out of scope — routing fixtures, handled by Task 2) |
| sdlc-decompose removed | `grep -n 'sdlc-decompose\|sdlc_decompose' pyproject.toml` | exit code 1 |
| No `--role dev`/`--role pm` in skills (CONCERN C8-3) | `grep -rn '\-\-role dev\|\-\-role pm' .claude/` | exit code 1 (the three skill files `skills-global/sdlc/SKILL.md:228`, `skills/x-com/SKILL.md:45`, `skills/linkedin/SKILL.md:85` all updated to `--role eng`; the global one ships to every machine via `scripts/update/hardlinks.py`) |
| GRANITE retained | `grep -n 'GRANITE' config/enums.py` | output contains GRANITE |
| Migration dry-run runs | `python scripts/migrate_session_type_pm_to_eng.py --dry-run` | exit code 0 |
| No `project_mode == "pm"` guard | `grep -n 'project_mode == "pm"\|project_mode != "pm"' agent/sdk_client.py` | exit code 1 |
| Migration refuses live worker | (manual) start worker, run migration | `sys.exit(1)` with fresh-heartbeat error |
| Merge dry-run reports collisions | `python scripts/merge_dev_chat_into_eng.py --dry-run --project test-x` | exit 0; collisions enumerated, no rename performed |
| No `== "pm"`/`!= "pm"`/`== "dev"` session_type comparison (C5-B1 / C6-B2 complement) | `grep -rnE 'session_type[^=]*(==\|!=) *"(pm\|dev)"' agent/ bridge/ tools/ scripts/ models/ --include="*.py" \| grep -vE '#\|::'` | no matches (exit 1). Catches the comparison gates the `=`-anchored gate misses: `sdlc_session_ensure.py:83`, `_sdlc_utils.py:87,162`, **the three additional live readers `session_pickup.py:119`, `sdlc_stage_query.py:66`, `stage_states_helpers.py:99`**, **and the six `agent/session_executor.py` setup-path `== "dev"` guards (652/781/828/855/885/910) renamed to `"eng"` in Task 3 (BLOCKER C6-B2)** — all renamed in Task 3. The `output_router.py:159` `== "pm"` is flipped by C4-B1; the single `session_executor.py:1914` `== "dev"` gate vanishes with the `_handle_dev_session_completion` deletion (Task 3), so it is correctly absent here. Scope now includes **`models/`** (`agent_session.py` carries no `== "pm"/"dev"` comparison — its checks use the `SESSION_TYPE_*` aliases / `SessionType.PM` enum refs, caught by the enum-name rows — but `models/` is added for completeness so a future literal cannot slip in). The `grep -v` drops the comment/docstring hits (`session_executor.py:770,1906,1912`, `sdlc_session_ensure.py:170`). |
| Reply-to steering routes to existing session (C5-C4) | (Cowboy pilot, manual) reply-to a running `Eng:` session; `python -m tools.valor_session status --id <ID>` | pending steering message present on the existing session; no new `eng` session created |
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

**Verdict:** NEEDS REVISION (4 blockers) → **REVISION APPLIED 2026-06-12.**

This cycle critiqued the **revised** plan (commit 054af5ed). The five cycle-1 blockers were
verified individually: **B1 (re-export), B4 (migration-before-/update), B5 (RENAME EXISTS-check)
hold.** Two cycle-1 resolutions were found **incomplete**, and three **new** blockers surfaced
(all independently verified against the live codebase before recording).

**Revision applied:** all 4 cycle-2 blockers + the 1 concern + 2 nits are folded into the body and
re-verified against live code (worker heartbeat file at `session_health.py:2058`; `register_worker_pid`
value=PID+24h-TTL at `:2033-2048`; email PM write at `email_bridge.py:879`; teammate DM-only at
`projects.example.json:7,47`; three `== "pm"` + four `!= "pm"` at 1173/2119/3164 + 3082/3277/3605/3693,
3647 a comment; `TelegramMessage.timestamp` SortedField `partition_by="chat_id"` at `models/telegram.py:29`).
The "Addressed By" column below records each disposition.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist | `merge_dev_chat_into_eng.py` (Task 4) clones the #652 raw-`RENAME` pattern onto `TelegramMessage` but never calls `TelegramMessage.rebuild_indexes()`. Every `rebuild_indexes()` in the plan targets `AgentSession`. Reads go through `TelegramMessage.query.filter(chat_id=...)` (`tools/telegram_history/__init__.py:457,516,560,1151`); a raw `RENAME` of the data-hash key does NOT move the message into the Eng `chat_id` field index or the `timestamp` SortedField partition. Result: after merge, `filter(chat_id="<eng_id>")` returns only pre-existing Eng messages, so the merged-history Success Criterion silently fails. | **FIXED — Task 4** (merge-script bullets): `TelegramMessage.rebuild_indexes()` added as the mandatory final step; pre/post count assertion rewritten to read back through `TelegramMessage.query.filter(chat_id=...).count()` after the rebuild, not raw keys. | Verified against `models/telegram.py:29` (`timestamp = SortedField(type=float, partition_by="chat_id")`). The raw-key count passes while the partitioned SortedField + chat_id KeyField index are stale; the ORM readback validates the actual `valor-telegram read` path, and the rebuild repartitions the sorted-set (out-of-order timeline display also depends on it). |
| BLOCKER | Operator, Adversary | The worker-heartbeat guard (B3 defense-in-depth) is specified against a non-existent key shape. Plan said scripts read "the worker heartbeat key (`register_worker_pid`/`HEARTBEAT_FRESHNESS_WINDOW`)" and `sys.exit(1)` on a *fresh* heartbeat. But `register_worker_pid` writes `worker:registered_pid:{hostname}:{pid}` with a **24h TTL** whose **value is the PID, not a timestamp**; `HEARTBEAT_FRESHNESS_WINDOW`=90 governs a different per-session field. As written the guard cannot compute freshness, or false-blocks legitimate migrations for up to 24h. | **FIXED — Task 4 guard bullet + Race 1 + Update System step 3**: guard rewritten to stat `data/last_worker_connected` mtime and `sys.exit(1)` if `(now - mtime) < threshold`; `register_worker_pid`/`HEARTBEAT_FRESHNESS_WINDOW` framing dropped everywhere. | Verified file write at `agent/session_health.py:2058` (rewritten every health tick) and the PID-value/24h-TTL key at `:2033-2048`. Path resolved via `Path(__file__).parent.parent / "data" / "last_worker_connected"` (repo root, not cwd); `pgrep -f "python -m worker"` / `os.kill(pid,0)` retained as an additional signal. |
| BLOCKER | Adversary | The **email bridge** (`python -m bridge.email_bridge`) is a *third* independent process (started/stopped via `email-start`/`email-stop`) that enqueues `session_type=SessionType.PM` AgentSessions (`bridge/email_bridge.py:879`) whenever inbound email resolves a non-teammate persona. The plan's concurrent-writer analysis (Race 1, Prerequisites, runbook step 1) named only the Telegram bridge and the worker. An email arriving during the `pm→eng` rename writes a fresh `pm` record mid-loop — the worker guard cannot catch it. | **FIXED — Prerequisites (new row) + Race 1 + Update System steps 1/3/6**: "stop the email bridge (`email-stop`)" added as a hard prerequisite, Race 1 third-writer, and runbook step; migration guard also asserts no live `bridge.email_bridge` (`pgrep` / fresh `email:last_poll_ts`). | Verified PM write at `bridge/email_bridge.py:879` and `email-stop`/`email-status` in `scripts/valor-service.sh`. **Corrected the critique's key name:** the email bridge writes `email:last_poll_ts` (`email_bridge.py:56`), not `email:relay:last_poll_ts` — the plan uses the verified key. |
| BLOCKER | User | The cycle-1 conversational-path resolution claims the lightweight "ask a quick question" surface "moves to the existing `Teammate: {Project}` group", but (a) group teammate routing is @mention-gated (un-mentioned → silent storage, no response) and (b) no `Teammate: {Project}` *group* exists today (teammate is DM-only via `dm_persona`). After the `PM:`→`Eng:` rename there is no quick-question surface unless this gap is closed. | **FIXED — Data Flow (conversational section rewritten) + Documentation**: decided resolution = the **`Eng: {Project}` group itself handles conversational messages too** (responds to direct messages for both questions and work via the single granite container); quick DM questions stay on the existing `dm_persona: "teammate"` path. No `Teammate:` group is invented. CLAUDE.md + the renamed architecture doc document the corrected contract. | Verified `config/projects.example.json:7` ("no prefix = teammate (mention-only)") and `:47` (`dm_persona: "teammate"`, DM-only). The cycle-1 claim was false on both counts; rather than add a per-project `Teammate:` group the runbook never listed, the Eng group is the single responsive per-project surface. |
| CONCERN | Skeptic, Consistency | The B2 resolution undercounts the `project_mode == "pm"` surgery: plan said "four `== "pm"` guards at 1173, 2119, 3164, **3647**", but reality is **three** `== "pm"` (1173, 2119, 3164; 3647 is a *comment*) and **four** `!= "pm"` complements (3082, 3277, 3605, **3693** — 3693 never listed). The line-757 grep is count-agnostic and catches a leftover 3693 (hence CONCERN not BLOCKER) but the guidance is wrong. | **FIXED — Task 3 (B2 bullet), Solution, Risk 6**: corrected to "three `== "pm"` guards (1173, 2119, 3164) + four `!= "pm"` complements (3082, 3277, 3605, 3693); 3647 is a comment, not a guard"; "four" dropped as a descriptor of the `==` guards. | Verified by `grep` against `agent/sdk_client.py`: `== "pm"` at 1173/2119/3164, `!= "pm"` at 3082/3277/3605/3693, comment at 3647. At 3693 (and the matching 3082/3277) the `!= "pm" and` prefix collapses to always-true once `mode=pm` is stripped, so Task 3 drops the prefix from the cross-repo SDLC `GH_REPO` branches. |
| NIT | Skeptic | Verification "No `dev` session value" check greps `config/ agent/ bridge/ tools/ ui/` but not `tests/`, while Success Criteria asserts no `pm`/`dev` value remains "anywhere ... including ... tests." A lingering `SessionType.DEV` in a test file passes this gate. | **FIXED — Verification table**: `tests/` added to the "No `dev` session value" grep scope. | — |
| NIT | Adversary | The #652 precedent renames via unanchored `key_str.replace(":chat:", ...)`. Cloned literally for `:pm:`→`:eng:`, `pm` is a shorter, more collision-prone substring. | **FIXED — Task 4 (migrate-script bullet)**: rewrite only the session_type key segment positionally (split on `:`, rewrite, rejoin) and assert exactly one `:pm:` occurrence before renaming (`sys.exit(1)` on zero or >1). | Realistically no KeyField holds exactly `pm` outside the session_type segment, but the positional rewrite + single-occurrence assertion removes the risk entirely. |

**Cap reasoning:** Redis `critique_cycle_count` reads 0 due to the post-data-loss rebuild, but this is
genuinely cycle 2 (the durable record is this table). Two consecutive NEEDS REVISION verdicts do not
trip a cap here; the new blockers were concrete, verified, and each carried a ready-to-apply
Implementation Note, so a targeted revision pass was the correct next step (not MAJOR REWORK — the
architecture and approach are sound; these were migration-correctness and rollout-completeness gaps).
**All 4 blockers + 1 concern + 2 nits are now resolved in the body; plan status → Ready.**

### Cycle 3 — revision pass 2026-06-12

**Verdict carried:** the recorded `NEEDS REVISION` (2026-06-12T07:28:31Z, artifact
`sha256:77055ced…`) is the **cycle-2** critique verdict; it was substantively addressed in commit
8c52d591 (the cycle-2 table above). This cycle-3 pass re-verified every load-bearing file:line
claim against live `main` and fixed the **one residual factual error** the cycle-2 revision left in
place — the rest of the plan's claims verified accurate.

| Severity | Finding | Fix |
|----------|---------|-----|
| NIT (accuracy) | The plan named the bridge prefix-check helper `_is_team_chat` (4 sites) at "line 331". The live function is **`is_team_chat`** (public, no leading underscore) at **`bridge/routing.py:327`** — the critique source bundle flagged the name explicitly (`NOTE: public name is is_team_chat, NOT _is_team_chat`). | **FIXED** — all four references corrected to `is_team_chat`; the Freshness Check line:number corrected to 327. |

**Re-verified accurate against live `main` (no change needed):** `project_mode == "pm"` guards at
1173/2119/3164 and `!= "pm"` complements at 3082/3277/3605/3693 with 3647 a comment (Task 3/Risk 6);
`agent/agent_session_queue.py` re-export block (B1); `bridge/email_bridge.py:879` PM write (C2-B3);
`config/enums.py` `SessionType` `{PM,TEAMMATE,DEV,GRANITE}` + `PersonaType`
`{DEVELOPER,PROJECT_MANAGER,…}` pre-rename shape. No blockers or concerns remain; plan stays Ready.

### Cycle 4 — war room re-run 2026-06-12

**Verdict:** NEEDS REVISION (2 blockers, 4 concerns, 1 nit) → **REVISION APPLIED 2026-06-12.**

This cycle surfaced two new blockers (hardcoded `"pm"` literals the prior cycles' enum-grep never
scanned) plus four concerns and a structural path fix. All folded into the body and re-verified against
live `main`. Two cited line numbers had **drifted** and were corrected during this revision (see notes).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | — | Hardcoded `session_type == "pm"` delivery branch — critique cited `agent/output_router.py:636`; the literal is actually at **line 159** in `determine_delivery_action` (def L79). Bare string, not `SessionType.PM`; after rename eng sessions get `deliver` instead of `nudge_continue`, silently breaking pipeline auto-continue. Not caught by the existing `"pm"`-in-`config/enums.py` grep. | **FIXED — Task 3 (new bullet + audit), Solution dev-machinery bullet, Verification (new `grep '"pm"' output_router.py` row), Test Impact (`test_output_router.py` nudge-path UPDATE)**: flip `"pm"` → `"eng"` at line 159. | **Line drift corrected:** critique said 636, verified 159 against live `main`. |
| BLOCKER | — | Task 3 pre-removal grep scoped to `agent/` only — misses `tools/agent_session_scheduler.py:417-419` (`from agent.agent_session_queue import _transition_parent`, a KEEPER; hazard is breaking the `:49-53` re-export block → scheduler ImportError at startup) and `tests/unit/test_continuation_pm.py` + `tests/integration/test_continuation_pm_handoff.py` (import `_create_continuation_pm` directly). | **FIXED — Task 3 (audit grep expanded to `agent/ tools/ tests/`; scheduler added to must-not-break caller list; re-export survival note), Test Impact (two continuation-pm test files DELETE/REPLACE)**. | Verified `tools/agent_session_scheduler.py:417-419` import + call against live `main`. |
| CONCERN | — | `pm_briefing.target_groups` still points at `PM:` group names — critique cited `config/projects.example.json:443`; the field is actually at **line 72** (`["PM: My Project"]`). Runbook step 4 never updated it → briefings target a dead group. | **FIXED — Update System runbook step 4 (update `target_groups`), Documentation Config-surfaces task, Success Criteria (pilot pm_briefing verification)**. | **Line drift corrected:** critique said 443, verified 72 against live `main`. |
| CONCERN | — | Verification grep asymmetry — `pm` scanned 1 dir, `dev` scanned 6. | **FIXED — Verification table**: "No `pm` session value" grep widened from `config/enums.py` to `config/ agent/ bridge/ tools/ ui/ tests/ --include="*.py"`, matching the `dev` row and the "no value remains anywhere" criterion. | — |
| CONCERN | — | No acceptance test for conversational-question behavior on `Eng:`. | **FIXED — Success Criteria (Cowboy plain-question → conversational reply, no issue/PR) + Failure Path Test Strategy (`ClassificationType.QUESTION` must resolve to nudge/deliver, never spawn a child dev session; QUESTION fast-path survives B2 deletion)**. | **Line drift corrected:** critique cited the QUESTION fast-path near `sdk_client.py:3265`; verified at `:3166,3202` (classification block ~3164-3210) against live `main`. |
| CONCERN | — | Fold Task 5 (validate-linkage) into Task 2; delete Task 5 + the `linkage-validator` team member; Task 7's dependency drops `validate-linkage`; roster shrinks to 5. | **FIXED — Task 2 Validates line + new parent-linkage bullet; former Task 5 deleted; Tasks 6/7 renumbered to 5/6; `validate-linkage` dep dropped from final-validation; `linkage-validator` team member removed (roster now 5)**. | — |
| NIT | — | Team roster right-sizes to 5 after the Task 5 fold; no standalone action. | **Subsumed by the CONCERN above** (roster trimmed to 5). | — |
| STRUCTURAL | — | Task 1 said `manifest.json` under `config/personas/` — correct path is `config/personas/segments/manifest.json`. | **FIXED — Task 1 + Solution persona-merge bullet**: path corrected to `config/personas/segments/manifest.json`. | Verified `config/personas/segments/manifest.json` exists; no `config/personas/manifest.json`. |

**All 2 blockers + 4 concerns + 1 nit + structural fix resolved in the body; plan status → Ready.**

### Cycle 5 — war room re-run 2026-06-12

**Verdict:** NEEDS REVISION (1 blocker, 4 actionable concerns, 1 nit) → **REVISION APPLIED 2026-06-12.**
This revision addresses the cycle-5 blocker and all concerns.

The single blocker is the cycle-4 `output_router.py:159` bug class at scale: **six bare-string
`session_type="pm"/"dev"` literal sites** outside the enum-grep's reach (flagged by 6 of 7 critics).
Every cited site was re-verified against live `main`; **two cited line-number/disposition corrections
were found** (see notes). All fixes folded into the body.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | 6 of 7 | Six bare-string `session_type="pm"/"dev"` literal sites invisible to the `SessionType.PM`/`.DEV` greps: writers `reflection_scheduler.py:571` (`"pm"`), `cold_start_metrics.py:37` (`"pm"`), `sustainability.py:610` (`"dev"`); readers `sdlc_stage_marker.py:99`, `sdlc_session_ensure.py:139/188`, `_sdlc_utils.py:97` (all `"pm"`). Post-rename: writers re-mint wrong-typed sessions forever, readers silently return zero. | **FIXED — Task 3 (new bare-literal bullet block with per-site disposition), Solution dev-machinery bullet, Verification (single-file `"pm"` row replaced by a codebase-wide `session_type` bare-literal gate + a `== "pm"` comparison spot-check), Success Criteria (grep gate named as the mechanical enforcement), Test Impact (new row for the affected tests), Update System (post-restart steady-state canary — CONCERN C5-C1).** | **Two corrections vs the critique.** (1) `cold_start_metrics.py:37` is **a docstring `Usage::` example, not code** — the real metric-label source is `agent/sdk_client.py:2408` `_session_type_tag = "pm" if system_prompt else "other"` (a JSONL metric label, not a session create); changed to `"eng"` per "match what the worker queries". (2) The three `tools/sdlc_*` reader files are **NOT deleted by Task 3's fan-out removal** (which deletes only `tools/sdlc_decompose.py`) — they belong to the live `sdlc-tool` family, so they are **renamed**. Also surfaced un-cited sibling literals in the same files (`sdlc_session_ensure.py:83`, `_sdlc_utils.py:87,162` `== "pm"` gates) and added them to the rename list. |
| CONCERN | Operator | Runbook verifies message delivery but not that wrong-typed records *stopped being minted* post-restart. | **FIXED — Update System runbook step 7 (new)**: post-reflection-cycle ORM-only canary `[s for s in AgentSession.query.all() if s.session_type in ('pm','dev')]` → exit 1 if any; documented as the break-glass canary (worker-disable + investigate if pm/dev reappear). | Never raw Redis; ORM-only per repo policy. |
| CONCERN | Consistency | Liveness guards don't catch an aborted-then-retried wrong ordering where `/update` already removed `SessionType.PM`. | **FIXED — Task 4 migrate-script guard bullet (new)**: after the liveness checks, assert `hasattr(SessionType, "PM")`; `sys.exit(1)` with "Run this migration BEFORE /update — SessionType.PM has already been removed" if absent. Makes the migration-before-`/update` ordering self-enforcing. | Migrate script only (the merge script does not touch `session_type`). |
| CONCERN | Simplifier | `merge_dev_chat_into_eng.py` is operator-optional but carries first-class Task 4 weight. | **FIXED — Task 4 restructured into critical-path 4 (`migrate_session_type_pm_to_eng.py` only) + new `### 4b. Optional operator tool` sub-section** holding all merge-script bullets/guards/Test-Impact; downstream deps hang off the rename migration only. Migration-builder still authors both. | Authoring `merge_dev_chat_into_eng.py` stays a Success Criterion; only its critical-path position changes. |
| CONCERN | User | No steering acceptance test. | **FIXED — Success Criteria (new Cowboy-pilot reply-to-steering criterion) + Failure Path Test Strategy (new entry) + Verification (new manual row)**: reply-to a running `Eng:` session lands in `queued_steering_messages` on the existing session (no new eng session) and the reply returns to the `Eng:` group; proves thread-ID continuation survives the prefix rename. | CONCERN C5-C5 (Skeptic verification-gap) is subsumed by the blocker's grep fix — no separate edit. |
| NIT | User | Runbook had no "heads-up to other group members" step. | **FIXED — Update System runbook step 0 (new)**: post a brief notice before renaming if other humans are in the `PM:`/`Dev:` groups. | — |

**All 1 blocker + 4 concerns + 1 nit resolved in the body; plan status → Ready.**

### Cycle 6 — war room re-run 2026-06-12

**Verdict:** NEEDS REVISION (2 blockers, 1 concern) → **REVISION APPLIED 2026-06-12.**
This revision addresses the cycle-6 blockers and the concern.

Both blockers are the same bug class the prior cycles chased (sites invisible to the enum greps), now in
two files no prior cycle had added to any task or grep scope: **`models/agent_session.py`** (the core ORM
model, flagged by all 7 critics) and the **`agent/session_executor.py` `== "dev"` setup-path guards**
(prior cycles wrongly claimed they "vanish with the dev-completion deletion"). Every cited site was
re-verified against live `main`; **line-number/disposition corrections were found** (see notes). All fixes
folded into the body.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | all 7 | `models/agent_session.py` is in no task list and `models/` in no Verification grep scope. Module-level aliases `SESSION_TYPE_PM = SessionType.PM` (81) / `SESSION_TYPE_DEV = SessionType.DEV` (83) evaluate at import → `import models.agent_session` raises `AttributeError` the moment `config/enums.py` drops `PM`/`DEV` (worker/bridge/email-bridge/test-collection all crash). `worker_key` (def 472) PM branch (494) does stage-aware serialization while the dev fall-through (500-503) is slug-always → ENG falling to the dev path loses per-project serialization at main-checkout stages (#828 race). `is_pm` (1207)/`is_dev` (1217), factories (`create_pm` 1305, `create_local` default 1375, `create_child` 1430), docstrings (87-112, field comment 144), and `scripts/steer_child.py:94` (`child.is_dev`) all carry pm/dev. | **FIXED — Task 1 (new `models/agent_session.py` block with per-site dispositions: alias rename/delete, `worker_key` ENG branch inherits PM logic + dead dev fall-through deleted + `_PM_WORKTREE_STAGES`→`_ENG_WORKTREE_STAGES`, `is_pm`→`is_eng`/`is_dev` deleted, factory renames, docstrings); Task 3 (`scripts/steer_child.py:94`); Verification (`models/`+`scripts/` added to enum-name + bare-literal + comparison rows, new `SESSION_TYPE_PM/DEV/is_pm/is_dev` model+scripts row); Test Impact (6 test files: test_enums/test_agent_session_queue_session_type/test_context_propagation/test_agent_session/test_steer_child/test_steering, each UPDATE/REPLACE); Success Criteria (slugless-ENG-at-PLAN→project_key worker_key; `import models.agent_session` exits 0).** | **Factory line drift corrected:** cycle-6 critique cited 1322/1375/1430 as the factory `def` lines; verified the bodies carry those, but the `def`s are `create_pm`:1305, `create_local`:1369, `create_child`:1396, `create_dev`:1444. ENG inherits the PM `worker_key` branch verbatim (slug only at worktree-compatible stages, else project_key); the dev fall-through is deleted, not retained. |
| BLOCKER | — | `session_executor.py` `== "dev"` gates: prior cycles claimed `781,1914` "vanish with the dev-completion deletion" — **wrong.** Seven gates exist (652, 781, 828, 855, 885, 910, 1914), reached via alias vars (`_stype_pre` 649, `_stype_early` 826, `_stype` 883, `_session_type` for the completion gate) so invisible to the enum + `session_type=` greps. Only 1914 is in `_handle_dev_session_completion` (dies with deletion); 652 is the #887 slugless-rejection guard; 781/828/855/885/910 are #887/#1272/#1377 worktree-provisioning/contamination guards — all must become `== "eng"`. | **FIXED — Task 3 (replaced the "vanish" claim with an explicit seven-site enumeration + per-site disposition: six RENAME, one DELETED-WITH-FUNCTION; added a pre-removal `== "dev"` audit grep step); Verification (comparison spot-check row now explicitly lists the six renamed executor gates + `models/` scope, and notes 1914 is correctly absent); Failure Path Test Strategy (slugged ENG still gets worktree isolation; slugless ENG with null agent_session_id still rejected at setup — the #887 guard fires for "eng").** | Each gate verified in context against live `main`: 652=slugless-rejection (#1272), 781=synthetic-slug synthesis, 828=branch-trust, 855=worktree-create FATAL, 885=main-checkout guard, 910=#1377 branch-mismatch, 1914=dev-completion call. The `:770,1906,1912` hits are comments (dropped by the spot-check `grep -v`). |
| CONCERN | User | The Cowboy pilot tests that a question does NOT spawn work, but not the inverse (a real work request DOES spawn a container). | **FIXED — Success Criteria (new paired AC)**: a concrete work request ("fix the login bug") to `Eng: Valor` spawns a granite container — verify via `python -m tools.valor_session list` that an `eng` session was created and left pending/running, and a Telegram reply arrives. Guards against the conversational-first path swallowing real work requests. | — |

**All 2 blockers + 1 concern resolved in the body; plan status → Ready.**

### Cycle 7 — war room re-run 2026-06-12

**Verdict:** NEEDS REVISION (4 blockers, 1 nit) → **REVISION APPLIED 2026-06-12.**
This revision addresses the cycle-7 blockers and the nit.

All four blockers are the same bug class the prior cycles chased — `pm`/`dev`/`PM_READONLY` references in
files or constructs no prior cycle added to any task or grep scope: a `def`-time default param
(`bridge/dispatch.py`), a runtime dict literal keyed on deleted `PersonaType` members
(`ui/data/machine.py`), an undispositioned `AccessLevel` member with a load-bearing behavioral side
effect (`PM_READONLY`'s work-vault context layer), and eight hardcoded `"Dev: Valor"` delivery targets.
Every cited site was re-verified against live `main`; **line-number/disposition corrections and one
under-count were found** (see notes). All fixes folded into the body.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | — | `bridge/dispatch.py:87` default param `session_type: str = SessionType.PM` binds at import time; `bridge/telegram_bridge.py:107` imports `bridge.dispatch`, so the bridge `AttributeError`s at startup the instant Task 1 drops `SessionType.PM`. File was in no task list. | **FIXED — Task 2 (new `bridge/dispatch.py:87` bullet: default → `SessionType.ENG`); Task 2 Validates (dispatch import smoke); Test Impact (correction — see note).** | Verified live: line 87 is the sole `SessionType.` ref in the file. **Correction vs critique:** the critique asked to add `tests/unit/test_bridge_dispatch_contract.py` (claimed it "imports SessionType.PM at line 200") to Test Impact as UPDATE — but the file has **zero** `SessionType` references (it is an AST enqueue-then-dedup contract test; the line-200 handler passes no `session_type`). Recorded as **NO CHANGE** in Test Impact with the correction; the default-param change does not touch it. |
| BLOCKER | — | `ui/data/machine.py:52-57` `persona_order` dict literal keyed on `PersonaType.PROJECT_MANAGER`/`DEVELOPER` is inside a function body → `AttributeError` on first dashboard machine-page render, deferred past import smoke + likely past CI. No Verification grep covered `PersonaType` member refs. | **FIXED — Task 1 (new `ui/data/machine.py` bullet: rekey to `{ENGINEER:0, TEAMMATE:1}`; `scripts/capture_persona_baseline.py:9` docstring UPDATE); Verification (new `PersonaType.PROJECT_MANAGER\|DEVELOPER` grep row, exit 1).** | **Line drift corrected:** critique cited 50-57, verified dict at **52-57**. `capture_persona_baseline.py` reference is a **docstring (line 9)**, not a runtime cell — disposition is a doc-label UPDATE, no crash. |
| BLOCKER | — | `AccessLevel.PM_READONLY` had no disposition: after Task 2/3 remove every resolver returning it, the member + rails are unreachable dead code (NO LEGACY); and `sdk_client.py:1109-1116` appends the work-vault `CLAUDE.md` business-context layer ONLY under PM_READONLY, `session_executor.py:1658` gates `load_pm_system_prompt` the same way — eng (WORKER) sessions would silently lose the layer. | **FIXED — decided: re-gate, not silent-drop.** Task 1 (delete `AccessLevel.PM_READONLY` config/enums.py:67); Task 2 (fold the vault `CLAUDE.md` append INTO the WORKER branch of `compose_system_prompt`; re-gate `session_executor.py:1658` on WORKER; rename `load_pm_system_prompt`→`load_eng_system_prompt`; full PM_READONLY consumer audit — 1040/1044/1058/1068/1087-1091/1109-1116/1141/1153/1169/1174/1187/1243/3646); Solution Key Elements ("AccessLevel.PM_READONLY reconciled" bullet); Documentation (architecture-doc note); Verification (new `PM_READONLY\|load_pm_system_prompt` grep row); Test Impact (`test_compose_system_prompt.py` REPLACE for the `(ENGINEER, WORKER)` baseline). | Verified the recommended re-gate is **coherent with live code**: `engineer` resolves WORKER via `_access_level_for_persona`'s default `return AccessLevel.WORKER` (sdk_client.py:1186-1194); the WORKER branch (1098-1107) `return`s **before** the PM_READONLY block (1109-1116), so the fix must **fold** the vault append into the WORKER branch (not merely rename the gate) and make `working_directory` optional for WORKER. The documented-deletion alternative was **not** chosen — live code shows the vault layer is the only business-context injection path, so preserving it is correct. |
| BLOCKER | — | Hardcoded `"Dev: Valor"` delivery targets route background-job alerts into the archived group (nightly tests, Sentry triage, docs audits, SDLC progress, memory consolidation, hibernate/wake all go dark). Plan covered only `sustainability.py:603` as cosmetic. | **FIXED — Task 3 (new delivery-target rename block → `"Eng: Valor"`); Verification (new `"Dev: Valor"` grep over `reflections/ scripts/ agent/`, exit 1); Success Criteria (new Cowboy-pilot background-alert criterion).** | **Under-count corrected — eight sites, not six** (grep'd live across the whole repo): `reflections/docs_auditor.py:844`, `reflections/sentry_triage.py:428`, `reflections/sdlc_progress.py:210`, `scripts/memory_consolidation.py:340`, `scripts/nightly_regression_tests.py:25` (module `TELEGRAM_CHAT`), `agent/sustainability.py:239`, `:250`, `:603`. **Scope explicitly excludes `tests/`** — the many `"Dev: Valor"` test occurrences are persona-**routing** fixtures for the OLD `Dev:` routing being deleted by Task 2, not delivery targets; docs/CLAUDE.md occurrences are covered by the Documentation section. |
| NIT | — | Email-bridge stop asymmetry: runbook uses `worker-disable` (suppresses launchd respawn) for the worker but only transient `email-stop` for the email bridge; on machines with the opt-in email-bridge launchd plist, `KeepAlive=true` may respawn it mid-migration past the point-in-time pgrep guard. | **FIXED — Update System runbook step 1 (note: on plist-equipped machines, unload `com.valor.email-bridge` / use the disable variant before migrating) + step 6 (re-load/re-enable after).** | — |

**All 4 blockers + 1 nit resolved in the body; plan status → Ready.**

### Cycle 8 — war room re-run 2026-06-12

**Verdict:** READY TO BUILD (with concerns) — **0 blockers, 5 concerns** → **REVISION APPLIED 2026-06-12.**
This was a **clarity pass**: every concern is an Implementation Note embedded surgically into the existing
task/section text so the builder needs no re-investigation. No restructuring, no scope change. Two critics
returned **clean** — the **Simplifier** ("genuinely exhausted from a simplification standpoint" — no further
collapse available) and the **Archaeologist** (no historical-precedent findings). Every cited site was
re-verified against live `main`; the line numbers, the `session_health.py:206` interval constant (300s),
the three SKILL.md lines, the `models/agent_session.py` module-docstring lines (10/14), and all seven test
files' reference counts held, with one import-source correction recorded below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic (disputed; 3 critics dismissed as grep-gated) | Task 3 lists only the `agent_session_queue.py:49-53` re-export block, but four more `SessionType.PM` sites live in the same file: def-time defaults at **223/1085** and inline worker_key branches at **385/1146**. | **Task 3** — new four-site enumeration bullet under the re-export item. | Verified live: 223/1085 are import-time-binding `def` defaults (same crash class as `dispatch.py:87`), 385/1146 are `elif session_type == SessionType.PM:` worker_key branches with `# KEEP IN SYNC` comments. All → `SessionType.ENG`; post-edit `grep -n 'SessionType' agent/agent_session_queue.py` survivors = TEAMMATE + ENG only. Disputed finding resolved by explicit enumeration. |
| CONCERN | Operator | The migration worker-liveness guard `(now - mtime) < threshold` never pinned `threshold`; anything under 300s false-passes a recently-stopped worker. | **Task 4** — pin `threshold = 600` (2 × write interval) with a comment citing the constant. | Verified live: heartbeat rewritten every `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300`s (`session_health.py:206`). 600s = 2× guarantees a single missed tick never reads as "stopped". `pgrep` remains primary; mtime is defense-in-depth. |
| CONCERN | Operator | Three skill files invoke `valor-session create --role dev`, which the CLI starts rejecting after this plan ships. | **Documentation** (3 checkbox items) + **Verification** (new `grep -rn '--role dev\|--role pm' .claude/` row, exit 1). | Lines verified live: `skills-global/sdlc/SKILL.md:228` (HIGHEST — hardlinked to every machine via `scripts/update/hardlinks.py`), `skills/x-com/SKILL.md:45`, `skills/linkedin/SKILL.md:85`. All → `--role eng`. |
| CONCERN | Archaeologist-adjacent (grep-mechanics) | Task 1's docstring scope was "lines 87-112 and 144", but the **module-level docstring at lines 10 and 14** also carries `session_type="pm"`/`"dev"` prose that the bare-literal grep matches (4 hits: 10/14/93/99) and `grep -v` does NOT strip (no `#`/`::`), keeping the gate from reaching exit 1. | **Task 1** — extend the docstring bullet to lines 1-20 (module docstring) with the grep-mechanics rationale. | Verified live: line 10 = `PM session (session_type="pm")`, line 14 = `Dev session (session_type="dev")`. Both must be rewritten for the bare-literal Verification grep to exit 1. |
| CONCERN | User/Operator | Seven test files calling the renamed factory/property surface (`.create_pm`/`.is_pm`/`.is_dev`/`.create_dev`) were not in Test Impact; no pre-edit enumeration hint in Task 1. | **Test Impact** (seven new rows) + **Task 1** (pre-edit `grep -rn '\.create_pm\b\|\.is_pm\b\|...' tests/` hint). | Reference counts verified live by `grep -c`: nudge_loop **3**, queue_isolation **8**, session_lifecycle **9**, error_boundaries **5**, bridge_routing **11** (asserts at 133/161/176), pm_session_factory **17** (DELETE/REPLACE — all 4 assertions invert). **Correction:** `test_session_completion_dev_spawn_no_truncation.py` imports `_create_continuation_pm` from **`agent.session_completion`** (line 23, def at :277), NOT `agent.agent_session_queue` as the C4-B2 row's `test_continuation_pm.py` does — recorded in the new row. |

**All 5 concerns embedded as Implementation Notes in the body; no item deferred; plan status → Ready (was already Ready — this pass only sharpens builder guidance).**

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
