---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1708
last_comment_id:
revision_applied: true
---

# Granite Canned Fallback (pm_no_user_message) + Eng-vs-Teammate Persona on Catchup Re-ingest

## Problem

After the granite startup-hang fix (commit 3a3ff1ab), granite runs the full PM↔Dev
loop but can still ship a useless reply. A live end-to-end test surfaced **two
separate, confirmed defects** the startup hang had been masking.

**Observed reply to a customer** (Cyndra Dev Team, msg 626):
*"Your request was completed; a summary could not be generated."* — the container's
`OPERATOR_TERMINAL_MESSAGE` wrap-up fallback. It falsely claims completion and
answers nothing. Session `tg_cyndra_-1003794218389_623`, `exit_reason=pm_no_user_message`,
`reached max_turns=10 without a [/complete]`.

**Current behavior:**
- **Finding 1:** Every steady-state turn logs `PM transcript read empty; using unknown
  classification`. Each empty read → `_unknown_classification()` → `PM_COMPLIANCE_NUDGE`
  re-prompt → 10 turns burned → wrap-up guard ships the canned `OPERATOR_TERMINAL_MESSAGE`.
  The PM **is** writing assistant text to its JSONL (a real cyndra transcript has 42
  `"type":"assistant"` entries), yet `last_assistant_text(...)` returns empty.
- **Finding 2:** `Cyndra Dev Team` is `persona: teammate` in projects.json, but the
  catchup-re-ingested session resolved `engineer (source=prime-command)`. An eng PM↔Dev
  work-loop is the wrong machine for a conversational CS question and compounds Finding 1's
  churn.

**Desired outcome:**
- The container never reads the wrong/None transcript path silently; when it does, the
  log distinguishes *path-None* / *file-missing* / *no-new-entry* so the failing branch is
  observable. The two latent path bugs that can produce a wrong path are closed.
- Catchup and reconciler re-ingest paths resolve the chat's configured persona instead of
  defaulting to `engineer` — so a teammate chat runs as `teammate` on every ingest path.

## Freshness Check

