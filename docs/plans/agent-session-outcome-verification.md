---
status: Planning
type: feature
appetite: Large
owner: Tom Counsell
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1267
last_comment_id: null
---

# AgentSession Outcome Verification

## Problem

A Dev session runs `/do-build`, narrates "tests pass, PR opened at https://github.com/.../pull/9999, docs updated," and emits `<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"..."}} -->`. `classify_outcome()` reads `status:"success"`, `_handle_dev_session_completion()` calls `psm.complete_stage("BUILD")`, and the PM advances to TEST. Hours later, a human checks the PR list and finds nothing — the URL was hallucinated, no commit was pushed, the agent lied and the pipeline trusted it.

This is the recurring class of failure issue #1267 surfaces. The current OUTCOME contract (Tier 0 of `agent/pipeline_state.py:670` `classify_outcome`) parses agent self-attestation and never verifies it against reality. Non-SDLC sessions (Teammate emailing a customer, PM directly running `valor-telegram send`) have no outcome surface at all — there is no contract to violate, so there is nothing to verify.

**Current behavior:**

- Skills *sometimes* emit `<!-- OUTCOME {...} -->` as the last line of agent output (when they remember).
- `classify_outcome()` parses Tier 0 (OUTCOME contract) → Tier 1 (SDK `stop_reason`) → Tier 2 (text patterns) and returns one of `success / fail / partial / ambiguous`. Nothing checks whether the claimed `pr_url` is reachable, whether the claimed Telegram message exists in the outbox, whether the claimed file edit landed in the diff.
- Outcomes attach to the parent PM's `stage_states` JSON via `complete_stage()` / `fail_stage()`, not to the session that did the work. The session that actually built the thing has no first-class record of what it claimed to produce.
- Teammate sessions and direct-action PM sessions bypass the contract entirely.
- When `classify_outcome` returns `fail`, the pipeline routes to PATCH (for TEST/REVIEW) or back to PLAN (for CRITIQUE). There is no path back into the queue for a session whose claimed outcome is *later discovered* to be false.

**Desired outcome:**

A reader of an `AgentSession` record can answer "did this session actually do what it claimed?" without re-reading the transcript. Each session emits structured `claimed_outcomes` at completion (via a new `record_outcome` call, replacing the comment-block hack). A worker-side verifier checks each claim against the world (GitHub API for PR URLs, Redis email/Telegram outbox for sent messages, filesystem/git diff for file writes). Verified outcomes update `verified_outcomes` on the session. A verification mismatch sets `status="failed_verification"` (a new terminal lifecycle state) and routes consequence by session type — Dev sessions auto-resume into PATCH with the diff between claimed and verified; Teammate/PM sessions escalate to the human via Telegram. The mechanism is uniform across all `session_type` values, not bolted onto SDLC stages.

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6` (HEAD at plan time)
**Issue filed at:** 2026-05-04T09:16:39Z (~12 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/pipeline_state.py:102-135` — claimed `_OUTCOME_RE` and `_parse_outcome_contract` definitions — still holds; lines now span 102-138.
- `agent/pipeline_state.py:670` — `classify_outcome` three-tier method — still present at line 670.
- `agent/session_completion.py:1560` — `_handle_dev_session_completion()` — still present at line 1560.
- `models/agent_session.py:144` — `session_type = KeyField(null=True)` discriminator — confirmed.
- `models/agent_session.py:114-138` — 13-state lifecycle (5 terminal: completed/failed/killed/abandoned/cancelled) — confirmed.
- `models/session_lifecycle.py:217` — `finalize_session()` is the sole terminal-transition writer — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1099 — closed, Mode 4 OOM defer; precedent for nullable-field treatment under `_heal_descriptor_pollution`.
- #1172 — closed, Pillar A in-flight visibility fields (`current_tool_name`, `last_tool_use_at`, `last_turn_at`); same nullable-field pattern.
- PR #667 — merged 2026-04-03, "Parse OUTCOME contracts in classify_outcome() for structured stage classification" — this is the PR that introduced Tier 0 of the current contract. This plan extends, doesn't replace, that work.
- PR #351 — merged 2026-03-10, "Typed outcomes from /do-* skills" — original typed-outcome groundwork.

**Commits on main since issue was filed (touching referenced files):**
- `5055b527 feat(completion-runner): mid-session-send-aware completion suppression (#1262) (#1278)` — touched `session_completion.py`. Re-read: changes the completion-runner suppression logic for mid-session sends; does NOT touch `_handle_dev_session_completion` or the OUTCOME contract path. Irrelevant to this plan.
- No commits to `agent/pipeline_state.py` or `models/agent_session.py` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** None. `pipeline-state-machine.md` (feature doc, not a plan) is the existing reference for `agent/pipeline_state.py`. No active plan modifies `classify_outcome` or the OUTCOME contract.

