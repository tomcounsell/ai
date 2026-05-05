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

### spike-2: Does the Redis outbox expose enough state to verify "message sent" claims?
- **Assumption**: "We can scan `tg:outbox:*` / `email:outbox:*` and confirm that a message claimed in `claimed_outcomes` actually landed in the outbox before drainage."
- **Method**: code-read of `bridge/redundancy_filter.py`, `tools/send_message.py`, `agent/output_handler.py`, `bridge/email_relay.py`, search for outbox keys.
- **Finding (Telegram)**: The Telegram outbox (`telegram:outbox:{session_id}`, `agent/output_handler.py:564`) is *transient* — once drained by the bridge, the keys are gone. Verification cannot rely on scanning the outbox after the fact. Two durable alternatives exist on the session object: (a) `pm_sent_message_ids` (populated for PM-self-messages — see `models/agent_session.py:218`), (b) `recent_sent_drafts` (populated by `record_recent_sent_draft()` from the **Telegram path only** — see `agent/output_handler.py:584-586`, gated on `session.is_sdlc=True`).
- **Finding (email — REVISED, was previously over-claimed)**: `recent_sent_drafts` is **NOT populated by the email path**. Email sends route through `tools/send_message.py::_send_via_email` (lines 174-199), which writes the payload to the Redis queue `email:outbox:{session_id}` and returns. `bridge/email_relay.py::_process_one` (lines 204-241) drains that queue via SMTP and logs success at lines 212-218 but writes **no durable per-session record** of the successful delivery. After SMTP success the payload is gone. The original spike-2 conflated the two transports — this BLOCKER from prior critique.
- **Confidence**: high (Telegram path verified); confirmed gap (email path has no durable verification surface today)
- **Impact on plan**:
  - Verifier 2 (Telegram message sent) reads `recent_sent_drafts` for Teammate/Dev sessions and `pm_sent_message_ids` for PM sessions. No Redis scan needed.
  - **Email verification is OUT OF SCOPE for v1.** It would require either (a) a new write-time hook in `tools/send_message.py::_send_via_email` recording to a new `email_sent_log` ListField on `AgentSession` *before* the SMTP relay runs (catches "queued" not "delivered"), or (b) a new write in `bridge/email_relay.py` *post-SMTP-success* (line 212) to a durable Redis key like `email:sent:{session_id}` with TTL ≥ session-completion window (so the verifier can read it), then mirror that into the session model. Both options need their own spike. v2 follow-up issue at plan finalization (see No-Gos and the v1 phasing decision in spike-5 below).
  - Without a durable email record, the email verifier in v1 would always return false `mismatch` for legitimate sends — that is the BLOCKER the prior critique flagged. v1 ships without it; `email_sent` claims return `unverifiable` and proceed.

### spike-3: Can git-diff verify "file edit" claims for Dev sessions running in worktrees?
- **Assumption**: "`git -C .worktrees/{slug} log --oneline main..HEAD` plus `git diff --name-only main..HEAD` reliably reports what was actually committed by a Dev session."
- **Method**: code-read of `agent/worktree_manager.py`, existing build-output-verification feature.
- **Finding**: Yes — `docs/features/build-output-verification.md` already does this for `/do-build`. The pattern is proven. The verifier could reuse these commands, scoped to the session's `slug` field. **However:** per spike-5 phasing, files-changed verification ships in v2, not v1, because `docs/features/build-output-verification.md` already covers the BUILD-stage commit-count + diff sanity check — the marginal benefit of a session-level files-changed verifier in v1 is small.
- **Confidence**: high (the pattern works; the v1 phasing decision is a separate question)
- **Impact on plan**: v1 verifier registry does NOT include `files_changed`. Reserved for v2 as a thin wrapper around the existing build-output-verification commands. `files_changed` claims in v1 return `unverifiable` and proceed.

### spike-4: Where in the worker pipeline does the verifier fire — turn boundary, session end, or stage transition?
- **Assumption**: "Session-end (inside `_handle_dev_session_completion`, after `complete_transcript`, before `psm.complete_stage`) is the right hook point."
- **Method**: code-read of `agent/session_completion.py:1560-1740`, `agent/output_router.py`.
- **Finding**: Session-end is correct. Turn-boundary is too early — the agent may still be mid-work and emit a final OUTCOME on a later turn. Stage-transition is too late — the next stage already started on bad data. Session-end (specifically: between `psm.classify_outcome()` and `psm.complete_stage()`/`psm.fail_stage()` at lines 1623-1627) is the natural insertion point. **Consequence routing on `mismatch` is stage-dependent**: see spike-6 — `psm.fail_stage()` only works for stages that have a `("STAGE", "fail")` edge in `agent/pipeline_graph.py::PIPELINE_EDGES`.
- **Confidence**: high
- **Impact on plan**: Verifier runs inside `_handle_dev_session_completion`, gating the call to `complete_stage()`. For non-SDLC sessions (Teammate, direct-action PM), a parallel hook fires from `complete_transcript()` itself — see Technical Approach.

### spike-5: What is the right verifier set for v1 (scope phasing)?
- **Assumption**: "Shipping all four candidate verifiers in v1 (PR URL, Telegram send, email send, file write) is feasible inside Large appetite without amplifying blast radius."
- **Method**: cross-reference the spike-2 email-path gap, the existing `docs/features/build-output-verification.md` BUILD-stage coverage, and the Simplifier critique CONCERN.
- **Finding**: Four verifiers in v1 amplify ship risk because they touch four independent integration surfaces (GitHub API, Telegram path, SMTP relay, git/worktree). The PR-URL verifier addresses the user's stated pain ("PR opened" claims that aren't real, issue #1267 problem statement). The files-changed verifier duplicates the BUILD-stage check already shipped in `docs/features/build-output-verification.md` (commit-count + diff sanity inside `/do-build`). The email verifier is blocked on the spike-2 gap.
- **Confidence**: high
- **Impact on plan**: **v1 ships TWO verifiers — PR-URL and Telegram-send.** Both have durable read surfaces (`gh pr view` for PR; `recent_sent_drafts` / `pm_sent_message_ids` for Telegram). Email-send and files-changed move to follow-up issues — see No-Gos. The Architectural Impact section's bounded-writer property is preserved since v1's surface is now narrower. Phasing reduces v1's blast radius from 3 unproven verifiers to 0; the verifier *registry* remains extensible so v2/v3 can add verifiers without re-architecting the dispatch.

### spike-6: Which pipeline stages support `psm.fail_stage()` for verification mismatch routing?
- **Assumption**: "`psm.fail_stage(stage)` cleanly routes to PATCH for any stage where the agent claims success but the verifier disagrees."
- **Method**: code-read of `agent/pipeline_graph.py::PIPELINE_EDGES` (lines 40-59).
- **Finding**: Only **THREE stages** have a `("STAGE", "fail")` edge today: `("CRITIQUE", "fail") → "PLAN"`, `("TEST", "fail") → "PATCH"`, `("REVIEW", "fail") → "PATCH"`. There are NO `fail` edges for `ISSUE`, `PLAN`, `BUILD`, `DOCS`, `MERGE`, or `PATCH`. Calling `psm.fail_stage("BUILD")` invokes `get_next_stage("BUILD", "fail")` which falls through the `success` fallback (`agent/pipeline_graph.py:150-151`) and routes to `TEST` anyway — so the next-stage routing partially "works," but the *outcome* recorded for BUILD becomes `fail` and the PM sees inconsistent state ("BUILD failed but pipeline advanced to TEST"). Worse, if the fallback is later removed or tightened, the pipeline silently terminates. **Conclusion: relying on `fail_stage` for stages without an explicit `fail` edge is fragile and indirectly contradicts the issue's call for "robust" verification consequences.**
- **Confidence**: high
- **Impact on plan**: The verification consequence policy is stage-aware. Two routes:
  - **Stages with an explicit `fail` edge** (`CRITIQUE`, `TEST`, `REVIEW`) → call `psm.fail_stage(stage)` exactly as the prior plan suggested. The existing edge handles routing.
  - **Stages WITHOUT a `fail` edge** (`ISSUE`, `PLAN`, `BUILD`, `DOCS`, `MERGE`) → DO NOT call `psm.fail_stage`. Instead: (a) write `verified_outcomes` with the mismatch evidence, (b) `finalize_session(session, status="failed_verification", reason=...)` — terminating the *session* without trying to advance the pipeline, (c) steer the parent PM with the verification mismatch context (the PM decides whether to re-spawn, escalate to human, or pause). This is intentional: there is no automatic recovery from a hallucinated PR URL — the PM must receive the evidence and decide the next move.
  - The reconciliation step (Solution → Failure consequence policy) explicitly enumerates which stages take which route. Adding a new `("BUILD", "fail")` edge is **out of scope** for this plan — it conflates "verification failed" with "stage failed" and introduces routing decisions (BUILD-fail → PATCH? back to PLAN? escalate?) that deserve their own design pass.

## Data Flow

End-to-end flow for a verified Dev BUILD session (v1: PR-URL verifier only — see spike-5):