**Baseline commit:** 3a3ff1ab
**Issue filed at:** 2026-06-16T04:38:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/granite_container/transcript_tailer.py:327-435` (`_text_bearing_assistant_texts`,
  `text_bearing_count`, `last_assistant_text`) — still present, logic matches issue claims.
- `agent/granite_container/container.py:284-297` (`_transcript_path`), `:300-318`
  (`_capture_pty_identity`), `:899-929` (steady-state baseline + read), `:1247-1267`
  (wrap-up guard + `OPERATOR_TERMINAL_MESSAGE`) — all present and current.
- `agent/granite_container/pty_pool.py:389-405` (`_needs_session_spawn`) — confirmed the
  predicate omits `spec.pm_session_id`/`spec.dev_session_id`.
- `bridge/catchup.py:235-246` and `bridge/reconciler.py:211-222` — confirmed both call
  `enqueue_agent_session_fn(...)` without `session_type`/`project_config`.
- `agent/agent_session_queue.py:1083` — confirmed `session_type: str = SessionType.ENG`.
- `bridge/telegram_bridge.py:2205-2209, 2394-2395` — live path resolves persona and forwards
  `session_type=` + `project_config=`.
- `agent/session_executor.py:1009` (`_session_type = getattr(...)`), `:1615-1626`
  (`_resolve_compose_args` call + "Resolved persona" log) and `agent/sdk_client.py:1099-1155`
  (`_resolve_compose_args` precedence) — all confirmed.

**Cited sibling issues/PRs re-checked:**
- commit 3a3ff1ab (namespaced prime-command sync fix) — landed; it is the current HEAD.

**Commits on main since issue was filed (touching referenced files):** None. HEAD is the
same commit (3a3ff1ab) the issue references; issue filed today.

**Active plans in `docs/plans/` overlapping this area:** None specific to granite transcript
path or catchup persona. (Keyword greps matched unrelated session-field/timestamp plans.)

**Notes:** Recon (two parallel investigation agents) overturned Finding 1's framing — see
Why Previous Fixes Failed / Spike Results. The three named suspects are verified correct
against real 2.1.178 transcripts and are NOT the live cause.

## Prior Art

- **Issue #827**: *PM sessions receive Teammate read-only restriction due to is_dm proxy
  (refactor artifact from PR #796)* — same **class** as Finding 2: a persona-resolution
  defect introduced when a refactor dropped a persona input. Confirms the pattern of persona
  resolution silently degrading when a call site omits the persona signal.
- **PR #1694** (Granite PTY: Persona-as-Priming Refactor) — introduced the
  persona-as-priming model whose `source=prime-command` resolution Finding 2 observes; it
  also last touched `_needs_session_spawn` (removed `pm_system_prompt`) without adding the
  session-id fields to the predicate — the latent Suspect-A bug.
- **PR #1612** (Granite PTY Container: Production Cutover + Bounded Slot Pool) — established
  the `PTYPool` slot model and the prewarmed-pair-vs-per-session-spawn distinction that the
  `_needs_session_spawn` predicate gates.
- No prior **fix** has targeted the "PM transcript read empty" churn or the catchup persona
  default — these are first fixes for both findings.

## Data Flow

**Finding 1 — transcript path:**
1. **Entry point:** `BridgeAdapter` builds a `PairSpawnSpec` and calls `PTYPool.acquire`.
2. **`pty_pool.py:_needs_session_spawn`** decides prewarmed-pair vs per-session spawn. If it
   returns True, `_spawn_session_pair` threads `pm_session_id`/`dev_session_id` into the PTY
   (claude gets `--session-id`); if False, the prewarmed pair (no session_id) is reused and
   claude auto-generates its own UUID.
3. **`container.py:_capture_pty_identity` → `_transcript_path(cwd, pty._session_id)`** computes
   `~/.claude/projects/{cwd.replace("/","-")}/{session_id}.jsonl`.
4. **Steady-state loop (`container.py:899-919`)** captures `text_bearing_count(pm_transcript)`
   as baseline, sends the nudge, then reads `last_assistant_text(pm_transcript,
   baseline_text_count=...)`. If `pm_transcript` is None / points at a non-existent or
   wrong-slug file, every read returns `""` → "PM transcript read empty".
5. **Output:** after 10 empty turns the wrap-up guard (`container.py:1247-1267`) ships
   `OPERATOR_TERMINAL_MESSAGE`.

**Finding 2 — persona resolution:**
1. **Entry point:** catchup scan (`bridge/catchup.py`) or reconciler scan
   (`bridge/reconciler.py`) finds an unprocessed message.
2. **`enqueue_agent_session_fn(...)`** is called **without** `session_type`/`project_config`
   → defaults to `SessionType.ENG` (`agent_session_queue.py:1083`).
3. **AgentSession persisted** with `session_type="eng"`.
4. **Worker** runs it: `session_executor.py:1009` reads `_session_type="eng"`;
   `_resolve_compose_args` (`sdk_client.py:1143-1144`) keys on session_type → `(ENGINEER, WORKER)`.
5. **Output:** logged `engineer (source=prime-command)`; an eng PM↔Dev loop runs for a
   teammate chat. (Contrast: live path at `telegram_bridge.py:2205-2209` resolves persona →
   `SessionType.TEAMMATE` and forwards it, so live is correct.)

## Why Previous Fixes Failed

This is not a re-fix of a prior attempt, but the **recon overturned the issue's own
hypotheses** for Finding 1, which is worth recording so the build does not chase the wrong layer.

| Hypothesis (from issue) | Status after recon | Why it is NOT the live cause |
|--------------------------|--------------------|-------------------------------|
| Transcript path mismatch (2.1.178 session-id) | Eliminated as live cause | `claude --session-id <uuid> -p` writes `<uuid>.jsonl`; PTY passes it at `pty_driver.py:383-384`; a live granite worktree transcript has inner `sessionId == filename`. |
| Baseline over-filtering | Eliminated | Baseline captured before `_cycle_idle` (`container.py:899` before `:902`); exact `_text_bearing_assistant_texts` logic extracts 11 text-bearing from 44 assistant entries on a real transcript. |
| 2.1.178 format/location change | Eliminated | Location still `~/.claude/projects/{slug}/{uuid}.jsonl`; assistant text still at `message.content[].type=="text"`. New UUID dirs are subagent sidecars. |

**Root cause pattern:** the live "empty read" is the Container reading a **None/missing/
wrong-slug** transcript path, conflated by a single log message that hides which of three
distinct failure modes fired. The correct first move is a **disambiguating diagnostic**, then
closing the two latent path bugs that can produce a wrong path. Jumping straight to "fix the
tailer" would not touch the real defect.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** `_needs_session_spawn` predicate gains two terms (additive, no
  signature change). Catchup/reconciler add two existing kwargs to an existing call.
- **Coupling:** Finding 2 fix increases catchup/reconciler coupling to `bridge.routing.resolve_persona`
  — but this is the same dependency the live handler already has, so it removes an asymmetry
  rather than adding net coupling.
- **Data ownership:** `session_type` is set at enqueue for the catchup and reconciler scanner
  paths fixed here (matching the live handler). The residual `SessionType.ENG` fallback at
  `agent_session_queue.py:1083` is a **separate, deliberate default** whose disposition is Open
  Question 2 — this plan keeps it (so intentional eng callers are unaffected) but adds a greppable
  WARNING when a caller omits both `session_type` and `project_config`, so the default's silence is
  no longer what hides a dropped persona signal.
- **Reversibility:** all changes are small, local, and trivially revertible.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the diagnostic disambiguation before hardening; confirm whether
  the live cyndra branch is path-None vs symlink-slug)
- Review rounds: 1

The fixes are small and surgical; the Medium sizing reflects that Finding 1 needs a
diagnostic-then-confirm loop (the live failing branch must be observed, not assumed) plus two
latent fixes, and Finding 2 spans two scanner call sites with shared semantics.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are to in-repo Python
modules and their tests.

## Solution

### Key Elements

- **Diagnostic disambiguation (Finding 1, lead change, lands FIRST):** Split the single
  `"PM transcript read empty"` log into three stably-named, greppable branches —
  `transcript read: path-None`, `transcript read: file-missing`, `transcript read: no-new-entry` —
  each WARNING also logging the resolved attempted path, `spec.pm_session_id`/`dev_session_id`
  presence, and `pty._session_id`. This is the highest-value change: it identifies which branch the
  live cyndra session actually hits without guessing, and distinguishes a spawn-threading gap from a
  slug mismatch. The two path fixes are gated on what this reveals (see Technical Approach).
- **`_needs_session_spawn` predicate fix (Finding 1, latent bug 1, gated on diagnostic):** Add
  `or spec.pm_session_id or spec.dev_session_id` so a spec carrying session-ids always forces
  a per-session spawn — even if some future session shape has an empty env. Closes the
  prewarmed-pair-reuse-without-session-id path that yields a None/auto-UUID transcript. Update the
  docstring to state the full per-session-identity invariant. (Production no-op today — masked
  because every real bridge spec already sets `env`/`pm_model`; ships as correct-regardless
  hardening.)
- **Symlink-resolved slug (Finding 1, latent bug 2, gated on diagnostic):** Apply
  `os.path.realpath(cwd)` (only when `cwd` is truthy) before `.replace("/","-")` in
  `_transcript_path` (after its `if not session_id: return None` guard) and the matching
  computation in `bridge_adapter.py`, so the slug matches claude's symlink-resolved slug for any
  symlink-crossing working directory. Two separate one-line inserts — NOT a merge of the two
  functions (they have divergent None-handling). (Production no-op today — no current project
  crosses a symlink; ships as correct-regardless hardening.)
- **Persona resolution (Finding 2) — shared helper:** Add `persona_to_session_type` to
  `bridge/routing.py`; call it from the live handler, `bridge/catchup.py`, and
  `bridge/reconciler.py`. Each scanner resolves `resolve_persona(project, chat_title)` →
  `persona_to_session_type(...)` and passes `session_type=` + `project_config=project` to
  `enqueue_agent_session_fn`, mirroring the live path. Wrapped in a narrow per-message try/except
  with a greppable WARNING on failure.
- **Greppable WARNING on silent ENG default (Finding 2, defense-in-depth):** at
  `agent_session_queue.py:1083`, warn (greppable) when both `session_type` and `project_config` are
  omitted; keep the default behavior.

### Flow

Catchup/reconciler scan → find unprocessed teammate-chat message → **resolve_persona(project,
chat_title)** → map to session_type → `enqueue_agent_session(session_type=…, project_config=project)`
→ worker resolves `teammate` → conversational reply (not eng PM↔Dev loop).

Container steady-state read → transcript path is **realpath-slugged** and **always session-id-bound**
→ `last_assistant_text` returns the PM's reply → classification proceeds → no canned fallback.
When a path is still None/missing → **WARNING names the exact branch**.

### Technical Approach

- **Diagnostic FIRST, fixes gated on what it reveals.** This is the critical sequencing point.
  Recon eliminated all three named suspects as the *live* cause and could not pin the live cyndra
  symptom to a specific branch — so the two path fixes are **admitted production no-ops today**
  (`_needs_session_spawn` is masked because every real bridge spec already sets `env`/`pm_model`
  so the predicate already returns True; the realpath slug changes nothing because no current
  project crosses a symlink). Therefore:
  1. **Land the log split first** (`container.py:922-929`, the prime-turn read at `:844-852`, and
     the wrap-up-guard read at `:1239-1247`), splitting `"PM transcript read empty"` into three
     **stably-named, greppable** branches:
     - `[granite-container] transcript read: path-None` — `pm_transcript` is None.
     - `[granite-container] transcript read: file-missing` — path is set but the file does not
       exist on disk (`os.path.exists()` is False).
     - `[granite-container] transcript read: no-new-entry` — file exists but
       `last_assistant_text(...)` returns empty (valid file, no new text-bearing entry past
       baseline).
     Each WARNING also logs the **fully-resolved attempted path string**, `spec.pm_session_id`/
     `spec.dev_session_id` presence, and `getattr(pty, "_session_id", None)` so an on-call can tell
     a spawn-threading gap (spec had IDs, pty did not) apart from a slug mismatch.
  2. **Observe the live firing branch** on a real failing cyndra run (re-run the repro, or read the
     next live cyndra container log) BEFORE declaring Finding 1 closed.
  3. **Fix only the branch that fires.** The predicate fix targets path-None; the realpath fix
     targets file-missing/wrong-slug. The `no-new-entry` branch has **no fix in this plan** — it is
     a distinct failure mode (the PM genuinely wrote nothing new past baseline) whose explicit exit
     condition is: *the wrap-up guard correctly ships `OPERATOR_TERMINAL_MESSAGE` only here*; if the
     live branch turns out to be `no-new-entry`, that is a **separate follow-up issue** with the
     now-disambiguated evidence, not a fix smuggled into this PR.
  Both latent path fixes still ship in this PR (they are correct-regardless hardening), but
  "Finding 1 closed" is **fail-closed**: it requires the diagnostic firing on a real failing run
  AND one fix demonstrably converting that run to a delivered PM reply (see Success Criteria).
- **`_needs_session_spawn`:** extend the existing `bool(...)` expression with the two session-id
  terms; no signature change. Update the docstring to state the **full invariant** — "returns True
  if the spec carries ANY per-session identity: env, model, cwd-override, OR session-id" — rather
  than a term-by-term list, since term-by-term editing is the churn that produced the latent bug.
- **Realpath slug:** apply `cwd = os.path.realpath(cwd)` **only when `cwd` is truthy** (raw
  `os.path.realpath("")` returns the process CWD, which would corrupt the slug), and keep
  `_transcript_path`'s `if not session_id: return None` guard **BEFORE** the realpath/slug so the
  path-None diagnostic branch is never regressed into a wrong path. Do **not** merge
  `_transcript_path` and the `bridge_adapter.py` slug into one function — they have divergent
  None-handling (`_transcript_path` returns None for falsy session_id; `bridge_adapter.py` has no
  None branch). The fix is a one-line `realpath` insert at each site, not a refactor; duplicating
  the single line is lower-risk than reconciling two signatures.
- **Persona resolution in scanners — EXTRACT a shared helper.** The TEAMMATE→`session_type`
  mapping currently lives inline at the live handler (`telegram_bridge.py:2206-2210`). Adding
  catchup and reconciler makes **three** sites — the live site counts toward the threshold, so
  the "extract if it appears in three places" rule fires. Extract a single helper
  `persona_to_session_type(persona: PersonaType) -> SessionType` in `bridge/routing.py` (next to
  `resolve_persona`), returning `SessionType.TEAMMATE` for `PersonaType.TEAMMATE` else
  `SessionType.ENG`. Refactor the live handler to call it, and call it from catchup + reconciler.
  This removes the asymmetry the bug exploited rather than duplicating the two-liner twice more.
  (The live handler's extra `logger.info` for the ENGINEER-config case stays at the call site —
  the helper returns only the type.)
- **Defense-in-depth — greppable WARNING on the silent ENG default.** The silent
  `SessionType.ENG` default at `agent_session_queue.py:1083` is what made the omission invisible.
  Keep the default (changing it would alter behavior for every intentional eng caller), but emit a
  greppable `logger.warning("[enqueue] session_type omitted AND project_config omitted; "
  "defaulting to eng — caller may have dropped persona resolution")` when a caller omits **both**
  `session_type` and `project_config`. This surfaces future omissions without changing behavior
  for intentional eng callers. (Disposition of changing the default itself remains Open Question 2,
  but the WARNING is non-optional and lands in this PR.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `transcript_tailer.py` functions are already fail-silent (`except OSError: return []`,
  broad `except Exception`). The diagnostic change is in `container.py` at the read site, NOT
  inside the tailer — assert the new WARNING fires (path-None / file-missing) via caplog rather
  than letting it be swallowed.
- [ ] Catchup/reconciler: the `enqueue_agent_session_fn` call is inside the scan loop. Wrap the
  persona resolution in a **narrow per-message try/except** (NOT the outer scan-level
  `except Exception → logger.error("Error scanning")`, which aborts the whole chat scan) so a single
  bad message degrades only itself. On failure, emit a stable greppable WARNING —
  `logger.warning("[catchup] persona resolution failed for chat %s (%s); defaulting to eng: %s", chat_id, chat_title, e)`
  (and the reconciler equivalent) — then fall back to the eng default. This is the exact
  silent-degradation pattern Prior Art #827 warns about; the WARNING makes the degradation visible
  instead of silent. Test that the WARNING fires and the scan continues.

### Empty/Invalid Input Handling
- [ ] `_transcript_path(cwd, None)` already returns None — add a test asserting the new
  diagnostic logs the *path-None* branch (not the generic message) when session_id is None.
- [ ] `resolve_persona(project=None, ...)` / missing project in catchup — test that a missing
  project dict falls back to eng without crashing the scan.
- [ ] Verify the wrap-up guard still ships `OPERATOR_TERMINAL_MESSAGE` only when the PM genuinely
  produced nothing — do not let the diagnostic change suppress the legitimate fallback.

### Error State Rendering
- [ ] The user-visible `OPERATOR_TERMINAL_MESSAGE` is the error rendering. Test that with the
  Finding-1 fix in place (correct path), a PM that DID reply delivers the PM's text, not the
  canned fallback.
- [ ] Test that the new WARNING messages propagate to the container log (caplog), not swallowed.

## Test Impact

- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: add cases asserting the
  three-way diagnostic log split (path-None / file-missing / no-new-entry) at the steady-state
  read site; assert correct-path PM reply is delivered (no canned fallback).
- [ ] `tests/unit/granite_container/test_pty_pool.py` and
  `tests/unit/granite_container/test_pty_pool_hardening.py` — UPDATE: add a case where a spec
  carries `pm_session_id`/`dev_session_id` but empty env → `_needs_session_spawn` returns True.
- [ ] `tests/unit/granite_container/test_transcript_tailer.py` — UPDATE (if needed): add a
  symlink-crossing cwd case asserting `_transcript_path` resolves to the realpath slug. (May
  live in `test_container.py` if that is where `_transcript_path` is exercised.)
- [ ] `tests/integration/test_catchup_revival.py` and
  `tests/integration/test_per_chat_catchup_cutoff.py` — UPDATE: assert a teammate-configured chat
  enqueues with `session_type=SessionType.TEAMMATE` and `project_config` set; assert an eng/default
  chat still enqueues eng. (These live in `tests/integration/`, NOT `tests/unit/` — verified.)
- [ ] `tests/unit/test_reconciler.py` and `tests/integration/test_reconciler.py` (both exist) —
  UPDATE: same teammate-vs-eng enqueue assertions for the reconciler path.
- [ ] `bridge/routing.py` test coverage (`tests/unit/test_routing.py` if present, else add) —
  UPDATE/ADD: assert `persona_to_session_type` maps TEAMMATE→TEAMMATE and ENGINEER/None→ENG.
- [ ] `tests/unit/test_agent_session_scheduler_persona.py` / `test_persona_loading.py` —
  REVIEW (likely no change): confirm they don't assert the old eng-default behavior for scanner
  paths; UPDATE only if they encode the bug.

## Rabbit Holes

- **Do NOT rewrite the tailer or baseline counting.** Recon proved both are correct against
  2.1.178. Touching them re-introduces risk for zero benefit.
- **Do NOT change the wrap-up guard's TRIGGER condition.** The fallback is correct *as a last
  resort*; the bug is that the loop reaches it spuriously. Fix the cause, not the trigger.
  *Permitted (low-risk, orthogonal):* reword the `OPERATOR_TERMINAL_MESSAGE` **string literal**
  (`container.py:217`) so it no longer falsely claims completion (e.g. "I wasn't able to produce a
  response to this — please rephrase or follow up.") — the misleading text is part of the user
  complaint. The trigger condition stays untouched.
- **Do NOT broaden the `SessionType.ENG` default change** at `agent_session_queue.py:1083`
  beyond adding the greppable WARNING — changing the default itself (Open Question 2) affects every
  direct caller and is out of scope unless review approves.
- **Do NOT chase a hypothetical claude 2.1.178 format change.** Confirmed unchanged. Resist
  re-investigating the schema.
- **Do NOT build a generic "persona resolution service" abstraction.** The shared helper is a
  single `persona_to_session_type(persona) -> SessionType` mapping function next to
  `resolve_persona` — nothing more. (The mapping triplicates across live + catchup + reconciler, so
  extraction is the resolved decision, not duplication.)

## Risks

### Risk 1: The live cyndra failure is a branch the diagnostic reveals to be neither path-None nor wrong-slug
**Impact:** The two latent fixes are correct but would not resolve the live symptom, leaving the
canned fallback in place for that session.
**Mitigation:** Land the diagnostic first and re-run the live repro (or inspect the next live
cyndra session's container log) to confirm the branch BEFORE declaring Finding 1 closed. The
diagnostic is the gating deliverable; the two fixes are correct-regardless hardening. If a third
branch appears, file a follow-up with the now-disambiguated evidence.

### Risk 2: resolve_persona import in scanners introduces a circular import or scan-loop crash
**Impact:** Catchup/reconciler could fail to enqueue, dropping messages.
**Mitigation:** `bridge.routing.resolve_persona` is already imported by `telegram_bridge.py`;
import locally inside the scan function if a module-level cycle appears. Wrap the resolution in a
try/except that falls back to the eng default with a logged warning, and test that fallback.

### Risk 3: A teammate session running a previously-eng-shaped catchup message hits write restrictions mid-task
**Impact:** A teammate session has restricted writes; if a real code task was (wrongly) enqueued
to a teammate chat, it would now correctly run as teammate and refuse source writes.
**Mitigation:** This is the *correct* behavior — the chat is configured teammate. The teammate
session already redirects source-write attempts to spawn an eng session (per
teammate-session-permissions). No additional mitigation needed; note it in docs.

## Race Conditions

No race conditions identified. The persona-resolution change is synchronous within the existing
scan loop (a single `enqueue_agent_session` call gains two pre-computed kwargs). The
`_needs_session_spawn` predicate is evaluated synchronously at acquire time under the existing
slot lock. The diagnostic log split is a read-then-log at a single point. None introduce new
shared mutable state or new ordering requirements beyond what already exists.

## No-Gos (Out of Scope)

Nothing deferred — both findings' root causes are fully in scope for this plan. The only
explicitly-bounded decision is whether to also harden the `SessionType.ENG` default at
`agent_session_queue.py:1083` (it affects unrelated direct callers); that decision is raised as
Open Question 2 and resolved in-plan during review, not deferred to a separate effort.

## Update System

No update system changes required — this is a bridge/worker-internal bug fix. No new
dependencies, config files, or migration steps. The fix propagates to every machine through the
normal `git pull` + service restart in `/update` (the bridge and worker are restarted by the
existing update flow).

## Agent Integration

No agent integration required — this is a bridge-internal change. The catchup and reconciler
scanners and the granite container run inside the bridge/worker process and are not invoked
through MCP tools or CLI entry points. No `.mcp.json` change, no new CLI script. The bridge
already imports the affected modules directly; integration is verified by the existing
catchup/reconciler/granite test suites (see Test Impact).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document the three-way transcript-read
  diagnostic (including that the WARNINGs land in `logs/worker.log` and the three greppable
  substrings `transcript read: path-None` / `file-missing` / `no-new-entry`), the
  `_needs_session_spawn` session-id requirement, and the realpath-slug rule.
- [ ] Update `docs/features/eng-session-architecture.md` (or `teammate-session-permissions.md`)
  — note that catchup and reconciler now resolve the chat's configured persona, matching the
  live path; a teammate chat runs teammate on every ingest path.

### External Documentation Site
- [ ] No external docs site in this repo — N/A.

### Inline Documentation
- [ ] Update the `_needs_session_spawn` docstring to mention the session-id terms.
- [ ] Add a comment at the catchup/reconciler enqueue sites explaining why persona is resolved
  here (parity with the live handler; prevents the eng-default regression).

## Success Criteria

**Finding 1 — FAIL-CLOSED. Finding 1 is NOT "closed" until BOTH of these hold:**
- [ ] (1a) The diagnostic fires on a **real failing run** (re-run the live cyndra repro, or the
  next live cyndra container log) and names the exact empty-read branch — `path-None` /
  `file-missing` / `no-new-entry` — no longer the conflated generic message. The WARNING line is
  greppable in the **worker log** (`logs/worker.log`, where the granite container's
  `logging.getLogger(__name__)` output lands) by the substrings
  `transcript read: path-None` / `transcript read: file-missing` / `transcript read: no-new-entry`.
- [ ] (1b) The fix targeting the observed branch **demonstrably converts that run to a delivered
  PM reply** — the `OPERATOR_TERMINAL_MESSAGE` canned fallback is NOT shipped for it. If the
  observed branch is `no-new-entry` (no fix in this plan), Finding 1 is **escalated to a follow-up
  issue** with the disambiguated evidence rather than marked closed; do not declare it fixed.

**Finding 1 — supporting (correct-regardless hardening, verified by unit tests):**
- [ ] `_needs_session_spawn` returns True for a spec carrying session-ids with empty env.
- [ ] `_transcript_path` produces a realpath-resolved slug for a symlink-crossing cwd, AND still
  returns None when `session_id` is falsy (None-guard precedes realpath).
- [ ] The three diagnostic branches are unit-tested via caplog (path-None / file-missing /
  no-new-entry each log their distinct substring).

**Finding 2:**
- [ ] A catchup-re-ingested message for a `teammate`-configured chat resolves persona
  `teammate` (not `engineer`); a default chat still resolves `eng`.
- [ ] The reconciler path resolves persona identically.
- [ ] `persona_to_session_type` exists in `bridge/routing.py` and is called from all three sites
  (live handler + catchup + reconciler).
- [ ] `enqueue_agent_session` emits a greppable WARNING when both `session_type` and
  `project_config` are omitted (unit-tested via caplog).
- [ ] grep confirms `bridge/catchup.py` and `bridge/reconciler.py` reference
  `persona_to_session_type` (or `resolve_persona`) and pass `session_type=` + `project_config=` to
  `enqueue_agent_session`.

**Both:**
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (granite-transcript)**
  - Name: granite-transcript-builder
  - Role: Finding 1 — diagnostic log split, `_needs_session_spawn` predicate, realpath slug
  - Agent Type: builder
  - Resume: true

- **Builder (persona-scanners)**
  - Name: persona-scanner-builder
  - Role: Finding 2 — catchup + reconciler persona resolution
  - Agent Type: builder
  - Resume: true

- **Validator (granite)**
  - Name: granite-validator
  - Role: Verify Finding 1 changes — diagnostic branches, predicate, slug; container tests pass
  - Agent Type: validator
  - Resume: true

- **Validator (persona)**
  - Name: persona-validator
  - Role: Verify Finding 2 — teammate-vs-eng enqueue on catchup + reconciler
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: granite-documentarian
  - Role: Update granite + eng/teammate session docs
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — see template. Primary: builder, validator, documentarian;
debugging-specialist available if the live diagnostic surfaces a third branch.)

## Step by Step Tasks

### 1. Finding 1 diagnostic + path fixes
- **Task ID**: build-granite-transcript
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_container.py`,
  `tests/unit/granite_container/test_pty_pool.py`,
  `tests/unit/granite_container/test_pty_pool_hardening.py`,
  `tests/unit/granite_container/test_transcript_tailer.py`