**Notes:** The bug-fix the recent commit makes ("mid-session-send-aware completion suppression" #1262) is concurrent but disjoint — it changes when completion runners *suppress* the final stop-drafter send, not how outcomes are classified or verified.

## Prior Art

- **PR #667** (merged 2026-04-03): "Parse OUTCOME contracts in classify_outcome() for structured stage classification". Introduced the Tier 0 OUTCOME contract (`<!-- OUTCOME {...} -->` block). **Outcome:** shipped successfully but limited — agent-authored, unverified, stage-scoped, falls through silently when malformed. **Relevance:** this plan promotes the contract from a parsed comment block to a typed `record_outcome` tool call, and adds the verification layer that #667 explicitly did not include.
- **PR #351** (merged 2026-03-10): "Typed outcomes from /do-* skills". Established the JSON shape `{status, stage, artifacts, ...}`. **Outcome:** shipped. **Relevance:** the JSON shape stays — the change is *who writes it* and *whether it gets verified*.
- **Issue #236 / `docs/features/build-output-verification.md`**: Prior verification work for `/do-build` specifically — three layers (post-task git diff, pre-validation commit count, pre-PR commit count). **Outcome:** shipped, prevents empty-PR creation. **Relevance:** this is a *narrow* verifier scoped to one skill on one stage. The current plan generalizes the pattern to `AgentSession` so it covers every session type, not just `/do-build`. Reuse: the git-diff/commit-count checks become one verifier among several.
- **Issues #706, #708, #709, #710, #717** (all closed 2026-04-05): cluster of "Verify skipped SDLC stages for session zombie fix (#700)" issues. **Outcome:** retrospective verification work. **Relevance:** establishes precedent that "did this stage actually run?" is a recurring class of question worth tooling for. Different shape (post-hoc audit vs. pre-advancement gate) but adjacent concern.
- **`sdlc-tool verdict`** (`docs/features/sdlc-tool-resolver.md`): per-stage CRITIQUE/REVIEW verdicts on the parent PM. **Outcome:** shipped, in active use. **Relevance:** orthogonal — verdicts capture *human/critic acceptance*, this work captures *machine truth about agent claims*. The two coexist; verification can fire and pass while a human still rejects the verdict.

## Research

This work is purely internal — no external libraries, APIs, or ecosystem patterns to research. The fazm `SESSION-REPLAY-SKILL.md` reference in the issue is a design inspiration the issue body already cites in full; rereading it would not surface new findings.

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

### spike-1: Can a single verifier function deterministically check the claimed `pr_url` artifact within 5s?
- **Assumption**: "`gh pr view <URL> --json state,headRefName,number` returns within 5s and reliably distinguishes 'PR exists' from 'PR does not exist' from 'network error'."
- **Method**: code-read + manual `gh` CLI test
- **Finding**: `gh pr view` returns in ~600-1500ms on a healthy network; exit code 0 = PR exists, exit code 1 with stderr containing "no pull requests found" = PR does not exist, exit code 1 with other stderr = network/auth error. Three-way distinction is reliable. The 5s budget is comfortable.
- **Confidence**: high
- **Impact on plan**: Verifier 1 (PR URL) proceeds with `gh pr view` as the primary check. Three-way return: `verified` / `mismatch` / `unverifiable`.

### spike-2: Does the Redis Telegram outbox expose enough state to verify "Telegram message sent"?
- **Assumption**: "We can scan `tg:outbox:*` (or whatever the canonical key pattern is) and confirm that a message claimed in `claimed_outcomes` actually landed in the outbox before drainage."
- **Method**: code-read of `bridge/redundancy_filter.py`, `tools/send_message.py`, search for outbox keys.
- **Finding**: The Telegram outbox is *transient* — once drained by the bridge, the keys are gone. Verification cannot rely on scanning the outbox after the fact. Two alternatives exist: (a) check `pm_sent_message_ids` on the parent (already populated for PM-self-messages — see `models/agent_session.py:218`), (b) check `recent_sent_drafts` on the session (populated by `record_recent_sent_draft()` after successful send — see `models/agent_session.py:228`). Both are durable and live on the session object.
- **Confidence**: high
- **Impact on plan**: Verifier 2 (Telegram message sent) reads `recent_sent_drafts` for Teammate/Dev sessions and `pm_sent_message_ids` for PM sessions. No Redis scan needed.

### spike-3: Can git-diff verify "file edit" claims for Dev sessions running in worktrees?
- **Assumption**: "`git -C .worktrees/{slug} log --oneline main..HEAD` plus `git diff --name-only main..HEAD` reliably reports what was actually committed by a Dev session."
- **Method**: code-read of `agent/worktree_manager.py`, existing build-output-verification feature.
- **Finding**: Yes — `docs/features/build-output-verification.md` already does this for `/do-build`. The pattern is proven. The verifier reuses these commands, scoped to the session's `slug` field.
- **Confidence**: high
- **Impact on plan**: Verifier 3 (file write/edit) reuses the existing build-output-verification pattern, generalized to read claimed paths from `claimed_outcomes`.

### spike-4: Where in the worker pipeline does the verifier fire — turn boundary, session end, or stage transition?
- **Assumption**: "Session-end (inside `_handle_dev_session_completion`, after `complete_transcript`, before `psm.complete_stage`) is the right hook point."
- **Method**: code-read of `agent/session_completion.py:1560-1740`, `agent/output_router.py`.
- **Finding**: Session-end is correct. Turn-boundary is too early — the agent may still be mid-work and emit a final OUTCOME on a later turn. Stage-transition is too late — the next stage already started on bad data. Session-end (specifically: between `psm.classify_outcome()` and `psm.complete_stage()`/`psm.fail_stage()` at lines 1623-1627) is the natural insertion point. If the verifier returns `mismatch`, we call `psm.fail_stage()` instead of `complete_stage()` and the session enters `failed_verification`.
- **Confidence**: high
- **Impact on plan**: Verifier runs inside `_handle_dev_session_completion`, gating the call to `complete_stage()`. For non-SDLC sessions (Teammate, direct-action PM), a parallel hook fires from `complete_transcript()` itself — see Technical Approach.

## Data Flow

End-to-end flow for a verified Dev BUILD session:

1. **Entry point**: PM session creates a Dev session via `valor-session create --role dev --slug {slug} --message "..."`. Session enqueued; worker picks it up.
2. **Execution**: Worker spawns CLI harness; the Dev agent runs `/do-build`. The skill instructs the agent to call `record_outcome(stage="BUILD", status="success", artifacts={"pr_url": "...", "files_changed": [...]})` instead of (or in addition to, see No-Gos) emitting the `<!-- OUTCOME ... -->` comment block.
3. **`record_outcome` MCP tool**: Writes `claimed_outcomes` (a `ListField` of dicts on the session, append-only across the session lifetime) via `session.save(update_fields=["claimed_outcomes", "updated_at"])`. Each entry is `{ts, stage, status, artifacts, raw_text}`. Multiple calls in one session append; the verifier consumes the last entry per stage.
4. **Session completion**: Harness exits; `_handle_dev_session_completion()` runs.
5. **Classify**: `psm.classify_outcome(stage, stop_reason, result)` runs as today (Tier 0/1/2). Result written to a local var.
6. **Verify** (new): `OutcomeVerifier(session).verify_claimed(stage)` reads `session.claimed_outcomes`, finds the entry for `stage`, dispatches per-artifact-type verifiers (PR URL, Telegram send, file write), aggregates a verdict (`verified` / `mismatch` / `unverifiable`), and writes `verified_outcomes` (parallel `ListField`) on the session.
7. **Reconcile**: If `classify_outcome` says `success` AND verifier says `verified` → `psm.complete_stage(stage)`. If `success` but `mismatch` → `psm.fail_stage(stage)`, set `session.status = "failed_verification"` via `finalize_session()`, set `session.expectations` to the verification diff for human-readable display. If `success` but `unverifiable` (network error, missing artifact) → log a structured warning, *still* call `complete_stage()` (don't punish the agent for transient infrastructure flake), but flag `verified_outcomes[-1].confidence = "unverifiable"` so the dashboard can show it.
8. **Output**: Stage comment posted to GitHub issue includes `(verified)` or `(verification mismatch: <reason>)` suffix. PM steered with the verified outcome.

For non-SDLC sessions (Teammate replying to email, PM running `valor-telegram send` directly):

1. **Entry point**: Bridge enqueues a Teammate session for inbound email; worker picks it up.
2. **Execution**: Agent runs, calls `record_outcome(stage=None, status="success", artifacts={"telegram_sent": true, "telegram_message_id": 123})` or `{"email_sent": true, "email_to": "alice@..."}`.
3. **Session completion**: `complete_transcript()` runs.
4. **Verify**: A new hook in `complete_transcript()` (parallel to the SDLC path) calls `OutcomeVerifier(session).verify_claimed(stage=None)` for sessions where `claimed_outcomes` is non-empty AND `session_type != "dev"`.
5. **Reconcile**: On `mismatch`, `finalize_session(status="failed_verification", reason=...)`. The drafter for the next session in the same chat gets the failure context via `expectations`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| PR #667 (Tier 0 OUTCOME contract) | Parses `<!-- OUTCOME {...} -->` comment blocks; returns the agent's claimed status verbatim. | **Self-attestation only** — never checks the claim against reality. The agent's `pr_url` could be fabricated. Stage-scoped (writes to parent PM's `stage_states`, not to the executing session). Silently falls through to Tier 2 text patterns when malformed. No path for *non-SDLC* sessions. |
| PR #351 (typed outcomes) | Defined the JSON shape used by `/do-build`, `/do-test`, `/do-pr-review`. | Same self-attestation problem. The shape is correct; the missing piece is the verifier. |
| #236 build-output-verification | Three-layer git-diff/commit-count check inside `/do-build`. | **Skill-scoped, not session-scoped.** Lives in the build skill's WORKFLOW.md and only catches the empty-PR case for `/do-build`. Doesn't generalize to "agent claimed it sent a Telegram message." Doesn't apply to Teammate sessions. |

**Root cause pattern:** Each prior fix solved one slice of self-attestation drift in one location. None promoted verification to a cross-session-type, cross-stage property of the `AgentSession` model. The pattern keeps recurring because every new session type or skill creates a new attestation surface that the per-skill checks don't cover.

## Architectural Impact

- **New dependencies**: None external. New internal module `agent/outcome_verifier.py` (verifier dispatcher + per-artifact verifiers). New MCP tool `record_outcome` exposed via `mcp_servers/outcome_server.py` (or extension of an existing MCP server — see Open Questions).
- **Interface changes**: 
  - New `AgentSession` fields: `claimed_outcomes` (ListField, nullable, default None), `verified_outcomes` (ListField, nullable, default None).
  - New terminal status: `failed_verification` (sixth terminal state alongside `completed`, `failed`, `killed`, `abandoned`, `cancelled`).
  - New skill instruction: every `/do-*` skill that currently emits `<!-- OUTCOME ... -->` learns to *also* call `record_outcome(...)`. The comment-block emission stays for the transition window — both paths feed the same `claimed_outcomes` list (Tier 0 parser writes through to the same field). The two paths converge in `agent/pipeline_state.py::_parse_outcome_contract`.
- **Coupling**: increases coupling between `agent/session_completion.py` and the new `agent/outcome_verifier.py`. Decreases coupling between `pipeline_state.py` and skill-specific text-pattern checks (Tier 2 patterns can be retired for stages that have a typed verifier). Adds a write site to `claimed_outcomes`/`verified_outcomes` from `record_outcome` — bounded, single writer per field per source.
- **Data ownership**: `claimed_outcomes` is owned by the agent (writes via `record_outcome`). `verified_outcomes` is owned by the worker (writes only from `OutcomeVerifier`). No dual-writer races.
- **Reversibility**: high. Both fields are nullable; the verifier dispatches off `claimed_outcomes` being non-empty; if the new module is disabled, `_handle_dev_session_completion` falls back to today's behavior. The new `failed_verification` terminal status is opt-in to the lifecycle module's allowlist — disabling the verifier means the status is never set.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (schema review, verifier dispatch design, lifecycle integration)
- Review rounds: 2 (initial review, post-PATCH review)

The work touches three subsystems (`AgentSession` model, pipeline state machine, session completion handler), introduces a new MCP tool, adds a new terminal lifecycle state, and threads through every `/do-*` skill that emits an OUTCOME today. Each step is small, but the surface is wide.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | Verifier 1 calls `gh pr view` to confirm PR existence |
| Redis reachable | `redis-cli ping` | Verifier 2 reads `recent_sent_drafts` (Popoto-backed) |
| `.worktrees/` writable | `test -w .worktrees/` | Verifier 3 runs `git -C .worktrees/{slug}` for file-write claims |

Run all checks: `python scripts/check_prerequisites.py docs/plans/agent-session-outcome-verification.md`

## Solution

### Key Elements

- **`AgentSession.claimed_outcomes` (ListField, nullable)**: Append-only list of `{ts, stage, status, artifacts, raw_text}` dicts. Written by the new `record_outcome` MCP tool and (for the transition window) by the existing Tier 0 OUTCOME parser. One entry per `record_outcome` call; verifier consumes the last entry per stage.
- **`AgentSession.verified_outcomes` (ListField, nullable)**: Append-only list of `{ts, stage, verdict, evidence, confidence}` dicts. Verdict is `verified` / `mismatch` / `unverifiable`. Confidence is `high` / `medium` / `low` reflecting how reliably the per-artifact verifier could check the claim.
- **`agent/outcome_verifier.py`**: New module. `OutcomeVerifier(session).verify_claimed(stage=None)` reads `claimed_outcomes`, dispatches per-artifact verifiers from a registry, aggregates a verdict, writes `verified_outcomes`, returns the verdict for the caller's reconciliation step. The registry maps artifact-key (`pr_url`, `telegram_sent`, `email_sent`, `files_changed`) to a verifier function. Initial registry: 4 verifiers (the top concrete claims agents make today).
- **`record_outcome` MCP tool**: Exposed through `mcp_servers/outcome_server.py`. Replaces the comment-block hack with a typed call. Writes to `session.claimed_outcomes` via Popoto partial save. Returns the session_id and entry index so the agent can confirm the write.
- **`failed_verification` terminal status**: New value in `models/session_lifecycle.py` allowlist. Set by `OutcomeVerifier` when verdict is `mismatch`. Routed by session type: Dev → re-enqueue with PATCH context; Teammate/PM → escalate via Telegram with `expectations` populated.
- **Verifier dispatch hook in `_handle_dev_session_completion`**: After `psm.classify_outcome()`, before `psm.complete_stage()`, the new verifier runs. The reconciliation rules (verifier verdict × classify_outcome result × session_type) determine whether the stage is completed, failed, or whether the whole session is finalized as `failed_verification`.

### Flow

**Dev session BUILD path:**
PM creates Dev session → Worker pops session → CLI harness runs `/do-build` → Agent calls `record_outcome("BUILD", "success", {"pr_url": "...", "files_changed": [...]})` → Harness exits → `_handle_dev_session_completion` → `psm.classify_outcome()` returns `success` → `OutcomeVerifier.verify_claimed("BUILD")` checks `gh pr view <url>` AND `git -C .worktrees/{slug} log` → Both verified → `psm.complete_stage("BUILD")` → PM steered → Pipeline advances to TEST.

**Dev session BUILD with hallucinated PR URL:**
... → `OutcomeVerifier.verify_claimed("BUILD")` runs `gh pr view <url>` → `gh` exits 1, "no pull requests found" → verdict = `mismatch` → `verified_outcomes` written with `evidence={"pr_url_check": "no pull request at <url>"}` → `psm.fail_stage("BUILD")` → `finalize_session(status="failed_verification", reason="claimed pr_url not found")` → PM steered with verification-mismatch context → PM dispatches `/do-patch` (or escalates to human if PATCH cap reached).

**Teammate session sending an email:**
Bridge enqueues Teammate → worker pops → agent runs, calls `record_outcome(stage=None, status="success", artifacts={"email_sent": true, "email_to": "alice@..."})` → `complete_transcript()` runs verifier → verifier checks `recent_sent_drafts` for an entry with matching recipient → if not found, verdict = `mismatch` → `finalize_session(status="failed_verification")` → drafter for the next inbound from Alice receives the failure context via `expectations`.

### Technical Approach

- **Field additions to `AgentSession`** (`models/agent_session.py`): `claimed_outcomes = ListField(null=True)`, `verified_outcomes = ListField(null=True)`. Both nullable; existing records' `_heal_descriptor_pollution` walks fields generically (memory: `feedback_field_backcompat_heal`, issues #1099 / #1172). No parallel-run migration. No back-compat code.
- **New terminal status `failed_verification`** (`models/session_lifecycle.py`): added to the terminal allowlist. `finalize_session(status="failed_verification", reason=...)` is the sole writer. The lifecycle module's terminal-to-different-terminal guard already handles the precedence (a session already `completed` cannot be flipped to `failed_verification` after the fact — that's a separate retroactive-audit concern, out of scope; see No-Gos).
- **`record_outcome` MCP tool** (`mcp_servers/outcome_server.py`, new file): single tool exposed: `record_outcome(stage: str | None, status: str, artifacts: dict, notes: str = "")`. Validates `status ∈ {"success", "fail", "partial"}` and at least one artifact key. Reads the session_id from env (`AGENT_SESSION_ID`), loads the session, appends to `claimed_outcomes`, partial-saves. Returns `{session_id, entry_index}` to the agent.
- **`agent/outcome_verifier.py`** (new file):
  ```python
  class OutcomeVerifier:
      def __init__(self, session: AgentSession): ...
      def verify_claimed(self, stage: str | None = None) -> str:
          """Return 'verified' | 'mismatch' | 'unverifiable'."""
  ```
  Dispatch registry maps artifact-key → verifier callable. Each verifier returns `(verdict, evidence_dict)`. Aggregator: any artifact `mismatch` → session `mismatch`; all `verified` → session `verified`; mixed `verified`/`unverifiable` → session `unverifiable` (do not punish the agent for infra flake).
- **Initial verifier registry (4)**:
  - `pr_url` → `gh pr view <url> --json state,number,headRefName` with 5s timeout. Exit 0 = `verified`, exit 1 with "no pull requests" = `mismatch`, other = `unverifiable`.
  - `telegram_sent` → for `session_type == "pm"`, check `pm_sent_message_ids` non-empty; else check `recent_sent_drafts` for an entry within the last 60s. `verified` if found, `mismatch` if not.
  - `email_sent` → check `recent_sent_drafts` (the email path also funnels through this list per `tools/send_message.py`) for an entry whose `artifacts` contains the claimed recipient. `verified` if matched, `mismatch` if not.
  - `files_changed` → for sessions with a `slug`, run `git -C .worktrees/{slug} diff --name-only main..HEAD`. Compare to claimed list. Subset match → `verified`, missing claimed paths → `mismatch`, worktree gone → `unverifiable`.
- **Hook in `_handle_dev_session_completion`** (`agent/session_completion.py:1623`): between `outcome = psm.classify_outcome(...)` and `psm.complete_stage(...)`, insert:
  ```python
  from agent.outcome_verifier import OutcomeVerifier
  verifier_verdict = OutcomeVerifier(parent).verify_claimed(stage=current_stage)  # parent runs the work? actually session-of-the-work — see Open Question 1
  if outcome == "success" and verifier_verdict == "mismatch":
      outcome = "fail"
      reconcile_failed_verification(session, agent_session, ...)
  ```
- **Hook in `complete_transcript()`** (for non-SDLC sessions): mirror invocation, gated on `session_type != "dev"` and `claimed_outcomes` non-empty.
- **Skill updates**: `/do-build`, `/do-test`, `/do-pr-review`, `/do-plan-critique` learn to call `record_outcome` (via the MCP tool) *in addition to* emitting the comment block. The comment block stays during the transition; the Tier 0 parser writes through to `claimed_outcomes` so legacy emissions are still captured. After 30 days of clean operation the comment block can be retired (separate cleanup issue, out of scope here).
- **Failure consequence policy**:
  - Dev session, verdict `mismatch` → `psm.fail_stage(stage)` → router dispatches PATCH (existing pipeline edge). The next session in the PATCH cycle reads the verification diff from `verified_outcomes[-1].evidence` via the PM steer message.
  - Teammate session, verdict `mismatch` → `finalize_session(status="failed_verification")`. PM is escalated via the standard chat with `expectations` populated. No auto-retry — Teammate sends are user-facing and a re-send risks duplicates.
  - PM session, verdict `mismatch` → escalate to human via `valor-telegram send`. No auto-retry.
  - Verdict `unverifiable` → log a `WARNING` with the artifact-key and reason, write `verified_outcomes` with `confidence="low"`, but proceed as if `verified` (the classification path's outcome is honored). This avoids cascading infra-flake into pipeline failures.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/outcome_verifier.py::OutcomeVerifier.verify_claimed` wraps each per-artifact verifier in `try/except` and converts unexpected exceptions to `(unverifiable, {"error": str(e)})`. Test: inject a verifier that raises; assert verdict is `unverifiable` and `verified_outcomes[-1].confidence == "low"`.
- [ ] `record_outcome` MCP tool wraps the partial save in `try/except`; on save failure, returns an error dict to the agent rather than raising. Test: simulate a Redis write failure; assert the tool returns `{"error": ...}` and the session does not crash.
- [ ] No `except Exception: pass` blocks introduced. Test: grep the diff for the pattern; assert zero matches in new code.

### Empty/Invalid Input Handling
- [ ] `record_outcome` with empty `artifacts={}` → returns error to agent (artifact dict must be non-empty).
- [ ] `record_outcome` with `status=""` or unknown status → returns error.
- [ ] `OutcomeVerifier.verify_claimed` on a session with `claimed_outcomes=None` → returns `unverifiable` (no claim to verify; do not block the pipeline).
- [ ] `OutcomeVerifier.verify_claimed` on a session whose `claimed_outcomes` exists but has no entry for the requested `stage` → returns `unverifiable` with `evidence={"reason": "no_claim_for_stage"}`.
- [ ] Test for whitespace-only / None values inside an artifact (`{"pr_url": "   "}` or `{"pr_url": None}`) → verifier reports `unverifiable`, not crash.

### Error State Rendering
- [ ] When `failed_verification` is set on a Dev session, the GitHub stage comment posted by `_handle_dev_session_completion` includes the verification mismatch evidence (truncated to 500 chars), not just `failed`.
- [ ] When a Teammate session fails verification, the next session in that chat receives `expectations` populated with the human-readable mismatch reason. Assert via integration test.
- [ ] Dashboard renders `failed_verification` distinctly from `failed` (separate status color/badge). Assert by reading `/dashboard.json` after triggering verification failure.

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py::test_classify_outcome_*` (12 tests at lines 628-1139) — UPDATE: the OUTCOME contract path is unchanged, but tests must assert that the new verifier is *not* called from `classify_outcome` (verification is a sibling step, not a sub-step). Add 3 new tests asserting the verifier integrates correctly with `_handle_dev_session_completion`.
- [ ] `tests/integration/test_parent_child_round_trip.py` — UPDATE: existing assertions about `psm.complete_stage()` being called need an extension asserting `verified_outcomes` is also populated when `claimed_outcomes` is set.
- [ ] `tests/unit/test_agent_session_lifecycle.py` — UPDATE: the terminal-status allowlist test needs `failed_verification` added; the terminal-to-different-terminal guard test needs a new case for `failed_verification`.
- [ ] `tests/unit/test_pipeline_state_machine.py::TestStageStates` and the OUTCOME parsing tests — UPDATE: assert that comment-block-derived OUTCOMEs *also* land in `claimed_outcomes` (transition-window invariant: the two write paths converge).
- [ ] New file `tests/unit/test_outcome_verifier.py` — REPLACE (create): one test per verifier in the registry (4 verifiers × {verified, mismatch, unverifiable} = 12 base tests). Plus aggregation tests, exception-path tests, and stage-mismatch tests.
- [ ] New file `tests/integration/test_outcome_verification_e2e.py` — REPLACE (create): full path test — Dev session emits a hallucinated `pr_url`, worker runs verifier, session ends in `failed_verification`, PM is steered with mismatch context. This is the acceptance-criterion test (Issue #1267 last bullet).
- [ ] `tests/integration/test_agent_session_queue_session_type.py` — UPDATE: Teammate session test needs a case for `failed_verification` routing (escalates via Telegram, does NOT re-enqueue).

## Rabbit Holes

- **Don't try to verify everything.** The initial registry is 4 verifiers (PR URL, Telegram send, email send, file write). Adding a verifier per claim type the agent might make is an open-ended trap. Each new verifier has its own freshness/auth/timeout edge cases. Pick the top 4, ship, learn, expand later.
- **Don't use Haiku/LLM for verification.** Tempting for "fuzzy" claims like "I improved the docs" — but LLM-mediated verification reintroduces self-attestation at one remove. Stick to deterministic checks. CLAUDE.md Principle 3 ("intelligent systems over rigid patterns") does NOT mean adding LLMs everywhere — it means using LLMs *where deterministic checks are infeasible*, which is not the case for the four artifact types in this plan.
- **Don't retroactively verify completed sessions.** A "go back and verify the last 30 days of completed sessions" cleanup is tempting but huge. Verification fires forward only; existing records keep their status. (If we later want a retroactive audit tool, it's a separate plan.)
- **Don't try to unify with `sdlc-tool verdict`.** Verdicts answer "did the human/critic accept this stage?" — verification answers "did the agent actually do what it said?". They are orthogonal. Conflating them ("a `failed_verification` becomes a `CHANGES_REQUESTED` review verdict") loses the distinction the issue specifically calls out.
- **Don't try to retire the comment-block contract in this plan.** The transition window matters. Both write paths feed `claimed_outcomes`. Retirement is a separate cleanup once production data shows zero orphan comment-only OUTCOMEs.
- **Don't add a `verification_attempts` retry counter.** The issue calls out the fazm "retry on next pipeline tick" pattern as "possibly catastrophic for our PR-creating sessions." We follow the failure-consequence-by-session-type policy instead. No retry counters.

## Risks

### Risk 1: Verifier latency stacks across stages, slowing the pipeline.
**Impact:** Each stage transition adds 0.5-5s of `gh`/`git` calls. Across a full SDLC pipeline (8 stages), that's up to 40s of added latency.
**Mitigation:** Each verifier has a 5s wall-clock timeout (per-artifact). Verdict on timeout = `unverifiable`, which does NOT block the pipeline. Total per-stage budget is 5s × N artifacts (typically 1-2) = 5-10s — acceptable. Track via a new analytics metric `outcome_verifier.duration_ms`.

### Risk 2: A bug in the verifier blocks legitimate completions, halting the pipeline globally.
**Impact:** If `OutcomeVerifier.verify_claimed` raises uncaught, `_handle_dev_session_completion` could fail before `psm.complete_stage()` runs, leaving sessions stuck.
**Mitigation:** The hook in `_handle_dev_session_completion` wraps the verifier call in `try/except`. On exception, log a `WARNING` and proceed with the original `classify_outcome` result — verification failure of the verifier itself does not block the pipeline. Pair with a Sentry alert on the warning so we notice if it becomes frequent.

### Risk 3: Agents stop emitting `record_outcome` calls during the transition window, causing verification to no-op for valid claims.
**Impact:** During the transition window where both the comment-block and the new `record_outcome` MCP tool exist, agents may forget the new tool. `claimed_outcomes` stays None, verifier returns `unverifiable`, pipeline proceeds without verification.
**Mitigation:** The Tier 0 OUTCOME parser writes through to `claimed_outcomes` whenever it parses a comment-block OUTCOME. So the comment-block path *also* populates the field. This means verification fires for both paths during transition and we're never worse than today. (Test: emit comment-block-only OUTCOME, assert `claimed_outcomes` is populated.)

### Risk 4: `failed_verification` confuses operators who don't yet recognize the new status.
**Impact:** Dashboards, alerts, and SDLC docs that enumerate terminal statuses miss the new one. Operators see "session has weird status" and don't know what to do.
**Mitigation:** Add `failed_verification` to: `models/agent_session.py` docstring (13-state lifecycle becomes 14-state), `docs/features/session-lifecycle.md`, the dashboard's status-color map, and the SDLC docs. All listed in Documentation section.

### Risk 5: A successful `gh pr view` against a *closed* PR is treated as `verified` even though the PR was abandoned.
**Impact:** Agent claims "PR opened," PR was opened then closed before verification ran — verifier reads PR exists, returns `verified`. False negative for our purposes.
**Mitigation:** Verifier 1 specifically checks `state == "OPEN"` (or `"MERGED"` if the stage is MERGE) — not just existence. `gh pr view --json state` returns the field; the verifier compares it to the expected state for the claimed stage.

## Race Conditions

### Race 1: `record_outcome` and `_handle_dev_session_completion` write to the same session concurrently.
**Location:** `mcp_servers/outcome_server.py` (writes `claimed_outcomes`) vs. `agent/outcome_verifier.py` invoked from `agent/session_completion.py:1623` (writes `verified_outcomes`).
**Trigger:** Agent calls `record_outcome` on its very last turn, harness exits, worker invokes `_handle_dev_session_completion` — the Popoto save from `record_outcome` may not have flushed before the verifier reads.
**Data prerequisite:** `claimed_outcomes` must reflect the agent's most recent call before `OutcomeVerifier.verify_claimed` reads.
**State prerequisite:** Session is in `running` status when `record_outcome` writes; transitions to `completed` after the verifier runs.
**Mitigation:** `OutcomeVerifier.__init__` reloads the session via `AgentSession.get_by_id(session.id)` to get the freshest state, even if a stale instance was passed in. The Popoto save in `record_outcome` is synchronous (returns after Redis ACK), so by the time the harness exits and the worker re-enters the completion path, the write is durable. The reload step is the belt-and-suspenders: if the worker raced ahead and the harness's last call hasn't flushed, the reload picks it up. (Test: simulate a 50ms `record_outcome` flush delay, assert the verifier sees the entry.)

### Race 2: Two MCP `record_outcome` calls in the same turn append to `claimed_outcomes` concurrently.
**Location:** `mcp_servers/outcome_server.py` partial-save path.
**Trigger:** Agent calls `record_outcome` twice in rapid succession (multi-stage skill, retry path).
**Data prerequisite:** Both entries must persist; neither is dropped.
**State prerequisite:** N/A — both calls are agent-side, serialized through the harness's tool-call dispatcher.
**Mitigation:** The harness serializes tool calls (one PostToolUse per call), so back-to-back calls are sequential, not concurrent. The append-then-save pattern under serial calls is safe. Document this in the tool implementation. (Test: two calls in a single turn, assert both entries land.)

### Race 3: Worker pops a Dev session whose previous run set `failed_verification`, and the resume path tries to re-run the verifier on a stale `claimed_outcomes`.
**Location:** `worker/__main__.py` resume path; `agent/outcome_verifier.py`.
**Trigger:** PATCH session resume on a `failed_verification` session.
**Data prerequisite:** `claimed_outcomes` and `verified_outcomes` from the previous attempt persist on the session (append-only). The resume sees both.
**State prerequisite:** Resume creates a *new* AgentSession for PATCH (not a re-run of the failed Dev session) per existing semantics.
**Mitigation:** PATCH is a new session with its own `claimed_outcomes` (starts as None). The previous session's lists are read-only audit trail, not consumed by the new verifier. No data race. (Test: trigger `failed_verification`, run PATCH, assert PATCH's verifier reads only PATCH's claims.)

## No-Gos (Out of Scope)

- **Retroactive verification of completed sessions.** This plan does not back-fill `verified_outcomes` for the last 30 days of records. Verification fires forward only.
- **Retiring the `<!-- OUTCOME ... -->` comment block contract.** Both write paths coexist during the transition. Retirement is a separate cleanup issue once production data shows zero orphan comment-only OUTCOMEs.
- **LLM-mediated verification ("Haiku judges").** Verifiers are deterministic only. If a claim type cannot be deterministically checked, it does not get a verifier — it falls through to `unverifiable` and the classify_outcome result is honored.
- **Verifier registry expansion beyond the initial 4.** Adding verifiers for "memory saved," "knowledge doc indexed," "reflection ran," etc. is left for follow-up issues. This plan ships with PR URL, Telegram send, email send, file write.
- **Re-enqueue retry counters.** No `verification_attempts` field. The failure-consequence policy by session type (Dev → PATCH; Teammate/PM → escalate) replaces fazm's "retry on next tick."
- **Migration code for existing records.** `claimed_outcomes` and `verified_outcomes` are nullable. `_heal_descriptor_pollution` handles existing records generically. Memory: `feedback_field_backcompat_heal` (issues #1099 / #1172).
- **Dashboard UX redesign.** Adding `failed_verification` to the dashboard's status-color map is in scope. A bigger redesign that surfaces verification mismatches inline in the session detail view is a follow-up.
- **Cross-session verification** (e.g., "child Dev session ran TEST and parent PM should verify"). Verification stays within the session that made the claim.

## Update System

The update system (`scripts/remote-update.sh`, `.claude/skills/update/`) needs:

- **Step 4.6 (config validation)**: `bridge/config_validation.py` does not need changes — the new field additions are backward-compatible Popoto descriptors and do not affect `projects.json`.
- **MCP server registration**: `mcp_servers/outcome_server.py` is a new MCP server. `.mcp.json` needs a new entry. `scripts/update/run.py` (and the bundled npm install / Python venv steps) propagate this automatically once registered, but the registration itself is a one-time edit covered in this plan's Step by Step Tasks.
- **No new dependencies, services, or config files** beyond the MCP server registration. The verifier uses `gh` (already required) and Popoto/Redis (already required).
- **No migration for existing installations** — existing AgentSession records get `claimed_outcomes=None` / `verified_outcomes=None` automatically; the verifier no-ops on None.

## Agent Integration

The verifier is invoked from worker-side code (no agent-facing surface). The `record_outcome` tool is the agent-facing surface:

- **New MCP server**: `mcp_servers/outcome_server.py` exposing the `record_outcome` tool. Registered in `.mcp.json` so the agent's harness loads it.
- **Skill updates**: `/do-build`, `/do-test`, `/do-pr-review`, `/do-plan-critique` (the four skills that emit OUTCOME contracts today) gain a one-line instruction to call `record_outcome` immediately before emitting the comment block. The comment block stays for the transition.
- **No bridge import changes** — the bridge does not directly call the new code.
- **Integration test** in `tests/integration/test_outcome_verification_e2e.py` verifies the agent can actually invoke `record_outcome` end-to-end (loads MCP server, agent calls tool, session field populated). This test is the gate that catches an unwired tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/agent-session-outcome-verification.md` describing the verifier architecture, the `claimed_outcomes` / `verified_outcomes` fields, the four initial verifiers, the failure-consequence policy by session type, and the relationship to the `<!-- OUTCOME ... -->` contract during the transition.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-lifecycle.md` from "13-state" to "14-state" — add `failed_verification` row with description and routing rules.
- [ ] Update `docs/features/pipeline-state-machine.md` to note that `classify_outcome` and the verifier are *sibling* steps (verifier does not replace classify_outcome).
- [ ] Update `docs/features/build-output-verification.md` to cross-reference the generalized session-level verifier.
- [ ] Update `CLAUDE.md` 13-state reference (line ~336) to mention `failed_verification`.

### External Documentation Site
This repo does not use Sphinx/Read the Docs/MkDocs. Skip.

### Inline Documentation
- [ ] `record_outcome` MCP tool gets a comprehensive docstring including artifact-key conventions for each session_type.
- [ ] `OutcomeVerifier.verify_claimed` docstring documents the verdict aggregation rules.
- [ ] `_handle_dev_session_completion` docstring updated to note the verifier hook between classify and complete_stage.
- [ ] `models/agent_session.py` field-level comments for `claimed_outcomes` and `verified_outcomes` (writer, reader, contract).

## Success Criteria

- [ ] `claimed_outcomes` and `verified_outcomes` fields exist on `AgentSession` and persist via Popoto partial save.
- [ ] `record_outcome` MCP tool is callable by an agent inside a Dev session and writes to `claimed_outcomes`.
- [ ] `OutcomeVerifier` runs all four initial verifiers against a session's claims and writes `verified_outcomes`.
- [ ] When a Dev session claims a `pr_url` that does not exist (verifier verdict `mismatch`), the session ends in `status="failed_verification"` and the parent PM receives a steer message containing the verification evidence.
- [ ] When a Teammate session claims `email_sent: true` but `recent_sent_drafts` shows no matching entry, the session ends in `failed_verification` and the next session for that chat receives the failure context via `expectations`.
- [ ] When a verifier returns `unverifiable` (e.g., `gh` network error), the session proceeds as if `verified` and a `WARNING` is logged with the artifact-key.
- [ ] The Tier 0 OUTCOME comment-block parser writes through to `claimed_outcomes` so legacy emissions are still verified.
- [ ] `failed_verification` appears in the `models/session_lifecycle.py` terminal allowlist and in `docs/features/session-lifecycle.md`.
- [ ] `tests/integration/test_outcome_verification_e2e.py` asserts the end-to-end claim-verify-fail-escalate flow.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (data layer)**
  - Name: `data-builder`
  - Role: Add `claimed_outcomes` / `verified_outcomes` fields to `AgentSession`, add `failed_verification` to the lifecycle allowlist.
  - Agent Type: builder
  - Resume: true

- **Builder (verifier core)**
  - Name: `verifier-builder`
  - Role: Implement `agent/outcome_verifier.py` with the dispatch registry and the four initial verifiers.
  - Agent Type: builder
  - Resume: true

- **Builder (MCP tool)**
  - Name: `mcp-builder`
  - Role: Implement `mcp_servers/outcome_server.py` with the `record_outcome` tool; register in `.mcp.json`.
  - Agent Type: mcp-specialist
  - Resume: true

- **Builder (integration)**
  - Name: `integration-builder`
  - Role: Wire the verifier into `_handle_dev_session_completion` and `complete_transcript`. Update the four `/do-*` skills to call `record_outcome`. Update Tier 0 parser to write through to `claimed_outcomes`.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `test-engineer-1`
  - Role: Write unit tests for the verifier registry; write integration test `test_outcome_verification_e2e.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `final-validator`
  - Role: Run all verification commands; assert success criteria met; check docs created.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Create `docs/features/agent-session-outcome-verification.md`, update lifecycle/pipeline/CLAUDE.md docs.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 — Core: builder, validator, code-reviewer, test-engineer, documentarian, plan-maker, frontend-tester
Tier 2 — Specialists: mcp-specialist (used here), data-architect (potential follow-up)

## Step by Step Tasks

### 1. Add Popoto fields to AgentSession
- **Task ID**: build-data-fields
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_lifecycle.py` (passes existing tests with new fields), `tests/unit/test_outcome_verifier.py::test_session_fields_exist` (create)
- **Informed By**: spike-3 (worktree git-diff pattern works), Architectural Impact section
- **Assigned To**: data-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `claimed_outcomes = ListField(null=True)` and `verified_outcomes = ListField(null=True)` to `models/agent_session.py` near the `recent_sent_drafts` field (lines 220-228 cluster).
- Update the model docstring's "13 statuses" reference to "14 statuses" — add the new state.

### 2. Add failed_verification terminal status
- **Task ID**: build-lifecycle-status
- **Depends On**: build-data-fields
- **Validates**: `tests/unit/test_agent_session_lifecycle.py::test_terminal_allowlist_includes_failed_verification` (create)
- **Informed By**: spike-4 (session-end is the right hook)
- **Assigned To**: data-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `"failed_verification"` to the terminal allowlist in `models/session_lifecycle.py:282`.
- Verify `finalize_session` correctly handles the new value via the existing terminal-to-different-terminal guard.
- Update the 14-state docstring in `models/agent_session.py:114-138`.

### 3. Implement OutcomeVerifier
- **Task ID**: build-verifier-core
- **Depends On**: build-data-fields
- **Validates**: `tests/unit/test_outcome_verifier.py` (create — all dispatch + per-verifier tests)
- **Informed By**: spike-1 (gh pr view), spike-2 (recent_sent_drafts), spike-3 (worktree git-diff)
- **Assigned To**: verifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/outcome_verifier.py` with the `OutcomeVerifier` class.
- Implement the 4 verifiers: `_verify_pr_url`, `_verify_telegram_sent`, `_verify_email_sent`, `_verify_files_changed`.
- Implement the dispatch registry and aggregator.
- Each verifier wraps its work in `try/except` returning `(unverifiable, evidence)` on exception.
- 5s timeout per verifier (use `subprocess.run(timeout=5)` for `gh` / `git` calls).

### 4. Implement record_outcome MCP tool
- **Task ID**: build-mcp-tool
- **Depends On**: build-data-fields
- **Validates**: `tests/integration/test_outcome_mcp_server.py` (create)
- **Informed By**: Architectural Impact (MCP tool surface)
- **Assigned To**: mcp-builder
- **Agent Type**: mcp-specialist
- **Parallel**: true
- Create `mcp_servers/outcome_server.py` with the `record_outcome` tool.
- Validate `status ∈ {"success", "fail", "partial"}`, validate non-empty artifacts dict.
- Read `AGENT_SESSION_ID` from env, load session, append to `claimed_outcomes`, partial-save.
- Return `{session_id, entry_index}`.
- Register in `.mcp.json`.

### 5. Wire verifier into session completion
- **Task ID**: build-integration-completion
- **Depends On**: build-verifier-core, build-lifecycle-status
- **Validates**: `tests/integration/test_outcome_verification_e2e.py::test_dev_session_mismatch_routes_failed_verification` (create)
- **Informed By**: spike-4
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert `OutcomeVerifier` call in `agent/session_completion.py:1623` between `psm.classify_outcome()` and `psm.complete_stage()`.
- Implement the reconciliation rules (verdict × outcome × session_type → action).
- Wrap the verifier call in `try/except`; on verifier exception, log `WARNING` and proceed with `classify_outcome` result.
- Mirror the wiring in `complete_transcript()` for non-SDLC session types.

### 6. Write through Tier 0 parser to claimed_outcomes
- **Task ID**: build-tier0-passthrough
- **Depends On**: build-data-fields
- **Validates**: `tests/unit/test_pipeline_state_machine.py::test_outcome_block_writes_through_to_claimed_outcomes` (create)
- **Informed By**: Risk 3 (transition window safety)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_parse_outcome_contract` (or its caller in `classify_outcome`) so that a successfully parsed comment-block OUTCOME also appends to the session's `claimed_outcomes`.
- Idempotency: if the same OUTCOME content is already the last entry, skip the append.

### 7. Update /do-* skills to call record_outcome
- **Task ID**: build-skill-updates
- **Depends On**: build-mcp-tool
- **Validates**: `tests/integration/test_skill_outcome_recording.py` (create — one test per skill)
- **Informed By**: Solution → Technical Approach (skill updates)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/do-build/SKILL.md`, `.claude/skills/do-test/SKILL.md`, `.claude/skills/do-pr-review/SKILL.md`, `.claude/skills/do-plan-critique/SKILL.md` to call `record_outcome(...)` immediately before emitting the existing `<!-- OUTCOME ... -->` block.
- The instruction is additive — the comment block stays.

### 8. Validation pass
- **Task ID**: validate-verifier
- **Depends On**: build-verifier-core, build-mcp-tool, build-integration-completion, build-tier0-passthrough, build-skill-updates
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_outcome_verifier.py tests/integration/test_outcome_verification_e2e.py tests/integration/test_outcome_mcp_server.py tests/integration/test_skill_outcome_recording.py -v`
- Run `python -m ruff check agent/ models/ mcp_servers/`
- Run `python -m ruff format --check agent/ models/ mcp_servers/`
- Verify all Success Criteria met.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-verifier
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-session-outcome-verification.md`.
- Update `docs/features/README.md` index, `docs/features/session-lifecycle.md` (13→14 state), `docs/features/pipeline-state-machine.md`, `docs/features/build-output-verification.md`, `CLAUDE.md` lifecycle reference.
- Update `models/agent_session.py` docstring.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands from the Verification table.
- Verify Success Criteria all met (including documentation).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Verifier unit tests | `pytest tests/unit/test_outcome_verifier.py -v` | exit code 0 |
| E2E verification test | `pytest tests/integration/test_outcome_verification_e2e.py -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/ models/ mcp_servers/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ models/ mcp_servers/` | exit code 0 |
| New status in allowlist | `grep -c '"failed_verification"' models/session_lifecycle.py` | output > 0 |
| MCP tool registered | `grep -c 'outcome_server' .mcp.json` | output > 0 |
| Feature doc created | `test -f docs/features/agent-session-outcome-verification.md` | exit code 0 |
| Lifecycle doc updated | `grep -c 'failed_verification' docs/features/session-lifecycle.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Subject of verification — parent PM or work-doing session?** In `_handle_dev_session_completion`, the `parent` variable is the PM session and `agent_session` is the Dev session that just completed. The agent that emitted the OUTCOME / called `record_outcome` is the Dev session, so `claimed_outcomes` lives on `agent_session`. But the existing `psm.classify_outcome()` call operates on `parent`. The plan currently says `OutcomeVerifier(parent).verify_claimed(stage=current_stage)` in one place and `OutcomeVerifier(agent_session)` in another. Resolution: the verifier reads from the session that actually did the work (`agent_session` for SDLC, `session` itself for non-SDLC), but the *consequence* (calling `psm.fail_stage`) is applied to the parent's pipeline. Code should be `OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)` and the reconciliation step calls `psm.fail_stage()` on the parent's PSM.

2. **Stage field semantics for non-SDLC sessions.** Teammate sessions don't have an SDLC stage. Should `record_outcome` accept `stage=None` (current plan) or invent a synthetic stage like `"TEAMMATE_REPLY"`? The current plan's `stage=None` is simpler but means the verifier dispatches off `artifact_keys` rather than `stage`. Confirm this choice.

3. **Verifier registry: dispatch by artifact-key or by stage?** Current plan says artifact-key (more general). Stage-based dispatch is more constrained but easier to reason about. Suggest artifact-key but flag for review.

4. **Should `unverifiable` block the pipeline?** Current plan: no — proceed as if verified, log warning. Alternative: block on a configurable threshold (e.g., 3 consecutive `unverifiable` results from the same verifier → escalate). The threshold approach is more defensive but adds state. Pick one for v1.
