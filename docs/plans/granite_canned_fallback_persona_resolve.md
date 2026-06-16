---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1708
last_comment_id:
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
- **Data ownership:** unchanged. `session_type` was always meant to be set at enqueue; the
  scanner paths were silently defaulting it.
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

- **Diagnostic disambiguation (Finding 1, lead change):** Split the single
  `"PM transcript read empty"` log into three observable branches — *path is None*,
  *file missing on disk*, *file present but no new text-bearing entry* — emitting a WARNING
  for the first two. This is the highest-value change: it identifies which branch the live
  cyndra session actually hits without guessing.
- **`_needs_session_spawn` predicate fix (Finding 1, latent bug 1):** Add
  `or spec.pm_session_id or spec.dev_session_id` so a spec carrying session-ids always forces
  a per-session spawn — even if some future session shape has an empty env. Closes the
  prewarmed-pair-reuse-without-session-id path that yields a None/auto-UUID transcript.
- **Symlink-resolved slug (Finding 1, latent bug 2):** Resolve `cwd` via `os.path.realpath`
  before `.replace("/","-")` in `_transcript_path` (and the matching computation in
  `bridge_adapter.py`) so the slug matches claude's symlink-resolved slug for any
  symlink-crossing working directory.
- **Catchup persona resolution (Finding 2):** In `bridge/catchup.py`, resolve the chat's
  persona via `bridge.routing.resolve_persona(project, chat_title)`, map TEAMMATE →
  `SessionType.TEAMMATE` else `SessionType.ENG`, and pass `session_type=` + `project_config=project`
  to `enqueue_agent_session_fn` — mirroring `telegram_bridge.py:2205-2209, 2394-2395`.
- **Reconciler persona resolution (Finding 2):** Identical fix in `bridge/reconciler.py`
  (`:211-222`) — it shares the omission.

### Flow

Catchup/reconciler scan → find unprocessed teammate-chat message → **resolve_persona(project,
chat_title)** → map to session_type → `enqueue_agent_session(session_type=…, project_config=project)`
→ worker resolves `teammate` → conversational reply (not eng PM↔Dev loop).

Container steady-state read → transcript path is **realpath-slugged** and **always session-id-bound**
→ `last_assistant_text` returns the PM's reply → classification proceeds → no canned fallback.
When a path is still None/missing → **WARNING names the exact branch**.

### Technical Approach

- **Diagnostic first.** Land the log split (`container.py:922-929` and the prime-turn /
  wrap-up read sites) early so a re-run of the live cyndra repro confirms the failing branch.
  The two latent path fixes are correct regardless, but the diagnostic tells us whether
  path-None (predicate fix) or wrong-slug (realpath fix) is the actual live trigger — and
  whether anything else (a third branch) is at play.
- **`_needs_session_spawn`:** extend the existing `bool(...)` expression with the two session-id
  terms; no signature change. Mirrors the conservative "any per-session requirement triggers
  spawn-on-acquire" intent already documented in its docstring.
- **Realpath slug:** centralize the slug computation so `_transcript_path` and the
  `bridge_adapter.py` equivalent cannot drift; apply `os.path.realpath(cwd)` before slugging.
- **Persona resolution in scanners:** import and call the same `resolve_persona` the live
  handler uses; do not duplicate the TEAMMATE→session_type mapping logic divergently — extract
  a tiny shared helper if the mapping appears in three places (live + catchup + reconciler),
  otherwise inline the two-line map to match the live handler exactly.
- **Defense-in-depth (optional, gate on review):** the silent `SessionType.ENG` default at
  `agent_session_queue.py:1083` is what made the omission invisible. The primary fix is at the
  two call sites; do not change the default unless review explicitly wants it (it would alter
  behavior for every other caller that intentionally relies on the eng default).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `transcript_tailer.py` functions are already fail-silent (`except OSError: return []`,
  broad `except Exception`). The diagnostic change is in `container.py` at the read site, NOT
  inside the tailer — assert the new WARNING fires (path-None / file-missing) via caplog rather
  than letting it be swallowed.
- [ ] Catchup/reconciler: the `enqueue_agent_session_fn` call is inside the scan loop. Assert
  that a persona-resolution failure (e.g. `resolve_persona` raising) does not abort the whole
  scan — wrap defensively and fall back to the existing eng default with a logged warning, and
  test that fallback path.

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
- [ ] `tests/unit/test_catchup_revival.py` and `tests/unit/test_per_chat_catchup_cutoff.py` —
  UPDATE: assert a teammate-configured chat enqueues with `session_type=SessionType.TEAMMATE`
  and `project_config` set; assert an eng/default chat still enqueues eng.