- **Informed By**: recon (path-None/missing/no-new-entry conflation; predicate omits session-ids;
  raw-slug-vs-realpath)
- **Assigned To**: granite-transcript-builder
- **Agent Type**: builder
- **Parallel**: true
- Split the `"PM transcript read empty"` log at the steady-state read (`container.py:922-929`),
  the prime-turn read (`:844-852`), and the wrap-up-guard read (`:1239-1247`) into three
  stably-named branches with greppable substrings `transcript read: path-None` /
  `transcript read: file-missing` / `transcript read: no-new-entry`; each WARNING logs the resolved
  attempted path, `spec.pm_session_id`/`dev_session_id` presence, and `pty._session_id`.
- Extend `_needs_session_spawn` (`pty_pool.py:389-405`) with
  `or spec.pm_session_id or spec.dev_session_id`; update its docstring to the full invariant.
- Apply `os.path.realpath(cwd)` (only when `cwd` truthy) before `.replace("/","-")` in
  `_transcript_path` (`container.py:284-297`, AFTER the `if not session_id: return None` guard) and
  the matching slug computation in `bridge_adapter.py:92-100`. Do not merge the two functions.
- Add/extend unit tests: three diagnostic branches via caplog; predicate True for session-id +
  empty env; realpath slug for symlink-crossing cwd; None-guard still precedes realpath.