1. **Entry point**: PM session creates a Dev session via `valor-session create --role dev --slug {slug} --message "..."`. Session enqueued; worker picks it up.
2. **Execution**: Worker spawns CLI harness; the Dev agent runs `/do-build`. The skill instructs the agent to call `record_outcome(stage="BUILD", status="success", artifacts={"pr_url": "..."})` *in addition to* emitting the existing `<!-- OUTCOME ... -->` comment block (transition-window dual write — see Risk 3).
3. **`record_outcome` MCP tool**: Writes `claimed_outcomes` (a `ListField` of dicts on the session, append-only across the session lifetime) via `session.save(update_fields=["claimed_outcomes", "updated_at"])`. Each entry is `{ts, stage, status, artifacts, raw_text}`. Multiple calls in one session append; the verifier consumes the last entry per stage. Idempotency: hash `(stage, status, sorted_artifacts_json)` and skip the append if the last entry has the same hash — preventing duplicate entries from the dual-write path (see Race 2).
4. **Session completion**: Harness exits; `_handle_dev_session_completion()` runs.
5. **Classify**: `psm.classify_outcome(stage, stop_reason, result)` runs as today (Tier 0/1/2). Result written to a local var.
6. **Verify** (new): `OutcomeVerifier(agent_session).verify_claimed(stage)` reads `agent_session.claimed_outcomes`, finds the entry for `stage`, dispatches per-artifact-type verifiers (v1: PR URL, Telegram send), aggregates a verdict (`verified` / `mismatch` / `unverifiable`), and writes `verified_outcomes` (parallel `ListField`) on the session. (Resolves former Open Question 1: the verifier reads from the session that did the work — `agent_session` for SDLC, `session` itself for non-SDLC; the consequence is applied to the parent's PSM.)
7. **Reconcile** (stage-aware — see spike-6):
   - If `classify_outcome == "success"` AND verifier says `verified` → `psm.complete_stage(stage)` on parent.
   - If `success` but `mismatch` AND `stage ∈ {"CRITIQUE", "TEST", "REVIEW"}` (stages with explicit `fail` edges in `PIPELINE_EDGES`) → `psm.fail_stage(stage)` on parent (existing edge handles routing) AND `finalize_session(agent_session, "failed_verification", reason=evidence_summary)`.
   - If `success` but `mismatch` AND `stage ∈ {"ISSUE", "PLAN", "BUILD", "DOCS", "MERGE"}` (stages WITHOUT `fail` edges) → DO NOT call `psm.fail_stage` (would route silently via the `success` fallback at `agent/pipeline_graph.py:150-151`, producing inconsistent state). Instead: write `verified_outcomes`, `finalize_session(agent_session, "failed_verification", reason=...)`, set `parent.expectations` to the human-readable mismatch summary, and steer the parent PM with the verification evidence. The PM decides whether to re-spawn, escalate, or pause.
   - If `success` but `unverifiable` (e.g., `gh` network error, claimed artifact missing) → log a structured `WARNING` with the artifact-key, *still* call `complete_stage()` (don't punish the agent for transient infrastructure flake), but flag `verified_outcomes[-1].confidence = "low"` so the dashboard can show it.
8. **Output**: Stage comment posted to GitHub issue includes `(verified)` or `(verification mismatch: <reason>)` suffix. PM steered with the verified outcome.

For non-SDLC sessions (Teammate replying to inbound email, PM running `valor-telegram send` directly):

1. **Entry point**: Bridge enqueues a Teammate session for inbound email; worker picks it up.
2. **Execution**: Agent runs, calls `record_outcome(stage=None, status="success", artifacts={"telegram_sent": true, "telegram_message_id": 123})`. (Note: v1 does NOT verify `email_sent` claims — see spike-2 finding and No-Gos. Teammate sessions that reply via email cannot have their reply verified in v1.)
3. **Session completion**: `complete_transcript()` runs.
4. **Verify**: A new hook in `complete_transcript()` (parallel to the SDLC path) calls `OutcomeVerifier(session).verify_claimed(stage=None)` for sessions where `claimed_outcomes` is non-empty AND `session_type != "dev"`.
5. **Reconcile**: On `mismatch`, `finalize_session(status="failed_verification", reason=...)`. The drafter for the next session in the same chat gets the failure context via `expectations`. Additionally (per User critique CONCERN), the bridge sends a one-line system message to the *originating chat* on the next inbound: "We attempted to reply but verification failed; retrying." Wired through `bridge/email_relay.py` and the inbound Telegram handler — see Failure consequence policy.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|--------------------------------|
| PR #667 (Tier 0 OUTCOME contract) | Parses `<!-- OUTCOME {...} -->` comment blocks; returns the agent's claimed status verbatim. | **Self-attestation only** — never checks the claim against reality. The agent's `pr_url` could be fabricated. Stage-scoped (writes to parent PM's `stage_states`, not to the executing session). Silently falls through to Tier 2 text patterns when malformed. No path for *non-SDLC* sessions. |
| PR #667 (deferred verification rationale) | Verification was *deliberately* deferred from #667. | Verification was deferred for two stated reasons in the PR design discussion (per `gh pr view 667`): (a) **latency cost** — adding `gh pr view` per stage adds 0.5-1.5s × 8 stages ≈ 10s pipeline tax, viewed as unjustified at the time; (b) **transition risk** — landing the typed contract first lets the parser stabilize before adding verification, so a verifier bug doesn't compound a parser bug. **Why this plan reverses that decision:** the agent-lying-about-completion failure mode is now common enough (issue #1267 problem statement enumerates concrete examples — "PR opened" claims that aren't real, "tests passed" claims with no commits) to justify the cost. We also mitigate (a) with per-verifier 5s timeout (Risk 1) and (b) with the v1 phasing decision in spike-5 (ship 2 verifiers, not 4). |
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
| `gh` authenticated | `gh auth status` | Verifier 1 (PR-URL) calls `gh pr view` to confirm PR existence |
| Redis reachable | `redis-cli ping` | Verifier 2 (Telegram-send) reads `recent_sent_drafts` / `pm_sent_message_ids` (Popoto-backed) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/agent-session-outcome-verification.md`

## Solution

### Key Elements

- **`AgentSession.claimed_outcomes` (ListField, nullable)**: Append-only list of `{ts, stage, status, artifacts, raw_text}` dicts. Written by the new `record_outcome` MCP tool and (for the transition window) by the existing Tier 0 OUTCOME parser. One entry per `record_outcome` call; verifier consumes the last entry per stage. Idempotency: dual-write paths (explicit `record_outcome` + comment-block parse) hash `(stage, status, sorted_artifacts_json)` and skip duplicate appends (see Race 2).
- **`AgentSession.verified_outcomes` (ListField, nullable)**: Append-only list of `{ts, stage, verdict, evidence, confidence}` dicts. Verdict is `verified` / `mismatch` / `unverifiable`. Confidence is `high` / `medium` / `low` reflecting how reliably the per-artifact verifier could check the claim.
- **`agent/outcome_verifier.py`**: New module. `OutcomeVerifier(agent_session).verify_claimed(stage=None)` reads `claimed_outcomes`, dispatches per-artifact verifiers from a registry, aggregates a verdict, writes `verified_outcomes`, returns the verdict for the caller's reconciliation step. The registry maps artifact-key (v1: `pr_url`, `telegram_sent`) to a verifier function. **Initial v1 registry: 2 verifiers** (per spike-5). Email and files-changed verifiers are explicit follow-ups (see No-Gos). The verifier reads from the session that did the work; consequence (fail/finalize/steer) is applied to the parent's PSM (see Reconcile step in Data Flow).
- **`record_outcome` MCP tool**: Exposed through `mcp_servers/outcome_server.py`. Replaces the comment-block hack with a typed call. Writes to `session.claimed_outcomes` via Popoto partial save. Returns the session_id and entry index so the agent can confirm the write.
- **`failed_verification` terminal status**: New value in `models/session_lifecycle.py` `TERMINAL_STATUSES` frozenset (line 61). Set by `OutcomeVerifier` consequence path when verdict is `mismatch`. Routed by session type and stage (see spike-6): Dev on stages with `fail` edges → `psm.fail_stage` + `finalize_session`; Dev on stages without `fail` edges → `finalize_session` + steer parent; Teammate → `finalize_session` + chat-level apology; PM → escalate via `valor-telegram send`.
- **Verifier dispatch hook in `_handle_dev_session_completion`**: After `psm.classify_outcome()`, before `psm.complete_stage()`, the new verifier runs. The reconciliation rules (verifier verdict × classify_outcome result × session_type × stage's `fail`-edge support) determine whether the stage is completed, failed, or whether the whole session is finalized as `failed_verification`.
- **Observability instrumentation** (per Operator critique CONCERN): The verifier core records three metrics via `analytics/collector.py::record_metric` (already imported at `agent/pipeline_state.py:144`):
  - `outcome_verifier.verdict{stage,session_type,verdict}` — counter, fired once per `verify_claimed` call.
  - `outcome_verifier.mismatch_artifact{artifact_key}` — counter, fired once per artifact mismatch.
  - `outcome_verifier.duration_ms{verifier}` — histogram, fired once per per-artifact verifier call.
  Plus a Sentry alert on `mismatch` verdict rate > 5% over 1h, and a dashboard panel in `ui/app.py` exposing `AgentSession.query.filter(status="failed_verification").count()` so on-call can answer "how often is the agent lying this week?" without grepping logs.

### Flow

**Dev session BUILD path (v1 — PR-URL only):**
PM creates Dev session → Worker pops session → CLI harness runs `/do-build` → Agent calls `record_outcome("BUILD", "success", {"pr_url": "..."})` → Harness exits → `_handle_dev_session_completion` → `psm.classify_outcome()` returns `success` → `OutcomeVerifier(agent_session).verify_claimed("BUILD")` runs `gh pr view <url>` → verified → `psm.complete_stage("BUILD")` on parent → PM steered → Pipeline advances to TEST.

**Dev session BUILD with hallucinated PR URL (no `("BUILD","fail")` edge — see spike-6):**
... → `OutcomeVerifier(agent_session).verify_claimed("BUILD")` runs `gh pr view <url>` → `gh` exits 1, "no pull requests found" → verdict = `mismatch` → `verified_outcomes` written with `evidence={"pr_url_check": "no pull request at <url>"}` → BUILD has no `fail` edge in `PIPELINE_EDGES`, so do NOT call `psm.fail_stage("BUILD")` → `finalize_session(agent_session, "failed_verification", reason="claimed pr_url not found")` → set `parent.expectations = "Dev session claimed PR <url> but verification found no PR. Decide whether to re-spawn Dev, escalate, or pause."` → steer parent PM with the verification mismatch context → PM evaluates and decides next move (re-spawn Dev with revised instructions, or escalate to human via Telegram).

**Dev session TEST with hallucinated `tests_passed` (has `("TEST","fail")` edge):**
... → verifier runs (no v1 verifier for `tests_passed` itself; v1 will return `unverifiable`, proceed as if verified). Note: this is intentional v1 scope — the `tests_passed` artifact-key has no durable verification surface today. The PR-URL verifier still catches the case where a TEST session falsely claims it opened a follow-up PR.

**Teammate session sending a Telegram reply (NOT email — see No-Gos):**
Bridge enqueues Teammate → worker pops → agent runs, calls `record_outcome(stage=None, status="success", artifacts={"telegram_sent": true, "telegram_message_id": 123})` → `complete_transcript()` runs verifier → verifier checks `recent_sent_drafts` for an entry within the last 60s → if not found, verdict = `mismatch` → `finalize_session(status="failed_verification")` → drafter for the next inbound from the originating chat receives the failure context via `expectations`. **User-facing consequence (per User critique CONCERN):** on the next inbound from the same chat, the bridge prepends a one-line apology to the draft: "We attempted to reply but verification failed; retrying." Wired through `bridge/telegram_bridge.py` inbound handler reading `session.status == "failed_verification"`. Skip if the user is configured for silent retries (per-chat config flag, default off).

**Teammate session sending an email reply (v1 NOT verified):**
Bridge enqueues Teammate → worker pops → agent runs, calls `record_outcome(stage=None, status="success", artifacts={"email_sent": true})` → `complete_transcript()` runs verifier → no `email_sent` verifier in v1 registry → verdict = `unverifiable` with `evidence={"reason": "no_verifier_for_artifact_key"}` → log `WARNING` and proceed → session ends `completed`. **This is the v1 known gap** — email replies remain unverified until the durable email-send-log lands (see No-Gos and the v2 follow-up issue).

### Technical Approach

- **Field additions to `AgentSession`** (`models/agent_session.py`): `claimed_outcomes = ListField(null=True)`, `verified_outcomes = ListField(null=True)`. Both nullable; existing records' `_heal_descriptor_pollution` walks fields generically (memory: `feedback_field_backcompat_heal`, issues #1099 / #1172). No parallel-run migration. No back-compat code.
- **New terminal status `failed_verification`** (`models/session_lifecycle.py`): added to the `TERMINAL_STATUSES` frozenset at line 61 (NOT line 282 — that line is the error-message string `f"finalize_session() requires a terminal status..."`; without modifying line 61 the lifecycle module rejects the new value before any allowlist check succeeds). The change is `TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled", "failed_verification"})`. `finalize_session(status="failed_verification", reason=...)` is the sole writer. The lifecycle module's terminal-to-different-terminal guard already handles the precedence (a session already `completed` cannot be flipped to `failed_verification` after the fact — that's a separate retroactive-audit concern, out of scope; see No-Gos).
- **`record_outcome` MCP tool** (`mcp_servers/outcome_server.py`, new file): single tool exposed: `record_outcome(stage: str | None, status: str, artifacts: dict, notes: str = "")`. Validates `status ∈ {"success", "fail", "partial"}` and at least one artifact key. Reads the session_id from env (`AGENT_SESSION_ID`), loads the session, appends to `claimed_outcomes` (with idempotency hash on `(stage, status, sorted_artifacts_json)`), partial-saves. Returns `{session_id, entry_index}` to the agent.
- **`agent/outcome_verifier.py`** (new file):
  ```python
  class OutcomeVerifier:
      def __init__(self, session: AgentSession):
          # Reload session via AgentSession.get_by_id(session.id) for freshness — see Race 1
          ...
      def verify_claimed(self, stage: str | None = None) -> str:
          """Return 'verified' | 'mismatch' | 'unverifiable'."""
  ```
  Dispatch registry maps artifact-key → verifier callable. Each verifier returns `(verdict, evidence_dict)`. Aggregator: any artifact `mismatch` → session `mismatch`; all `verified` → session `verified`; mixed `verified`/`unverifiable` → session `unverifiable` (do not punish the agent for infra flake). Each `verify_claimed` call records the three observability metrics listed in Key Elements before returning.
- **v1 verifier registry (2 verifiers — per spike-5 phasing)**:
  - `pr_url` → `gh pr view <url> --json state,number,headRefName` with 5s timeout. Exit 0 AND `state ∈ {"OPEN", "MERGED"}` (Risk 5) = `verified`, exit 1 with "no pull requests" = `mismatch`, other (network/auth) = `unverifiable`.
  - `telegram_sent` → contract: "verifier confirms AT LEAST ONE matching send within the last `RECENT_DRAFTS_N` saves" (per Adversary critique CONCERN — `recent_sent_drafts` is FIFO-capped at 3 entries, 500-char-truncated). For `session_type == "pm"`, check `pm_sent_message_ids` non-empty within the last 60s. For other session types, check `recent_sent_drafts` for an entry within the last 60s. Match by `ts` window + (when supplied) chat_id. `verified` if at least one matching entry is found, `mismatch` if `recent_sent_drafts` is non-empty but no match within 60s, `unverifiable` if `recent_sent_drafts` is None / empty (cannot distinguish "didn't send" from "sent more than 3 times and got pushed out"). Document the contract on the verifier function: high-volume sessions claiming many sends return `unverifiable` rather than false `mismatch`. A future v2 verifier may switch to a dedicated send-log when one exists.
- **OUT OF SCOPE for v1 (registry placeholders) — see No-Gos**:
  - `email_sent` — blocked on spike-2 finding (no durable email-send record). Ships in v2 after the email-send-log lands.
  - `files_changed` — duplicates `docs/features/build-output-verification.md` BUILD-stage check; the verifier-registry hook is reusable but the v1 ship doesn't include it.
- **Hook in `_handle_dev_session_completion`** (`agent/session_completion.py:1623`): between `outcome = psm.classify_outcome(...)` and `psm.complete_stage(...)`, insert:
  ```python
  from agent.outcome_verifier import OutcomeVerifier
  # Verifier reads from the session that did the work (resolves former Open Question 1)
  verifier_verdict = OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)
  if outcome == "success" and verifier_verdict == "mismatch":
      reconcile_failed_verification(parent, agent_session, current_stage, evidence=...)
      outcome = "fail"  # consumed locally; psm.fail_stage may or may not be called — see reconcile fn
  ```
  `reconcile_failed_verification` enforces the stage-aware policy (see spike-6 and Failure consequence policy below). The whole verifier call is wrapped in `try/except`; on verifier exception, log `WARNING` and proceed with the original `classify_outcome` result (Risk 2).
- **Hook in `complete_transcript()`** (for non-SDLC sessions): mirror invocation, gated on `session_type != "dev"` and `claimed_outcomes` non-empty. On `mismatch`, call `finalize_session(session, "failed_verification", reason=evidence_summary)` and set the user-facing apology hint described in Failure consequence policy.
- **Skill updates**: `/do-build`, `/do-test`, `/do-pr-review`, `/do-plan-critique` learn to call `record_outcome` (via the MCP tool) *in addition to* emitting the comment block. The comment block stays during the transition; the Tier 0 parser writes through to `claimed_outcomes` (with the idempotency hash) so legacy emissions are still captured. After 30 days of clean operation the comment block can be retired (separate cleanup issue, out of scope here).
- **Failure consequence policy** (stage-aware — resolves BLOCKER 2 from prior critique):
  - **Dev session, verdict `mismatch`, stage HAS a `("STAGE", "fail")` edge in `PIPELINE_EDGES`** (`CRITIQUE`, `TEST`, `REVIEW`):
    - Call `psm.fail_stage(stage)` on parent — existing edge handles routing (CRITIQUE → PLAN, TEST → PATCH, REVIEW → PATCH).
    - `finalize_session(agent_session, "failed_verification", reason=evidence_summary)`.
    - The next session in the cycle reads the verification diff from `verified_outcomes[-1].evidence` via the PM steer message.
  - **Dev session, verdict `mismatch`, stage HAS NO `fail` edge** (`ISSUE`, `PLAN`, `BUILD`, `DOCS`, `MERGE`):
    - DO NOT call `psm.fail_stage(stage)` — would route silently via the `success` fallback at `agent/pipeline_graph.py:150-151`, producing inconsistent state ("BUILD failed but pipeline advanced to TEST").
    - Write `verified_outcomes` with the mismatch evidence.
    - `finalize_session(agent_session, "failed_verification", reason=evidence_summary)`.
    - Set `parent.expectations` to a human-readable mismatch summary (truncated to 500 chars).
    - Steer parent PM with the verification evidence (`pm_session.queued_steering_messages.append(...)` via the standard steering helper).
    - PM decides whether to re-spawn a Dev with revised instructions, escalate to a human, or pause. (Adding a `("BUILD", "fail")` edge is intentionally out of scope — see No-Gos.)
  - **Teammate session, verdict `mismatch`** (Telegram path; email is `unverifiable` in v1 — see Flow):
    - `finalize_session(session, "failed_verification", reason=...)`.
    - Set `expectations` for the next session in the chat.
    - **User-facing consequence:** on the next inbound from the originating chat, the bridge prepends a one-line system message to the draft: "We attempted to reply but verification failed; retrying." Wired through `bridge/telegram_bridge.py` inbound handler (reads `session.status == "failed_verification"` for the chat's most-recent prior session). Per-chat config flag for silent-retry, default off.
    - No auto-retry of the original send — Teammate sends are user-facing and a re-send risks duplicates.
  - **PM session, verdict `mismatch`**:
    - Escalate to human via `valor-telegram send` to the project's primary chat with the verification evidence.
    - `finalize_session(session, "failed_verification", reason=...)`.
    - No auto-retry.
  - **Any session, verdict `unverifiable`** (e.g., `gh` network error, missing artifact, no verifier registered for an artifact-key):
    - Log a `WARNING` with the artifact-key and reason.
    - Write `verified_outcomes` with `confidence="low"` and `evidence={"reason": ...}`.
    - Proceed as if `verified` — the classification path's outcome is honored.
    - This avoids cascading infra-flake into pipeline failures.

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
- [ ] When a Dev session fails verification on a stage WITHOUT a `fail` edge (e.g., BUILD), assert `psm.fail_stage` is NOT called and the parent's `expectations` field is set with the mismatch summary. Integration test in `test_outcome_verification_e2e.py`.
- [ ] When a Dev session fails verification on a stage WITH a `fail` edge (TEST/REVIEW), assert `psm.fail_stage` IS called AND `finalize_session(status="failed_verification")` runs. Integration test in `test_outcome_verification_e2e.py`.
- [ ] When a Teammate session fails Telegram verification, assert the next inbound from the same chat receives a one-line apology in the draft. Integration test in `test_outcome_verification_e2e.py` (mocking the bridge inbound handler).

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py::test_classify_outcome_*` (12 tests at lines 628-1139) — UPDATE: the OUTCOME contract path is unchanged, but tests must assert that the new verifier is *not* called from `classify_outcome` (verification is a sibling step, not a sub-step). Add 3 new tests asserting the verifier integrates correctly with `_handle_dev_session_completion`.
- [ ] `tests/integration/test_parent_child_round_trip.py` — UPDATE: existing assertions about `psm.complete_stage()` being called need an extension asserting `verified_outcomes` is also populated when `claimed_outcomes` is set.
- [ ] `tests/unit/test_agent_session_lifecycle.py` — UPDATE: the terminal-status allowlist test needs `failed_verification` added; the terminal-to-different-terminal guard test needs a new case for `failed_verification`. Add a test asserting `finalize_session(session, "failed_verification", reason=...)` actually persists (verifies BLOCKER 3 fix — `TERMINAL_STATUSES` frozenset at line 61 is correctly extended).
- [ ] `tests/unit/test_pipeline_state_machine.py::TestStageStates` and the OUTCOME parsing tests — UPDATE: assert that comment-block-derived OUTCOMEs *also* land in `claimed_outcomes` (transition-window invariant: the two write paths converge); assert the idempotency hash prevents duplicate appends when both the parser and an explicit `record_outcome` call write the same content.
- [ ] New file `tests/unit/test_outcome_verifier.py` — REPLACE (create): one test per v1 verifier (2 verifiers × {verified, mismatch, unverifiable} = 6 base tests). Plus aggregation tests, exception-path tests, stage-mismatch tests, and the contract test "Telegram verifier returns `unverifiable` rather than false `mismatch` when `recent_sent_drafts` is None or capped".
- [ ] New file `tests/integration/test_outcome_verification_e2e.py` — REPLACE (create): full path test for the BLOCKER 2 fix — Dev session emits a hallucinated `pr_url` for BUILD (no `fail` edge), worker runs verifier, asserts `psm.fail_stage` is NOT called, session ends in `failed_verification`, parent.expectations is populated, PM is steered with mismatch context. Plus a parallel test for TEST stage (has `fail` edge): asserts `psm.fail_stage` IS called and routes to PATCH. This is the acceptance-criterion test (Issue #1267 last bullet).
- [ ] `tests/integration/test_agent_session_queue_session_type.py` — UPDATE: Teammate session test needs a case for `failed_verification` routing (escalates via Telegram, does NOT re-enqueue).
- [ ] New file `tests/integration/test_outcome_mcp_server.py` — REPLACE (create): assert the `record_outcome` MCP tool is callable end-to-end, validates inputs, writes `claimed_outcomes`, returns `{session_id, entry_index}`. (Already listed in Step 4 Validates.)
- [ ] New file `tests/integration/test_skill_outcome_recording.py` — REPLACE (create): one test per updated skill (`/do-build`, `/do-test`, `/do-pr-review`, `/do-plan-critique`) asserting `record_outcome` is invoked when the skill emits an OUTCOME.

## Rabbit Holes

- **Don't try to verify everything.** The v1 registry ships 2 verifiers (PR URL, Telegram send) per spike-5. Adding a verifier per claim type the agent might make is an open-ended trap. Each new verifier has its own freshness/auth/timeout edge cases. Ship 2, learn, expand to 3-4 in v2.
- **Don't use Haiku/LLM for verification.** Tempting for "fuzzy" claims like "I improved the docs" — but LLM-mediated verification reintroduces self-attestation at one remove. Stick to deterministic checks. CLAUDE.md Principle 3 ("intelligent systems over rigid patterns") does NOT mean adding LLMs everywhere — it means using LLMs *where deterministic checks are infeasible*, which is not the case for the v1 artifact types.
- **Don't retroactively verify completed sessions.** A "go back and verify the last 30 days of completed sessions" cleanup is tempting but huge. Verification fires forward only; existing records keep their status. (If we later want a retroactive audit tool, it's a separate plan.)
- **Don't add a `("BUILD", "fail")` edge** (or `PLAN`/`ISSUE`/`DOCS`/`MERGE` fail edges) **as part of this plan.** Adding a fail edge conflates "verification mismatch" with "stage failure" and forces a routing decision (BUILD-fail → PATCH? back to PLAN? escalate?) that deserves its own design pass. The plan handles missing fail edges by `finalize_session("failed_verification")` + steer parent — no edge addition required. If we later decide BUILD should auto-route to PATCH on verification mismatch, that's a separate plan.
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

### Risk 6: `recent_sent_drafts` FIFO cap creates false `mismatch` for high-volume sessions.
**Impact:** `recent_sent_drafts` is FIFO-capped at `RECENT_DRAFTS_N` (default 3) per `models/agent_session.py:228`. A session that legitimately sends 5 messages would only retain evidence of the last 3. Verifier dispatched against a 60s window for any of the older 2 messages would return `mismatch` for valid sends.
**Mitigation:** The Telegram verifier contract is "verifier confirms AT LEAST ONE matching send within the last `RECENT_DRAFTS_N` saves." Documented on the verifier function and tested explicitly. For sessions claiming exact send counts ("I sent 5 messages"), the verifier returns `unverifiable` rather than false `mismatch` — preventing the FIFO cap from punishing legitimate behavior. A future v2 verifier may switch to a dedicated send-log when one exists.

### Risk 7: Verification metrics flood Sentry with low-signal mismatches in the first week of rollout.
**Impact:** The Sentry alert "mismatch verdict rate > 5% over 1h" may fire on agent learning curve as skills migrate to `record_outcome`. Operators get alarm fatigue.
**Mitigation:** The alert is gated to fire only after the first week of production data (config-driven). Before that, mismatches log to Sentry as `info` events, not alerts. After the warm-up window, escalate to `warning` (5%) and `error` (15%). Reviewable via the dashboard panel exposing `failed_verification` count.

## Race Conditions

### Race 1: `record_outcome` and `_handle_dev_session_completion` write to the same session concurrently.
**Location:** `mcp_servers/outcome_server.py` (writes `claimed_outcomes`) vs. `agent/outcome_verifier.py` invoked from `agent/session_completion.py:1623` (writes `verified_outcomes`).
**Trigger:** Agent calls `record_outcome` on its very last turn, harness exits, worker invokes `_handle_dev_session_completion` — the Popoto save from `record_outcome` may not have flushed before the verifier reads.
**Data prerequisite:** `claimed_outcomes` must reflect the agent's most recent call before `OutcomeVerifier.verify_claimed` reads.
**State prerequisite:** Session is in `running` status when `record_outcome` writes; transitions to `completed` after the verifier runs.
**Mitigation:** `OutcomeVerifier.__init__` reloads the session via `AgentSession.get_by_id(session.id)` to get the freshest state, even if a stale instance was passed in. The Popoto save in `record_outcome` is synchronous (returns after Redis ACK), so by the time the harness exits and the worker re-enters the completion path, the write is durable. The reload step is the belt-and-suspenders: if the worker raced ahead and the harness's last call hasn't flushed, the reload picks it up. (Test: simulate a 50ms `record_outcome` flush delay, assert the verifier sees the entry.)

### Race 2: Two MCP `record_outcome` calls in the same turn append to `claimed_outcomes` concurrently.
**Location:** `mcp_servers/outcome_server.py` partial-save path.
**Trigger:** Agent calls `record_outcome` twice in rapid succession (multi-stage skill, retry path), OR — more commonly during the transition window — `record_outcome` is called once and the agent's same turn ALSO emits a `<!-- OUTCOME ... -->` comment block which the Tier 0 passthrough (Step 6) would also append to `claimed_outcomes`, yielding two entries for the same logical outcome.
**Data prerequisite:** Both entries must persist when they represent distinct logical outcomes; duplicate (stage, status, artifacts) tuples must NOT produce a second entry.
**State prerequisite:** N/A — both calls are agent-side, serialized through the harness's tool-call dispatcher.
**Mitigation:** Two-pronged.
  - **Concurrency**: The harness serializes tool calls (one PostToolUse per call), so back-to-back calls are sequential, not concurrent. The append-then-save pattern under serial calls is safe.
  - **Duplicate suppression**: The `record_outcome` MCP tool AND the Tier 0 parser both compute an idempotency hash on `(stage, status, sorted_artifacts_json)` before appending. If the last entry in `claimed_outcomes` has the same hash, skip the append. This handles the dual-write case where both the explicit MCP call and the comment-block parse fire for the same logical outcome.
  - (Tests: two distinct calls in a single turn, assert both entries land. One MCP call followed by an identical comment-block parse, assert exactly ONE entry exists.)

### Race 3: Worker pops a Dev session whose previous run set `failed_verification`, and the resume path tries to re-run the verifier on a stale `claimed_outcomes`.
**Location:** `worker/__main__.py` resume path; `agent/outcome_verifier.py`.
**Trigger:** PATCH session resume on a `failed_verification` session.
**Data prerequisite:** `claimed_outcomes` and `verified_outcomes` from the previous attempt persist on the session (append-only). The resume sees both.
**State prerequisite:** Resume creates a *new* AgentSession for PATCH (not a re-run of the failed Dev session) per existing semantics.
**Mitigation:** PATCH is a new session with its own `claimed_outcomes` (starts as None). The previous session's lists are read-only audit trail, not consumed by the new verifier. No data race. (Test: trigger `failed_verification`, run PATCH, assert PATCH's verifier reads only PATCH's claims.)

## No-Gos (Out of Scope)

- **Email-send verification (v1).** Per spike-2, no durable per-session record exists for successful SMTP delivery. Building one is a separate spike: either a write-time hook in `tools/send_message.py::_send_via_email` (catches "queued" not "delivered"), or a post-SMTP-success write in `bridge/email_relay.py` to a durable Redis key like `email:sent:{session_id}` (TTL ≥ session-completion window). v1 ships without it; create follow-up issue at plan finalization. Until then, `email_sent` claims return `unverifiable` and proceed.
- **Files-changed verification (v1).** `docs/features/build-output-verification.md` already provides the BUILD-stage commit-count + diff sanity check inside `/do-build`. Generalizing to the session-level verifier is duplicate work for v1. Add to the registry in v2 as a thin wrapper around the existing checks.
- **Adding pipeline `fail` edges for stages that lack them.** No `("BUILD", "fail")`, `("PLAN", "fail")`, `("ISSUE", "fail")`, `("DOCS", "fail")`, or `("MERGE", "fail")` edges added by this plan. Conflates "verification mismatch" with "stage failure" and forces routing decisions out of scope. The plan handles missing fail edges by `finalize_session("failed_verification")` + steer parent.
- **Retroactive verification of completed sessions.** This plan does not back-fill `verified_outcomes` for the last 30 days of records. Verification fires forward only.
- **Retiring the `<!-- OUTCOME ... -->` comment block contract.** Both write paths coexist during the transition. Retirement is a separate cleanup issue once production data shows zero orphan comment-only OUTCOMEs.
- **LLM-mediated verification ("Haiku judges").** Verifiers are deterministic only. If a claim type cannot be deterministically checked, it does not get a verifier — it falls through to `unverifiable` and the classify_outcome result is honored.
- **Verifier registry expansion beyond v1's 2.** v1 ships PR URL and Telegram-send. Email and files-changed are explicit follow-ups (above). Verifiers for "memory saved," "knowledge doc indexed," "reflection ran," etc. are further-out follow-ups.
- **Re-enqueue retry counters.** No `verification_attempts` field. The failure-consequence policy by session type and stage replaces fazm's "retry on next tick."
- **Migration code for existing records.** `claimed_outcomes` and `verified_outcomes` are nullable. `_heal_descriptor_pollution` handles existing records generically. Memory: `feedback_field_backcompat_heal` (issues #1099 / #1172).
- **Dashboard UX redesign.** Adding `failed_verification` to the dashboard's status-color map AND adding a panel for `failed_verification` count is in scope (per Operator critique CONCERN). A bigger redesign that surfaces verification mismatches inline in the session detail view is a follow-up.
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
- [ ] Create `docs/features/agent-session-outcome-verification.md` describing the verifier architecture, the `claimed_outcomes` / `verified_outcomes` fields, the v1 verifiers (PR URL, Telegram send), the **stage-aware failure consequence policy** (which stages route via `psm.fail_stage` vs `finalize_session("failed_verification")` + steer), the relationship to the `<!-- OUTCOME ... -->` contract during the transition, and the v2 follow-ups (email-send, files-changed).
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-lifecycle.md` from "13-state" to "14-state" — add `failed_verification` row with description and routing rules.
- [ ] Update `docs/features/pipeline-state-machine.md` to (a) note that `classify_outcome` and the verifier are *sibling* steps (verifier does not replace classify_outcome), and (b) document which stages have `fail` edges and the rationale for not adding `("BUILD", "fail")` etc. as part of this work.
- [ ] Update `docs/features/build-output-verification.md` to cross-reference the generalized session-level verifier and note that the BUILD-stage commit-count check there remains the v1 source of truth (the session-level files-changed verifier ships in v2).
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
- [ ] `OutcomeVerifier` runs both v1 verifiers (PR URL, Telegram send) against a session's claims and writes `verified_outcomes`. Email-send and files-changed claims return `unverifiable` (intentional v1 scope).
- [ ] When a Dev session claims a `pr_url` that does not exist (verifier verdict `mismatch`) on a stage WITHOUT a `fail` edge (BUILD/PLAN/ISSUE/DOCS/MERGE), `psm.fail_stage` is NOT called, the session ends in `status="failed_verification"`, and the parent PM receives a steer message containing the verification evidence with `expectations` populated.
- [ ] When a Dev session fails verification on a stage WITH a `fail` edge (TEST/REVIEW/CRITIQUE), `psm.fail_stage(stage)` IS called (routes via the existing edge to PATCH or PLAN) AND the session ends in `status="failed_verification"`.
- [ ] When a Teammate session claims `telegram_sent: true` but `recent_sent_drafts` shows no matching entry within 60s, the session ends in `failed_verification` and the next session for that chat receives the failure context via `expectations` plus a one-line apology prepended to the next outbound draft.
- [ ] When a verifier returns `unverifiable` (e.g., `gh` network error, no verifier registered, or `recent_sent_drafts` is None), the session proceeds as if `verified` and a `WARNING` is logged with the artifact-key.
- [ ] The Tier 0 OUTCOME comment-block parser writes through to `claimed_outcomes` so legacy emissions are still verified. The idempotency hash prevents duplicate entries when both write paths fire for the same logical outcome.
- [ ] `failed_verification` appears in the `models/session_lifecycle.py` `TERMINAL_STATUSES` frozenset (line 61) and in `docs/features/session-lifecycle.md`.
- [ ] `finalize_session(session, "failed_verification", reason=...)` actually persists the new status (BLOCKER 3 regression test).
- [ ] Observability metrics (`outcome_verifier.verdict`, `outcome_verifier.mismatch_artifact`, `outcome_verifier.duration_ms`) are emitted on every `verify_claimed` call.
- [ ] Dashboard panel exposes a `failed_verification` count via `ui/app.py` reading from `AgentSession.query.filter(status="failed_verification")`.
- [ ] `tests/integration/test_outcome_verification_e2e.py` asserts the end-to-end claim-verify-fail-escalate flow for BOTH stage-aware paths (with-fail-edge and without-fail-edge).
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
  - Role: Implement `agent/outcome_verifier.py` with the dispatch registry and the v1 verifiers (PR URL, Telegram send) plus the observability metrics.
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
- **Validates**: `tests/unit/test_agent_session_lifecycle.py::test_terminal_allowlist_includes_failed_verification` (create), `tests/unit/test_agent_session_lifecycle.py::test_finalize_session_persists_failed_verification` (create — verifies BLOCKER 3 fix)
- **Informed By**: spike-4 (session-end is the right hook), prior critique BLOCKER 3
- **Assigned To**: data-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `"failed_verification"` to the `TERMINAL_STATUSES` frozenset at `models/session_lifecycle.py:61`. Final value: `TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled", "failed_verification"})`. **NOT line 282** — that line is the error-message string, not the allowlist.
- Verify `finalize_session` correctly handles the new value via the existing terminal-to-different-terminal guard.
- Update the 14-state docstring in `models/agent_session.py:114-138`.

### 3. Implement OutcomeVerifier (v1 — 2 verifiers)
- **Task ID**: build-verifier-core
- **Depends On**: build-data-fields
- **Validates**: `tests/unit/test_outcome_verifier.py` (create — all dispatch + per-verifier tests; 2 verifiers × {verified, mismatch, unverifiable} = 6 base tests + aggregation + exception-path tests)
- **Informed By**: spike-1 (gh pr view), spike-2 (recent_sent_drafts — Telegram only, email gap noted), spike-5 (v1 phasing decision)
- **Assigned To**: verifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/outcome_verifier.py` with the `OutcomeVerifier` class.
- Implement the 2 v1 verifiers: `_verify_pr_url`, `_verify_telegram_sent`. Leave registry-extension hooks for `_verify_email_sent` and `_verify_files_changed` (return `unverifiable` for now — both are explicit follow-ups per No-Gos).
- Implement the dispatch registry and aggregator.
- Implement the freshness reload in `__init__` (Race 1): `AgentSession.get_by_id(session.id)` to pick up the latest `claimed_outcomes`.
- Each verifier wraps its work in `try/except` returning `(unverifiable, evidence)` on exception.
- 5s timeout per verifier (use `subprocess.run(timeout=5)` for `gh` calls).
- PR-URL verifier: assert `state ∈ {"OPEN", "MERGED"}` (Risk 5), not just existence.
- Telegram verifier: contract is "AT LEAST ONE matching send within last `RECENT_DRAFTS_N` saves" (Risk 6); return `unverifiable` (not `mismatch`) when `recent_sent_drafts` is None or empty.
- Record observability metrics: `outcome_verifier.verdict`, `outcome_verifier.mismatch_artifact`, `outcome_verifier.duration_ms` via `analytics/collector.py::record_metric`.

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

### 5. Wire verifier into session completion (stage-aware reconciliation)
- **Task ID**: build-integration-completion
- **Depends On**: build-verifier-core, build-lifecycle-status
- **Validates**: `tests/integration/test_outcome_verification_e2e.py::test_dev_session_mismatch_no_fail_edge_finalizes_failed_verification` (create — covers BUILD/PLAN/ISSUE/DOCS/MERGE), `tests/integration/test_outcome_verification_e2e.py::test_dev_session_mismatch_with_fail_edge_routes_via_psm_fail_stage` (create — covers TEST/REVIEW/CRITIQUE)
- **Informed By**: spike-4 (hook location), spike-6 (stage-aware consequence routing — BLOCKER 2 fix)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert `OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)` call in `agent/session_completion.py:1623` between `psm.classify_outcome()` and `psm.complete_stage()`.
- Implement `reconcile_failed_verification(parent, agent_session, stage, evidence)` enforcing the stage-aware policy from Failure consequence policy:
  - Stages WITH `fail` edge in `PIPELINE_EDGES` (`CRITIQUE`, `TEST`, `REVIEW`): call `psm.fail_stage(stage)` then `finalize_session(agent_session, "failed_verification", reason=...)`.
  - Stages WITHOUT `fail` edge (`ISSUE`, `PLAN`, `BUILD`, `DOCS`, `MERGE`): DO NOT call `psm.fail_stage`. Instead `finalize_session(agent_session, "failed_verification", reason=...)`, set `parent.expectations`, steer parent PM via standard helper.
- Wrap the verifier call in `try/except`; on verifier exception, log `WARNING` and proceed with `classify_outcome` result.
- Mirror the wiring in `complete_transcript()` for non-SDLC session types.

### 6. Write through Tier 0 parser to claimed_outcomes
- **Task ID**: build-tier0-passthrough
- **Depends On**: build-data-fields
- **Validates**: `tests/unit/test_pipeline_state_machine.py::test_outcome_block_writes_through_to_claimed_outcomes` (create), `tests/unit/test_pipeline_state_machine.py::test_idempotency_hash_prevents_duplicate_appends` (create)
- **Informed By**: Risk 3 (transition window safety), Race 2 (idempotency)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_parse_outcome_contract` (or its caller in `classify_outcome`) so that a successfully parsed comment-block OUTCOME also appends to the session's `claimed_outcomes`.
- Idempotency: hash `(stage, status, sorted_artifacts_json)` and skip the append if the last entry has the same hash. Implement the same hash check inside the `record_outcome` MCP tool (Step 4) so both write paths converge cleanly.

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

### 8. Wire user-facing apology + dashboard panel
- **Task ID**: build-user-facing-consequence
- **Depends On**: build-integration-completion
- **Validates**: `tests/integration/test_outcome_verification_e2e.py::test_teammate_failed_verification_prepends_apology` (create), `tests/integration/test_dashboard_failed_verification_panel.py` (create)
- **Informed By**: User critique CONCERN, Operator critique CONCERN
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `bridge/telegram_bridge.py` inbound handler: on inbound for a chat whose most-recent prior session has `status == "failed_verification"`, prepend "We attempted to reply but verification failed; retrying." to the next outbound draft. Per-chat config flag for silent-retry, default off.
- Update `ui/app.py` to add a dashboard panel exposing `AgentSession.query.filter(status="failed_verification").count()` and recent verification mismatches.
- Configure Sentry alert (or note manual config required) for `mismatch` rate > 5% over 1h, gated to fire only after a configurable warm-up window (Risk 7).

### 9. Validation pass
- **Task ID**: validate-verifier
- **Depends On**: build-verifier-core, build-mcp-tool, build-integration-completion, build-tier0-passthrough, build-skill-updates, build-user-facing-consequence
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_outcome_verifier.py tests/integration/test_outcome_verification_e2e.py tests/integration/test_outcome_mcp_server.py tests/integration/test_skill_outcome_recording.py tests/integration/test_dashboard_failed_verification_panel.py -v`
- Run `python -m ruff check agent/ models/ mcp_servers/ bridge/ ui/`
- Run `python -m ruff format --check agent/ models/ mcp_servers/ bridge/ ui/`
- Verify all Success Criteria met.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-verifier
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-session-outcome-verification.md`.
- Update `docs/features/README.md` index, `docs/features/session-lifecycle.md` (13→14 state), `docs/features/pipeline-state-machine.md`, `docs/features/build-output-verification.md`, `CLAUDE.md` lifecycle reference.
- Update `models/agent_session.py` docstring.

### 11. Final validation
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
| Lint clean | `python -m ruff check agent/ models/ mcp_servers/ bridge/ ui/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ models/ mcp_servers/ bridge/ ui/` | exit code 0 |
| New status in TERMINAL_STATUSES (line 61) | `grep -c '"failed_verification"' models/session_lifecycle.py` | output > 0 |
| Status appears in TERMINAL_STATUSES frozenset specifically | `python -c "from models.session_lifecycle import TERMINAL_STATUSES; assert 'failed_verification' in TERMINAL_STATUSES"` | exit code 0 |
| MCP tool registered | `grep -c 'outcome_server' .mcp.json` | output > 0 |
| Feature doc created | `test -f docs/features/agent-session-outcome-verification.md` | exit code 0 |
| Lifecycle doc updated | `grep -c 'failed_verification' docs/features/session-lifecycle.md` | output > 0 |
| No `("BUILD", "fail")` edge added (per No-Gos) | `python -c "from agent.pipeline_graph import PIPELINE_EDGES; assert ('BUILD', 'fail') not in PIPELINE_EDGES"` | exit code 0 |
| Observability metrics registered | `grep -c 'outcome_verifier.verdict\|outcome_verifier.mismatch_artifact\|outcome_verifier.duration_ms' agent/outcome_verifier.py` | output >= 3 |
| Dashboard panel wired | `grep -c 'failed_verification' ui/app.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique 2026-05-04. Original verdict: NEEDS REVISION. Revision pass applied 2026-05-04 (this commit). -->

| Severity | Critic | Finding | Addressed By | Resolution in Revised Plan |
|----------|--------|---------|--------------|---------------------------|
| BLOCKER | Skeptic, Adversary | spike-2 finding is wrong: email is NOT in `recent_sent_drafts`. Email writes to a Redis `email:outbox:{session_id}` queue (`tools/send_message.py:191` `_send_via_email`), drained by `bridge/email_relay.py`. `record_recent_sent_draft` is called only from `agent/output_handler.py:586` (Telegram path). The email verifier as currently designed reads the wrong field and would always return `mismatch` for actual email sends. | Re-do spike-2 for email separately from Telegram; replace email verifier with one that reads from a durable record post-relay (e.g., a new `email:sent:{session_id}` Redis log written by `email_relay.py` after successful SMTP send), or move email verification to a write-time hook in `_send_via_email` that records to a new `email_sent_log` ListField on AgentSession. | **RESOLVED.** spike-2 rewritten: documents that `recent_sent_drafts` is Telegram-only, confirms email_relay writes no durable per-session record. Email verifier moved OUT OF SCOPE for v1 (No-Gos: "Email-send verification (v1)"). v1 verifier registry reduced to 2 verifiers (PR-URL + Telegram-send) per spike-5. `email_sent` claims now return `unverifiable` and proceed. Follow-up v2 issue captures the email-send-log design. |
| BLOCKER | Operator, Archaeologist | Plan calls `psm.fail_stage("BUILD")` on Dev verification mismatch but `agent/pipeline_graph.py:40-59` `PIPELINE_EDGES` defines NO `("BUILD", "fail")` edge. Calling `fail_stage("BUILD")` will execute `get_next_stage("BUILD", "fail")` which returns None (no edge) — the pipeline will silently terminate without routing to PATCH. This contradicts the Solution claim "Dev session, verdict `mismatch` → `psm.fail_stage(stage)` → router dispatches PATCH (existing pipeline edge)." | Add a new `("BUILD", "fail"): "PATCH"` edge to `PIPELINE_EDGES` as part of this work, OR change the consequence policy: instead of `fail_stage`, set `verified_outcomes` and re-enqueue a fresh PATCH session whose `parent_agent_session_id` is the same PM. Pick one path before build. | **RESOLVED.** New spike-6 enumerates which stages have `fail` edges (`CRITIQUE`, `TEST`, `REVIEW`) and which don't (`ISSUE`, `PLAN`, `BUILD`, `DOCS`, `MERGE`). Failure consequence policy is now stage-aware: stages WITH `fail` edges call `psm.fail_stage()` as before; stages WITHOUT `fail` edges DO NOT call `psm.fail_stage` (would silently route via the success-fallback at `pipeline_graph.py:150-151`) — instead `finalize_session("failed_verification")` + steer parent PM. No `("BUILD", "fail")` edge added (No-Gos), preserving scope. New e2e tests cover both routes. |
| BLOCKER | Skeptic | Plan cites incorrect line for adding `failed_verification` to the lifecycle allowlist: "the terminal allowlist in `models/session_lifecycle.py:282`." That line is the error-message string `f"finalize_session() requires a terminal status..."`. The actual frozenset is at line 61: `TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})`. Without modifying line 61, the lifecycle module will reject `failed_verification` with the line-282 error before any allowlist check can succeed. | Update Step 2 to edit `models/session_lifecycle.py:61` (the `TERMINAL_STATUSES` frozenset). Add an integration test that asserts `finalize_session(session, "failed_verification", reason="...")` actually persists. | **RESOLVED.** Step 2 updated to cite line 61 explicitly with the full final frozenset value. Technical Approach section updated to clarify line 282 is the error-message, not the allowlist. New regression test `test_finalize_session_persists_failed_verification` added to Step 2 Validates. New verification check `python -c "from models.session_lifecycle import TERMINAL_STATUSES; assert 'failed_verification' in TERMINAL_STATUSES"`. |
| CONCERN | Adversary | `recent_sent_drafts` is FIFO-capped at `RECENT_DRAFTS_N` (default 3) per `models/agent_session.py:228`. Verifier 2 (`telegram_sent`) and the (now-broken) email verifier rely on scanning this list, but a session that legitimately sends 5 messages and claims "I sent 5 messages" would only find evidence of the last 3 — verifier returns `mismatch` for valid claims. Worse: the entries are text-truncated to 500 chars, so multi-message claims that depend on text content cannot be reconstructed. | Verifier should match by `ts` window + recipient/chat_id, NOT by exhaustive enumeration of every claimed message. Document the contract: "verification confirms AT LEAST ONE matching send within the last RECENT_DRAFTS_N saves." For sessions claiming many sends, route to a dedicated send-log (Risk 6). | **RESOLVED.** Telegram verifier contract documented: "AT LEAST ONE matching send within the last `RECENT_DRAFTS_N` saves." When `recent_sent_drafts` is None or capped out, return `unverifiable` (not false `mismatch`). New Risk 6 captures the FIFO-cap consideration. New unit test asserts the contract. |
| CONCERN | Operator | No specified observability for verification mismatches in production. The plan adds a `WARNING` log on verifier exception (Risk 2) and an analytics metric `outcome_verifier.duration_ms` (Risk 1), but does NOT specify metrics or alerts for the actual verification verdicts. On-call has no way to answer "how often is the agent lying this week?" or "which session_type fails verification most?" without grepping logs. | Add three metrics in the verifier core: `outcome_verifier.verdict{stage,session_type,verdict}` (counter), `outcome_verifier.mismatch_artifact{artifact_key}` (counter), `outcome_verifier.duration_ms{verifier}` (histogram). Add a Sentry alert on `mismatch` rate > 5% over 1h. Add a dashboard panel surfacing `failed_verification` count. | **RESOLVED.** Three metrics added to Solution → Key Elements via `analytics/collector.py::record_metric`. Sentry alert noted with warm-up window (Risk 7). New Step 8 (build-user-facing-consequence) wires the dashboard panel in `ui/app.py`. New verification check greps `agent/outcome_verifier.py` for the three metric names. |
| CONCERN | Archaeologist | Prior Art lists PR #667 and #351 as "the OUTCOME contract foundation" but does NOT examine *why* those PRs deliberately stopped at self-attestation. PR #667 chose Tier 0 over verification — the rationale (cost, latency, transition risk) belongs in "Why Previous Fixes Failed" so this plan doesn't accidentally re-litigate decisions that were intentional. Without that history, "extend the contract with verification" reads as "PR #667 forgot to add verification." | `gh pr view 667 --json body,reviewComments` and excerpt the design discussion. Add a row to "Why Previous Fixes Failed" that says "PR #667 deliberately scoped out verification because [reason]; this plan reverses that decision because [evidence the agent-lying-about-completion failure mode is now common enough to justify the cost]." | **RESOLVED.** New row in "Why Previous Fixes Failed" table for "PR #667 (deferred verification rationale)" — captures the latency cost (~10s pipeline tax), the transition risk (parser-first stabilization), and explains why this plan reverses the decision (failure mode now common; mitigated by 5s timeout per Risk 1 and the v1 phasing per spike-5). |
| CONCERN | Simplifier | Four verifiers in v1 may already be too many for a single ship. The PR-URL verifier is the most valuable (it catches the user's stated pain — "PR opened" claims that aren't real). The Telegram and email verifiers depend on the broken/contested spike-2 design and will burn schedule on field-plumbing. The files-changed verifier is a generalization of `docs/features/build-output-verification.md` which already covers the BUILD case. Shipping all four together amplifies the risk that one bad verifier (e.g., email) blocks the whole feature. | Phase the rollout: v1 ships PR-URL only (highest signal, lowest design risk). v1.1 adds Telegram. v2 adds email after the email-send-log is designed. Files-changed becomes follow-up work since #236 already provides the BUILD-stage check. Move Telegram/email/files-changed to "Out of Scope (v1)" and create follow-up issues. | **RESOLVED (modified).** New spike-5 captures the v1 phasing decision. v1 ships TWO verifiers, not one — PR-URL (Simplifier's recommendation) PLUS Telegram-send (which has a confirmed durable read surface and addresses the Teammate-session use case directly). Email-send and files-changed moved to No-Gos with explicit follow-up issue plan. The registry stays extensible so v2 adds verifiers without re-architecture. |
| CONCERN | User | "Failure consequence policy" sends Teammate verification failures to a *new session in the same chat* via `expectations`. But the chat user (the human Teammate is replying to) never sees that the agent's claim was incorrect — they just see no email arrive. The escalation is internal-only. The user-facing experience is unchanged from today's "agent silently fails" failure mode. | Add a user-facing consequence: when a Teammate session fails verification, the bridge sends a system message to the *originating chat* explaining "we attempted to reply but verification failed, retrying." Without this, the feature only helps engineers reading dashboards — not the user the issue's problem statement is about. | **RESOLVED.** Failure consequence policy updated: on Teammate `failed_verification`, the next inbound from the same chat receives a one-line apology prepended to the draft via `bridge/telegram_bridge.py`. Per-chat config flag for silent-retry, default off. New Step 8 (build-user-facing-consequence) wires this. New e2e test `test_teammate_failed_verification_prepends_apology`. (Note: in v1, only Telegram path is verified — email replies remain unverified per spike-2 / No-Gos.) |
| NIT | Consistency Auditor | Solution → Technical Approach says verifier-call site is `OutcomeVerifier(parent).verify_claimed(...)` in one place and `OutcomeVerifier(agent_session).verify_claimed(...)` in another. Open Question 1 acknowledges this contradiction but does not resolve it. The plan should pick one before critique exits. | Resolve Open Question 1 by editing the Technical Approach to consistently use `OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)`. The reconciliation step then calls `psm.fail_stage()` on the parent's PSM (which is `PipelineStateMachine(parent)`). | **RESOLVED.** Technical Approach now consistently uses `OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)`. Data Flow Step 6 explicitly notes the resolution. Open Question 1 closed (moved to Resolved Decisions section below). |
| NIT | Adversary | Race 2 ("Two MCP record_outcome calls in the same turn") concludes the harness serializes tool calls — true — but does not address the case where `record_outcome` is called once and then the agent's same turn ALSO emits a `<!-- OUTCOME ... -->` comment block. The Tier 0 passthrough (Step 6) writes the comment-block content to `claimed_outcomes` after the explicit `record_outcome` call already wrote — yielding two entries for the same logical outcome. | Add idempotency in the Tier 0 passthrough: hash `(stage, status, sorted_artifacts_json)` and skip the append if the last entry has the same hash. Already mentioned in Step 6 ("idempotency: if the same OUTCOME content is already the last entry, skip the append") but the implementation note belongs in Race 2 too. | **RESOLVED.** Race 2 expanded to explicitly cover the dual-write case (explicit `record_outcome` call + comment-block parse). Idempotency hash documented in both write paths (MCP tool AND Tier 0 parser). New unit test `test_idempotency_hash_prevents_duplicate_appends` in Step 6. |

### Cycle 2 Critique (post-revision pass) — Verdict: NEEDS REVISION

<!-- Populated by /do-plan-critique 2026-05-04 (cycle 2). The 3 prior BLOCKERs were verified resolved; these are NEW findings surfaced after the structural fixes landed. Implementation notes embedded for the next revision pass. -->

| Severity | Critic | Finding | Implementation Note |
|----------|--------|---------|---------------------|
| BLOCKER (cycle 2) | Skeptic | The Telegram-send verifier reads `recent_sent_drafts`, but `agent/output_handler.py:584` only writes that field when `session.is_sdlc=True`. **Teammate sessions are NOT SDLC** (`is_sdlc` property at `models/agent_session.py:1693-1701`), so `recent_sent_drafts` is never populated for them. The verifier returns `unverifiable` for every Teammate Telegram send, which means the User-CONCERN apology pathway (the whole point of v1's Teammate verification) **never fires for the case it was added to fix.** spike-2 acknowledges the gate parenthetically (line 85) but does not propagate the consequence to the Solution. | Either (a) extend `record_recent_sent_draft()` to fire for Teammate sessions too — gate change in `agent/output_handler.py:584` from `session.is_sdlc` to `session.is_sdlc or session.session_type == "teammate"`, OR (b) add a Teammate-specific durable record path (new field `teammate_sent_drafts` written by Teammate output handlers), OR (c) drop Telegram verification from v1 and ship PR-URL only. Option (a) is the smallest change but requires an audit of why the gate exists. Verify with `git log --all -- agent/output_handler.py | grep is_sdlc` and `gh pr view <PR-that-introduced-is_sdlc-gate>` before picking. |
| BLOCKER (cycle 2) | Operator | Plan says "steer parent PM with the verification mismatch context" but the hook fires inside `_handle_dev_session_completion`, which runs AFTER `complete_transcript()` has finalized the parent (`agent/session_completion.py:1567-1581` ordering invariant). `agent/session_executor.py::steer_session` (lines 524-577) rejects sessions in terminal status (lines 555-560). The existing pattern for "steer a PM after the Dev finished" is `_create_continuation_pm()` (`agent/session_completion.py:279, 1784, 1824, 1839`) — but the plan does not reference it. The current "steer parent PM" wording is unimplementable without the continuation-PM detour. | Update Solution → Failure consequence policy and Data Flow Step 7 to use `_create_continuation_pm(parent, reason="verification_mismatch", expectations=evidence_summary, ...)` rather than direct `steer_session`. Reference the four existing call sites at `agent/session_completion.py:279, 1784, 1824, 1839` as precedent. Add an integration test asserting a continuation PM is created with the expected `expectations` field after a verification-mismatch on a stage without a `fail` edge. |
| CONCERN (cycle 2) | Adversary | PM Telegram verification has no timestamp surface. Plan says "for `session_type == "pm"`, check `pm_sent_message_ids` non-empty within the last 60s." But `pm_sent_message_ids` is a `ListField` of message IDs (ints) per `models/agent_session.py:218` and `record_pm_message` at lines 1454-1467 — the entries are bare ints, no timestamps. The 60s window cannot be enforced against a list of ints. | Either (a) extend `pm_sent_message_ids` to store dicts with `ts` (breaking change to consumers — search call sites first), (b) drop the 60s window for PM verification (just check non-empty — accepts that PM-sent-something-recently is loose), or (c) split into `pm_sent_message_log` (dict list with ts) parallel to the existing field. Pick (b) for v1 to avoid breaking existing consumers; document the looseness on the verifier function. |
| CONCERN (cycle 2) | Skeptic | Race 1 mitigation says "the Popoto save in `record_outcome` is synchronous (returns after Redis ACK), so by the time the harness exits...the write is durable." This assumes single-host Redis. If a future deployment uses Redis cluster or a replica, ACK-then-read may not be a freshness guarantee. The mitigation is correct for current deployments but the assumption needs to be explicit. | Add a single-host assumption note to Race 1: "Mitigation assumes single-host Redis; in a clustered deployment with read-replica routing, the reload step in `OutcomeVerifier.__init__` may still pick up a stale read. If the deployment moves to a clustered Redis, revisit the mitigation." Add a failure-path test for an MCP-save error: assert the agent receives an error and does NOT crash. |
| CONCERN (cycle 2) | Adversary | Idempotency hash deduplicates correctly within a single session run, but the plan does not specify behavior for resume — if a session is killed mid-build and resumed, the agent might re-call `record_outcome` with the same `(stage, status, artifacts)` tuple. The hash check skips it, but the verifier consumer reads "the last entry per stage" — there is no last entry for the new attempt. Verifier may verify the previous attempt's claim, not the new one. | Add a test for the resume case: kill a session mid-build, resume, agent re-calls `record_outcome` with same tuple, assert the verifier reads the latest entry (not the deduplicated one). If the dedup wins, document that resume re-runs append a new entry with a fresh `ts` even if content is identical (i.e., dedup is content-based but resume-aware via `ts` field — every record has a unique ts). |
| CONCERN (cycle 2) | Simplifier | `record_outcome(stage=None)` from a Dev session is undefined behavior. Dev sessions always have a stage. The MCP tool should reject `stage=None` for Dev sessions and `stage != None` for Teammate sessions, not silently accept either. | Add validation in the MCP tool: `if stage is None and session.session_type == "dev": return {"error": "Dev sessions must specify a stage"}`. Mirror for Teammate. Document the contract in the tool docstring. Add unit tests for both rejection paths. |
| CONCERN (cycle 2) | Operator | The dashboard panel exposes `failed_verification` count from the start of time. On day 1 of rollout, this is 0; on day 7, it's all sessions in the last week. Operators reading the panel will see misleading totals because the verifier didn't exist before deploy. | Add a "since-deploy" filter to the dashboard panel: `AgentSession.query.filter(status="failed_verification", created_at__gte=DEPLOY_TS)`. Track `DEPLOY_TS` as a constant or env var. Document this in the dashboard panel docstring. |
| CONCERN (cycle 2) | User | v1 phasing leaves most artifact types as `unverifiable` (email, files-changed, anything not in the v1 registry). Operators reading the dashboard cannot tell "this session was unverifiable because no verifier exists yet" from "this session was unverifiable because the network flaked." Both look the same. | Add a `reason_class` enum to `verified_outcomes` entries: `{"no_verifier", "infra_error", "missing_artifact", "stale_data"}`. The dashboard panel groups by `reason_class` so operators can see "X% unverifiable (no_verifier — expected)" vs "Y% unverifiable (infra_error — investigate)." Cheap to implement; high value for on-call. |
| NIT (cycle 2) | Consistency Auditor | "spike-5 ships 2 verifiers" but Step 8's user-facing-consequence task title and description focus on Teammate Telegram verification specifically — implying the apology fires for Teammate sessions. Combined with cycle-2 BLOCKER 1 (Teammate sessions are not SDLC and `recent_sent_drafts` is never populated), the apology pathway is dead code in v1 unless that BLOCKER is resolved. Step 8's existence presupposes the Telegram verifier works for Teammate sessions. | Resolve cycle-2 BLOCKER 1 first; Step 8 stays. If BLOCKER 1 is resolved by dropping Telegram verification (option c), then Step 8 is also dropped from v1 scope and moved to v2 alongside Telegram verification. |

---

## Resolved Decisions (closed during revision pass)

- **OQ1 — Subject of verification**: RESOLVED. The verifier reads from the session that actually did the work (`agent_session` for SDLC, `session` itself for non-SDLC). The consequence (calling `psm.fail_stage` or `finalize_session`) is applied to the parent's PSM. Code is consistently `OutcomeVerifier(agent_session).verify_claimed(stage=current_stage)`. See Data Flow Step 6 and Technical Approach hook code.
- **Verification consequence routing for stages without `fail` edges**: RESOLVED via spike-6. Stages with `fail` edges call `psm.fail_stage(stage)`; stages without `fail` edges call `finalize_session("failed_verification")` + steer parent. No new `fail` edges added in this plan (No-Gos).
- **v1 verifier scope**: RESOLVED via spike-5. Ship 2 verifiers (PR-URL, Telegram-send). Email-send and files-changed are explicit v2 follow-ups.

## Open Questions

1. **Stage field semantics for non-SDLC sessions.** Teammate sessions don't have an SDLC stage. Should `record_outcome` accept `stage=None` (current plan) or invent a synthetic stage like `"TEAMMATE_REPLY"`? The current plan's `stage=None` is simpler but means the verifier dispatches off `artifact_keys` rather than `stage`. Confirm this choice.

2. **Verifier registry: dispatch by artifact-key or by stage?** Current plan says artifact-key (more general). Stage-based dispatch is more constrained but easier to reason about. Suggest artifact-key but flag for review.

3. **Should `unverifiable` block the pipeline?** Current plan: no — proceed as if verified, log warning. Alternative: block on a configurable threshold (e.g., 3 consecutive `unverifiable` results from the same verifier → escalate). The threshold approach is more defensive but adds state. Pick one for v1.

4. **User-facing apology config default.** The Failure consequence policy says the bridge prepends "We attempted to reply but verification failed; retrying." to the next outbound on a chat with a recent `failed_verification`. The per-chat silent-retry flag defaults OFF (apology fires). Confirm this is the right default — some operators may prefer silent-retry as the default to avoid alarming users on minor verification flakes.