- [ ] `tests/unit/test_reconciler.py` (and `tests/integration/test_reconciler.py` if present) —
  UPDATE: same teammate-vs-eng enqueue assertions for the reconciler path.
- [ ] `tests/unit/test_agent_session_scheduler_persona.py` / `test_persona_loading.py` —
  REVIEW (likely no change): confirm they don't assert the old eng-default behavior for scanner
  paths; UPDATE only if they encode the bug.

## Rabbit Holes

- **Do NOT rewrite the tailer or baseline counting.** Recon proved both are correct against
  2.1.178. Touching them re-introduces risk for zero benefit.
- **Do NOT change the `OPERATOR_TERMINAL_MESSAGE` text or the wrap-up guard's trigger
  condition.** The fallback is correct *as a last resort*; the bug is that the loop reaches it
  spuriously. Fix the cause, not the fallback.
- **Do NOT broaden the `SessionType.ENG` default change** at `agent_session_queue.py:1083`
  beyond what review approves — it affects every direct caller.
- **Do NOT chase a hypothetical claude 2.1.178 format change.** Confirmed unchanged. Resist
  re-investigating the schema.
- **Do NOT build a generic "persona resolution service" abstraction.** Mirror the live
  handler's two-line mapping; extract a helper only if the exact mapping triplicates.

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
  diagnostic, the `_needs_session_spawn` session-id requirement, and the realpath-slug rule.
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

- [ ] A re-run of the live cyndra repro (or the next live cyndra container log) shows the
  diagnostic naming the exact empty-read branch (path-None / file-missing / no-new-entry),
  no longer the conflated generic message.
- [ ] A catchup-re-ingested message for a `teammate`-configured chat resolves persona
  `teammate` (not `engineer`); a default chat still resolves `eng`.
- [ ] The reconciler path resolves persona identically.
- [ ] `_needs_session_spawn` returns True for a spec carrying session-ids with empty env.
- [ ] `_transcript_path` produces a realpath-resolved slug for a symlink-crossing cwd.
- [ ] With a correct transcript path, a PM that produced a reply delivers the PM's text — the
  `OPERATOR_TERMINAL_MESSAGE` canned fallback is NOT shipped.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `bridge/catchup.py` and `bridge/reconciler.py` reference `resolve_persona`
  and pass `session_type=` + `project_config=` to `enqueue_agent_session`.

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
  the prime-turn read, and the wrap-up-guard read into path-None / file-missing / no-new-entry
  branches; emit WARNING for the first two.
- Extend `_needs_session_spawn` (`pty_pool.py:389-405`) with
  `or spec.pm_session_id or spec.dev_session_id`.
- Apply `os.path.realpath(cwd)` before `.replace("/","-")` in `_transcript_path`
  (`container.py:284-297`) and the matching slug computation in `bridge_adapter.py:92-100`.
- Add/extend unit tests for all three.

### 2. Finding 2 persona resolution in scanners
- **Task ID**: build-persona-scanners
- **Depends On**: none
- **Validates**: `tests/unit/test_catchup_revival.py`, `tests/unit/test_per_chat_catchup_cutoff.py`,
  `tests/unit/test_reconciler.py`
- **Informed By**: recon (catchup + reconciler omit session_type/project_config; live path at
  telegram_bridge.py:2205-2209,2394-2395 is the template)
- **Assigned To**: persona-scanner-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/catchup.py:235-246` and `bridge/reconciler.py:211-222`: resolve
  `resolve_persona(project, chat_title)`, map TEAMMATE→`SessionType.TEAMMATE` else
  `SessionType.ENG`, and pass `session_type=` + `project_config=project`. Wrap in a defensive
  fallback to the eng default on resolution failure (logged).
- Add unit tests asserting teammate-vs-eng enqueue for both paths.

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
| Catchup + reconciler tests | `pytest tests/unit/test_catchup_revival.py tests/unit/test_per_chat_catchup_cutoff.py tests/unit/test_reconciler.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Catchup resolves persona | `grep -n "resolve_persona" bridge/catchup.py` | output contains resolve_persona |
| Reconciler resolves persona | `grep -n "resolve_persona" bridge/reconciler.py` | output contains resolve_persona |
| Predicate covers session-ids | `grep -n "pm_session_id" agent/granite_container/pty_pool.py` | output contains pm_session_id |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

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
3. **Shared persona-mapping helper:** inline the TEAMMATE→session_type two-liner at each of the
   three sites (live + catchup + reconciler), or extract a single shared helper? Plan inlines to
   match the live handler unless review prefers extraction.