### 2. Finding 2 persona resolution in scanners
- **Task ID**: build-persona-scanners
- **Depends On**: none
- **Validates**: `tests/integration/test_catchup_revival.py`,
  `tests/integration/test_per_chat_catchup_cutoff.py`, `tests/unit/test_reconciler.py`,
  `tests/integration/test_reconciler.py`
- **Informed By**: recon (catchup + reconciler omit session_type/project_config; live path at
  telegram_bridge.py:2205-2209,2394-2395 is the template)
- **Assigned To**: persona-scanner-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `persona_to_session_type(persona) -> SessionType` to `bridge/routing.py`; refactor the live
  handler (`telegram_bridge.py:2206-2210`) to call it (keeping its ENGINEER-config `logger.info` at
  the call site).
- In `bridge/catchup.py:235-246` and `bridge/reconciler.py:211-222`: call
  `resolve_persona(project, chat_title)` → `persona_to_session_type(...)`, and pass
  `session_type=` + `project_config=project`. Wrap the resolution in a **narrow per-message
  try/except** (not the outer scan-level catch) that falls back to the eng default and emits a
  greppable `logger.warning("[catchup] persona resolution failed ...")` / reconciler equivalent.
- Add a greppable `logger.warning` at `agent_session_queue.py:1083` when both `session_type` and
  `project_config` are omitted (keep the eng default behavior).
- Add tests: teammate-vs-eng enqueue for both scanner paths; `persona_to_session_type` mapping;
  per-message try/except WARNING fires and scan continues; enqueue-default WARNING fires via caplog.

### 3. Validate Finding 1
- **Task ID**: validate-granite-transcript
- **Depends On**: build-granite-transcript
- **Assigned To**: granite-validator
- **Agent Type**: validator
- **Parallel**: false
- Run granite container + pty_pool + transcript_tailer tests; confirm the three diagnostic
  branches and the predicate/slug fixes; report pass/fail.

### 4. Validate Finding 2
- **Task ID**: validate-persona-scanners
- **Depends On**: build-persona-scanners
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Run catchup + reconciler tests; confirm teammate chat → TEAMMATE, default → ENG; report
  pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-granite-transcript, validate-persona-scanners
- **Assigned To**: granite-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and the eng/teammate session docs per the
  Documentation section.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: granite-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit + relevant integration suites; verify all success criteria including docs;
  generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Granite container tests | `pytest tests/unit/granite_container/ -q` | exit code 0 |
| Catchup + reconciler tests | `pytest tests/integration/test_catchup_revival.py tests/integration/test_per_chat_catchup_cutoff.py tests/unit/test_reconciler.py tests/integration/test_reconciler.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Catchup resolves persona | `grep -n "persona_to_session_type\|resolve_persona" bridge/catchup.py` | output contains a persona resolver |
| Reconciler resolves persona | `grep -n "persona_to_session_type\|resolve_persona" bridge/reconciler.py` | output contains a persona resolver |
| Shared helper exists | `grep -n "def persona_to_session_type" bridge/routing.py` | output contains the helper def |
| Predicate covers session-ids | `grep -n "pm_session_id" agent/granite_container/pty_pool.py` | output contains pm_session_id |
| Diagnostic branches named | `grep -n "transcript read: path-None\|transcript read: file-missing\|transcript read: no-new-entry" agent/granite_container/container.py` | all three substrings present |

## Critique Results

Critique 1 (war room, 7 critics): **NEEDS REVISION** — 2 blockers + 1 structural blocker. All addressed in this revision pass.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consistency Auditor | Plan contradicted itself on persona-helper extraction (three-places rule vs "inline") | Technical Approach + Solution + Rabbit Holes + OQ3 | Resolved to EXTRACT `persona_to_session_type` in `bridge/routing.py`; live site counts toward the three-place threshold. |
| BLOCKER | Skeptic/Simplifier/User/Adversary | Two path fixes are admitted production no-ops while live cause is unconfirmed; synthetic criteria presented as proof of fix | Technical Approach (diagnostic-first sequencing) + Success Criteria (fail-closed 1a/1b) + Risk 1 | Diagnostic lands first; "Finding 1 closed" requires the diagnostic firing on a real run AND a fix converting it to a delivered reply; `no-new-entry` branch has explicit follow-up exit condition. |
| BLOCKER (structural) | Structural check | Test files cited as `tests/unit/` are actually `tests/integration/` | Test Impact + Task 1/2 Validates + Verification table | Corrected `test_catchup_revival.py` and `test_per_chat_catchup_cutoff.py` to `tests/integration/`; both `test_reconciler.py` paths listed. |
| CONCERN | Operator | Diagnostic WARNING had no grep-able destination | Success Criteria 1a + Documentation | Named `logs/worker.log` + three stable greppable substrings. |
| CONCERN | Operator/Archaeologist | Silent teammate→eng degradation in scanner fallback (#827 pattern) | Failure Path Test Strategy + Task 2 | Narrow per-message try/except with greppable WARNING; not the outer scan-level catch; unit-tested. |
| CONCERN | Archaeologist | Silent ENG default (root mechanism) deferred | Solution + Architectural Impact + Task 2 | Greppable WARNING added at `agent_session_queue.py:1083` when both kwargs omitted; default kept (OQ2). |
| CONCERN | Archaeologist | `_needs_session_spawn` term churn with no invariant | Technical Approach + Task 1 | Docstring updated to state the full per-session-identity invariant. |
| CONCERN | Adversary | realpath slug asymmetric / `realpath("")` returns CWD | Technical Approach + Solution + Task 1 | `realpath` only when cwd truthy; `if not session_id: return None` precedes realpath. |
| CONCERN | Simplifier | "Centralize the slug computation" smuggled a refactor | Technical Approach + Rabbit Holes | Dropped the merge; two one-line realpath inserts, functions stay separate. |
| CONCERN | Consistency Auditor | Architectural Impact framing contradicted keep-the-default decision | Architectural Impact | Reworded data-ownership sentence per the auditor's suggested phrasing. |
| CONCERN | User | Known-misleading fallback text left in place | Rabbit Holes | Permitted rewording the `OPERATOR_TERMINAL_MESSAGE` string literal (truthful) while keeping the trigger condition untouched. |
| CONCERN | Skeptic | Transcript-naming contract version-fragile | Technical Approach + Task 1 | All three read sites log the fully-resolved attempted path + IDs, not just category labels. |

---

## Open Questions

1. **Live branch confirmation:** the diagnostic is designed to reveal which empty-read branch
   the live cyndra session hits. Should the build BLOCK on observing a fresh live container log
   (re-run the repro) before merging the two latent path fixes, or land the diagnostic and the
   two fixes in the same PR and confirm the branch during the verification step? (Plan currently
   lands them in one PR with the diagnostic as the gating deliverable, confirmed by re-running
   the repro in the build's verification phase.)
2. **`SessionType.ENG` default:** keep the unsafe default at `agent_session_queue.py:1083`
   (fix only the two call sites, as planned) or harden the default to require an explicit
   `session_type` (affects every direct caller)? Plan defaults to the former.
3. **Shared persona-mapping helper:** RESOLVED — the mapping triplicates (live + catchup +
   reconciler), so the plan extracts a single `persona_to_session_type` helper in
   `bridge/routing.py` and calls it from all three sites. (No longer an open question; recorded here
   for traceability.)
