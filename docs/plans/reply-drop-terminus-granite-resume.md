---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1836
last_comment_id: 4877536194
---

# Reply-to-Valor drops: terminus misclassification + granite resume gate

## Problem

Tom replied to a Valor message in the "Eng: Valor" Telegram chat (msg 1081:
"look here: https://github.com/BuilderIO/agent-native/tree/main/plans", a reply
to a prior Valor message that ended with an open question) and never got a
response. Two independent, confirmed bugs surfaced — one dropped the message,
the second blocked the correct recovery path once the drop was found.

**Current behavior:**

- **Part A — reply misclassified.** `bridge/routing.py::classify_conversation_terminus`
  runs on every reply-to-Valor message. For a bare link/pointer with no `?`, no
  acknowledgment token, and no imperative verb, none of the fast-paths fire and
  the message reaches the LLM classifier, which most plausibly returns **REACT**
  (the prompt's "adds nothing new or is redundant with prior context → REACT"
  rule). `should_respond_async` treats both REACT and SILENT as
  `should_respond=False` — REACT sets a 👍 emoji and sends no reply. The message
  was silently dropped both live and during the catchup re-scan.

- **Part B — granite sessions can't clear the resume gate.** `resume_session()`
  (`tools/valor_session.py:715`) hard-gates all resume/steering-into-thread flows
  on `claude_session_uuid` being non-null. For granite PTY sessions — the primary
  bridge Eng execution path — that field is **never populated**, so no
  granite-driven session can be resumed. Attempting it errors with
  `cannot resume: no transcript UUID stored`.

**Desired outcome:**

- A reply-to-Valor that shares new information (a link, pointer, or reference)
  with no explicit ask defaults conservatively to RESPOND, matching the "any
  classifier error → RESPOND" principle already in the function's docstring.
- `claude_session_uuid` is populated for granite sessions so the
  `resume_session()` gate passes and `valor-session resume` transitions the
  session to `pending` instead of hard-erroring.

## Freshness Check

**Baseline commit:** `fe1fe1358b50da4040ccc23ea16fb19f07303757`
**Issue filed at:** 2026-07-01T10:00:11Z
**Disposition:** Minor drift + Overlap

**File:line references re-verified:**
- `bridge/routing.py::classify_conversation_terminus` — fast-path chain (0 imperative, 1 bot, 2 ack/≤1-word, 3 standalone `?`) plus LLM fallback — **still holds**, now at lines 776-920. `_IMPERATIVE_VERBS` 753-769, `_ACKNOWLEDGMENT_TOKENS` 605-652, `_STANDALONE_QUESTION_RE` 732. Spike-1 confirmed no fast-path fires for the motivating text and REACT is the likely drop verdict.
- `agent/granite_container/bridge_adapter.py` "persists `dev_transcript_path` ~line 640-642" — **drifted** to lines 821-823 (exit-summary save `_publish_exit_summary`). Claim still holds: `dev_transcript_path` is persisted, `claude_session_uuid` is not.
- `tools/valor_session.py` resume gate "line 713" — **drifted** to line 715. Claim holds: `resume_session()` returns the null-UUID error when `claude_session_uuid is None`.
- `agent/sdk_client.py::_store_claude_session_uuid` — **still holds**, line 534; only reached from the headless SDK-client path (lines 1797, 2632), not the granite PTY path.
- `docs/features/agent-session-model.md:272` guard ("drafter UUID NOT written over PM's `claude_session_uuid`") — **still holds** (enforced at `agent/session_completion.py:723-728`). Spike-2 confirmed no collision: that guard *preserves* an already-populated PM UUID; this fix is the *first* population.

**Cited sibling issues/PRs re-checked:**
- #1318 (imperative fast-path) — CLOSED. Its narrow-verb design is the template for Part A's fast-path.
- #1090 (short-reply-to-question exception) — CLOSED. Same bug family.
- #1061 (`claude_session_uuid`-gated resume) — CLOSED. Introduced the gate that Part B unblocks; predates granite PTY as default.
- #1842 (per-role transport hedge) — **CLOSED / MERGED 2026-07-02** (after this issue was filed). Landed the `resume_handles` field that reshapes Part B's fix — see below.
- #1721 (Granite Lossless Checkpoint Resume) — **OPEN**, plan `docs/plans/granite_lossless_checkpoint_resume.md` status `Ready` but **not landed**. Owns the transcript *re-entry* consumption that Part B's gate-unblock is a prerequisite for.

**Commits on main since issue was filed (touching referenced files):**
- `b624607b` #1842 per-role transport hedge — **changed Part B's fix mechanism.** Added `resume_handles`: a per-role list `[{role, claude_session_id, transcript_path, transport}]` populated at spawn in `bridge_adapter.py::_persist_resume_handles` (714-768). For PTY roles the `claude_session_id` is already known at spawn (line 743); headless roles capture it at first turn. The UUID we need is therefore **already in hand** — no need to derive it from a transcript basename at exit.
- `0297da0d` #1688 hook-driven turn returns, `f0775190`/`b01d7fce` granite crash-resume/wedge — irrelevant to this fix's surface.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721) — owns *consuming* a stored UUID to re-enter a prior transcript (`--resume`, cursor replay, skip-priming). This plan only *populates* the field so the gate passes; true lossless re-entry stays #1721's scope. Overlap surfaced as a No-Go, not a blocker — Part B ships independently.

**Notes:** Two drift corrections carried into Technical Approach: (1) Part B populates from the **PM** role handle, **not** `dev_transcript_path` as the issue text suggested — spike-2 proved the human-facing conversational thread is owned by the PM (steering messages inject into PM's PTY only). (2) The fix belongs at the spawn-time per-role capture site (`_persist_resume_handles`), not the exit-time dev-basename site.

## Prior Art

- **#1318**: `fix(terminus): classifier silently drops human action directives` — CLOSED. Added Fast-Path 0 (`_IMPERATIVE_LINE_RE`) plus few-shot examples after a directive-drop. Deliberately narrow verb list; the LLM-prompt side of that split still misses valid replies (this issue). Template for Part A.
- **#1090**: `short human replies to Valor questions silenced by terminus ≤1-word fast-path` — CLOSED. Added the "Valor asked a question" exception to Fast-Path 2. Same bug family (reply-to-Valor content dropped), different mechanism.
- **#1061**: `valor-session resume: support killed/failed sessions` — CLOSED. Introduced the `claude_session_uuid` resume gate — written before granite PTY was default, so it never accounted for the PTY transcript case. Part B closes that gap.
- **#911**: `add conversation terminus detection (RESPOND/REACT/SILENT)` — CLOSED. Original terminus design.
- **#1842**: `per-role transport hedge` — MERGED. Added `resume_handles`; reshapes Part B's fix location.
- **#1721**: `Granite Lossless Checkpoint Resume` — OPEN (Ready, not landed). Owns transcript re-entry consumption.

No merged PR found that already populates `claude_session_uuid` for granite sessions.

## Research

No relevant external findings — this is purely internal (LLM-classifier prompt/fast-path tuning and an internal session-model field). Proceeding with codebase context and the two spikes below.

## Spike Results

### spike-1: Part A — classifier verdict and fix testability
- **Assumption**: "The motivating message falls through all fast-paths to the LLM, which returns REACT/SILENT, and a fast-path fix is deterministically testable."
- **Method**: code-read (`bridge/routing.py`, `tests/unit/test_routing.py`)
- **Finding**: Confirmed. No fast-path fires — the URL has no `?`, "look" is not an imperative verb, word_count=3, and the replied-to Valor message contained a question (so Fast-Path 2's `not valor_asked_question` guard is False anyway). The message reaches the LLM (843-877); the "adds nothing new → REACT" rule (871-872) is the plausible verdict. `should_respond_async` (1331-1349) maps **both** REACT and SILENT to `should_respond=False` — REACT only adds 👍. The existing test suite is dominated by **deterministic fast-path tests that mock no LLM**; a `not sender_is_bot`-gated fast-path for "essentially just a link/pointer" is therefore testable without mocking Ollama/Haiku. A broad "any URL → RESPOND" rule would regress `test_classify_terminus_url_with_query_param_not_respond` (bot-sender URL → SILENT), so the fast-path must gate on `not sender_is_bot`.
- **Confidence**: high
- **Impact on plan**: Part A = add a narrow, `not sender_is_bot`-gated fast-path (RESPOND for a reply that is essentially a bare link/pointer with no ack token and no closing signal), placed after Fast-Path 2 and before the LLM call; plus a defense-in-depth few-shot example in the LLM prompt. Regression test asserts the fast-path deterministically.

### spike-2: Part B — role choice, resume re-entry mechanics, fix location, guard
- **Assumption**: "Populating `claude_session_uuid` from the dev transcript is the fix and is sufficient for resume to work end-to-end."
- **Method**: code-read (`bridge_adapter.py`, `container.py`, `pty_driver.py`, `tools/valor_session.py`, `docs/plans/granite_lossless_checkpoint_resume.md`, tests)
- **Finding**: **Two corrections.** (1) ROLE: the human-facing thread is owned by the **PM**, not the Dev — steering messages (how a human reply reaches a running/resumed session) are written only to PM's PTY (`container.py:2136`, doc 848, 2074-2075). The issue's `dev_transcript_path` citation is wrong for resume; use the **PM** `resume_handles` entry. (2) SUFFICIENCY: populating the field is **necessary but not sufficient** for true re-entry. On worker re-pickup, `bridge_adapter.run()` generates **fresh** UUIDs (558-559) and spawns cold from turn 0 — it never reads the prior `claude_session_uuid`/`resume_handles`. `--resume` is only wired for intra-run crash-resume (`pty_driver.py:431-435,471-476`), not cross-pickup. Cross-pickup consumption is #1721's explicit scope (`_persist_resume_handles` docstring: "Consumption ... is #1721's scope") and #1721 is Ready, not landed. FIX LOCATION: spawn-time `_persist_resume_handles` (714-768) already holds the PTY PM UUID; for headless PM the UUID lands at first turn (same seam as `_store_claude_session_uuid`). GUARD: no collision — the `agent-session-model.md:272` guard *preserves* an already-populated PM UUID; this is the first population.
- **Confidence**: high
- **Impact on plan**: Part B = populate `claude_session_uuid` from the **PM** handle (spawn-time for PTY PM; first-turn for headless PM), unblocking the `resume_session()` gate so resume transitions to `pending`. Full cold→warm transcript re-entry is a No-Go tagged to #1721. The AC "resume succeeds against a real completed granite session" is interpreted as **the `resume_session()` gate passes and the session transitions to pending** (see Open Question 1).

## Data Flow

**Part A (inbound reply):**
1. **Entry point**: Human replies to a Valor message in Telegram → `bridge/telegram_bridge.py` `NewMessage` handler.
2. **Routing**: `should_respond_async` (`bridge/routing.py:1265`) detects `reply_to_valor=True` and calls `classify_conversation_terminus(text, thread_messages, sender_is_bot=False)`.
3. **Classification**: fast-paths 0-3 → (new) link/pointer fast-path → LLM fallback. Verdict RESPOND/REACT/SILENT.
4. **Output**: RESPOND → session continues; REACT → 👍 only; SILENT → nothing. Fix routes the bare-link case to RESPOND.

**Part B (resume attempt):**
1. **Entry point**: `python -m tools.valor_session resume --id <granite-session-id> --message "..."` → `cmd_resume` → `resume_session()` (`tools/valor_session.py:674`).
2. **Gate**: `resume_session()` checks `claude_session_uuid is None` (715). Today: None for granite → hard error. After fix: populated → passes.
3. **Population (fix)**: at granite spawn, `_persist_resume_handles` (`bridge_adapter.py:714`) builds per-role handles; the PM handle's `claude_session_id` is also written to `agent_session.claude_session_uuid`. Headless PM: captured at first turn.
4. **Output**: gate passes → steering message pushed to Redis → `transition_status(..., "pending")` → worker re-picks the session. (Actual transcript re-entry from turn N is #1721.)

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| #1318 | Added imperative-verb fast-path + few-shot examples | Verb list deliberately narrow; the LLM-prompt side of the split still returns REACT/SILENT for a bare link/pointer with no closing signal. |
| #1090 | Added "Valor asked a question" exception to the ack fast-path | Only rescues *short* replies (≤1 word / ack tokens); a multi-word link/pointer still reaches the LLM and is mis-verdicted. |
| #1061 | `claude_session_uuid`-gated resume | Written before granite PTY was the default execution path; never populated the field for PTY sessions, so the gate blocks 100% of granite resumes. |

**Root cause pattern:** The terminus classifier's conservative-default principle ("any error → RESPOND") lives only in fast-paths and the error branch, not in the LLM's *success* path — a confident REACT/SILENT verdict on ambiguous-but-content-bearing input still drops the message. And the resume gate assumed a field that one whole execution path never writes.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. `classify_conversation_terminus` signature unchanged; `claude_session_uuid` is an existing `AgentSession` field.
- **Coupling**: Part B reuses the `resume_handles` PM entry captured by #1842 — no new cross-component coupling; if anything it makes the existing field consistent across transports.
- **Data ownership**: `claude_session_uuid` for granite sessions becomes owned by the granite adapter's spawn/first-turn path, mirroring how the SDK-client path owns it via `_store_claude_session_uuid`.
- **Reversibility**: trivial — both changes are additive (one fast-path branch, one field write) and revert cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm AC interpretation for Part B — see Open Question 1)
- Review rounds: 1

Two independent, small-surface fixes. The bottleneck is the Part B scope boundary (gate-unblock vs. full re-entry), not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies. Both changes are internal to `bridge/routing.py` and `agent/granite_container/bridge_adapter.py`, tested against local Redis (`redis_test_db` fixture).

## Solution

### Key Elements

- **Link/pointer fast-path (Part A)**: a `not sender_is_bot`-gated branch in `classify_conversation_terminus` that returns RESPOND when a reply is essentially a bare link/reference with no acknowledgment token and no closing signal.
- **LLM few-shot reinforcement (Part A)**: an example line in the classifier prompt mapping a bare-link reply to RESPOND (defense-in-depth for cases the fast-path misses).
- **PM-handle UUID population (Part B)**: write the PM role's `claude_session_id` onto `agent_session.claude_session_uuid` at the point it becomes known — spawn-time for PTY PM (`_persist_resume_handles`), first-turn for headless PM.

### Flow

**Part A:** Human replies with a link → routing calls terminus classifier → link/pointer fast-path fires → RESPOND → session continues, human gets a reply.

**Part B:** Granite session completes a turn → PM UUID persisted to `claude_session_uuid` → operator runs `valor-session resume` → gate passes → session transitions to `pending` → worker re-picks it up.

### Technical Approach

- **Part A** (`bridge/routing.py`): Add the link/pointer fast-path after Fast-Path 2 (ack) and before the LLM call (~line 838). Condition: `not sender_is_bot` AND the stripped text, with URLs removed, is empty or trivially short (i.e. the message is "essentially just a link/pointer") AND it is not already an ack token. Return RESPOND. Keep the bot-sender path untouched so `test_classify_terminus_url_with_query_param_not_respond` still yields SILENT. Add one few-shot line to the LLM prompt (852-866) mapping `"look here: <url>" → RESPOND`.
- **Part B** (`agent/granite_container/bridge_adapter.py`): In `_persist_resume_handles` (714-768), after building `handles`, if the PM handle has a non-null `claude_session_id`, also set `self._agent_session.claude_session_uuid = <pm_uuid>` and include it in the `save(update_fields=...)`. For the headless-PM case (per #1842's per-role hedge), confirm the first-turn capture path (`_store_claude_session_uuid` / `container.py:774` seam) also lands the PM UUID on `claude_session_uuid`; if it does not, add the write there. Do **not** derive from `dev_transcript_path`. Guard-safe: this is the field's first population; the completion-runner drafter guard (`session_completion.py:723-728`) already avoids overwriting it.
- **AC4 (no SDK-client regression)**: the granite path does not touch `_store_claude_session_uuid` for PTY; the headless capture already flows through it. `_get_prior_session_uuid` is never read by `bridge_adapter.run()` (it generates fresh UUIDs), so populating the field cannot cause a stale-transcript resume on a *new* granite session.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_persist_resume_handles` wraps its body in `except Exception` and logs a warning (`bridge_adapter.py:767-768`). The new `claude_session_uuid` write lives inside that block — add a test asserting a handle-persist failure logs the warning and does not crash the run, and that a successful persist sets `claude_session_uuid`.
- [ ] `classify_conversation_terminus`'s Ollama/Haiku failure branch already defaults to RESPOND — the new fast-path runs before it and needs no exception handler (pure regex/string logic).

### Empty/Invalid Input Handling
- [ ] The link/pointer fast-path must handle: empty text (already returns RESPOND at 803-804, before the new branch), whitespace-only, a URL with a trailing word ("look here: <url> thoughts?" — has `?`, should already RESPOND via Fast-Path 3), and a bare token that is also an ack. Add tests for the bare-URL, "look here: <url>", and multi-URL cases.
- [ ] Part B: assert a granite session whose PM handle has a null `claude_session_id` (headless-at-spawn) does not write a `None` over an existing value.

### Error State Rendering
- [ ] Part A output is user-visible: assert that a RESPOND verdict for the link reply produces `should_respond=True` (the message is not dropped to emoji-only). Covered by a routing-level test.
- [ ] Part B: assert `resume_session()` against a populated granite session returns `success=True` (not the `cannot resume: no transcript UUID stored` error).

## Test Impact

- [ ] `tests/unit/test_routing.py` — UPDATE (additive): add regression tests for the link/pointer fast-path (human bare-URL → RESPOND; "look here: <url>" → RESPOND; multi-URL → RESPOND). Verify `test_classify_terminus_url_with_query_param_not_respond` (bot-sender URL → SILENT) still passes unchanged — the new fast-path is `not sender_is_bot`-gated.
- [ ] `tests/unit/test_valor_session_resume_release.py` — UPDATE (additive): add a case where a resumable session with a populated `claude_session_uuid` resumes successfully (mirrors the existing null-UUID rejection at lines 330-348, inverted).
- [ ] `tests/unit/test_session_executor_granite.py` — UPDATE (additive): extend the real-`AgentSession` fixture (`_make_session`, 88-116) to assert that after a granite run the record has a non-null `claude_session_uuid` equal to the PM handle's `claude_session_id`, and that `resume_session()` succeeds against it.

No existing test is broken or deleted — both fixes are purely additive (a new fast-path branch before the LLM, and a first-population field write). No existing behavior or interface changes.

## Rabbit Holes

- **Building the full lossless-resume re-entry.** Making the worker actually re-enter the prior PM transcript from turn N (feed the stored UUID into `--resume`, replay the loop cursor, skip re-priming) is #1721's entire Large-appetite scope. Populating the field is a one-line prerequisite; do NOT pull #1721's consumption work into this plan.
- **Over-broadening the Part A fast-path.** "Any message containing a URL → RESPOND" would regress the bot-sender-URL SILENT case and could re-open bot loops. Keep it narrow: `not sender_is_bot` AND the reply is *essentially* a bare link/pointer.
- **Reworking the LLM prompt wholesale.** Tightening one few-shot line is fine; redesigning the RESPOND/REACT/SILENT prompt is a separate, un-scoped effort with no deterministic test.
- **Choosing dev over PM for the UUID.** The issue text says `dev_transcript_path`; spike-2 proved PM owns the thread. Do not follow the issue text here.

## Risks

### Risk 1: Link/pointer fast-path over-fires and resurrects bot loops
**Impact:** A bot reply that is a bare link could be routed to RESPOND, re-opening the loop that Fast-Path 1 exists to break.
**Mitigation:** Gate the new branch on `not sender_is_bot` (Fast-Path 1 handles bot senders before this branch is reached). Add a test asserting a bot-sender bare-URL still returns SILENT.

### Risk 2: Part B populates the field but resume still "does nothing useful"
**Impact:** After the gate passes, the worker re-picks the session but spawns cold from turn 0 (re-entry is #1721), so the human perceives a fresh, context-less session rather than a true continuation.
**Mitigation:** Scope Part B's AC to the gate-unblock (session transitions to pending, no hard error) and document the cold-re-entry limitation in `granite-pty-production.md` with a pointer to #1721. Surface as Open Question 1 for explicit sign-off.

### Risk 3: Headless-PM transport writes a null UUID
**Impact:** Under #1842's per-role hedge, if PM runs headless, `claude_session_id` is null at spawn — a naive spawn-time write would clobber the field with None.
**Mitigation:** Only write when the PM handle's `claude_session_id` is non-null; land the headless-PM UUID at first-turn capture (the existing `_store_claude_session_uuid` seam). Test both transports.

## Race Conditions

### Race 1: resume gate reads `claude_session_uuid` while the granite adapter is still writing it
**Location:** `bridge_adapter.py:714-766` (write) vs. `tools/valor_session.py:715` (read).
**Trigger:** An operator runs `valor-session resume` against a session that is mid-spawn.
**Data prerequisite:** `claude_session_uuid` must be persisted before a resume is attempted.
**State prerequisite:** Resume only targets sessions in `RESUMABLE_STATUSES` (completed/killed/failed/abandoned) — a mid-spawn session is `running`/`pending` and is rejected by the status gate (694-714) before the UUID gate is reached.
**Mitigation:** The status gate already excludes non-terminal sessions, so the UUID is always fully persisted (spawn-time or first-turn) by the time a session is resumable. No new synchronization needed; note it in the test.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1721] **Cold→warm transcript re-entry** — feeding the stored `claude_session_uuid` into a `--resume` spawn on worker re-pickup, loop-cursor replay, and skip-priming so a resumed granite session continues from turn N instead of spawning fresh. This is the entire scope of the open, Ready plan `granite_lossless_checkpoint_resume.md` (#1721). Part B only populates the field so #1721's consumption has something to read and the `resume_session()` gate stops hard-erroring.
- [SEPARATE-SLUG #1721] **Dev-role transcript resume** — resuming the Dev sub-session's transcript. The human thread is PM-owned; Dev re-entry, if ever needed, belongs with #1721's per-role handle consumption.

## Update System

No update system changes required — this is purely internal. `claude_session_uuid` is an **existing** `AgentSession` field (no schema change, no Popoto migration, no `scripts/update/migrations.py` entry). The two edits (`bridge/routing.py`, `agent/granite_container/bridge_adapter.py`) ship with the normal `git pull` in `/update`; the bridge restart at the end of `/update` picks them up. No new dependencies or config files to propagate.

## Agent Integration

No agent integration required — both fixes are bridge/worker-internal.
- Part A changes the bridge's own inbound-routing decision (`bridge/routing.py`), invoked directly by `bridge/telegram_bridge.py`. No MCP/`.mcp.json` surface.
- Part B populates a field consumed by the existing `python -m tools.valor_session resume` CLI (already an entry point in `pyproject.toml [project.scripts]`). No new CLI or MCP tool.
- Integration coverage: the Part B acceptance test drives `valor-session resume` against a real completed granite `AgentSession` (reusing the `test_session_executor_granite.py` fixture) and asserts the gate passes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — note that `claude_session_uuid` is now populated from the PM role handle so `valor-session resume` clears the gate; link to #1721 for the cold-re-entry limitation.
- [ ] Update `docs/features/agent-session-model.md` — document that granite sessions now populate `claude_session_uuid` (PM handle), and reconcile with the existing drafter-guard note at line 272 (first-population vs. no-overwrite).

### External Documentation Site
- No external docs site in scope.

### Inline Documentation
- [ ] Docstring on the new link/pointer fast-path in `classify_conversation_terminus`, mirroring the Fast-Path 0 comment style (mined-example provenance).
- [ ] Comment at the `claude_session_uuid` write in `_persist_resume_handles` explaining the PM-role choice and the #1721 boundary.

## Success Criteria

- [ ] A human reply-to-Valor that is essentially a bare link/reference with no `?`, ack token, or imperative verb classifies as RESPOND (deterministic fast-path test, no LLM mock).
- [ ] A bot-sender bare-URL reply still classifies as SILENT (no regression to `test_classify_terminus_url_with_query_param_not_respond`).
- [ ] A completed granite PTY session has `claude_session_uuid` populated (equal to the PM handle's `claude_session_id`) once its first turn completes.
- [ ] `python -m tools.valor_session resume --id <granite-session-id> --message "..."` returns success (gate passes, transitions to `pending`) against a real completed granite session in a test — no `cannot resume: no transcript UUID stored`.
- [ ] No regression to the SDK-client-path `_store_claude_session_uuid` behavior (headless UUID capture unchanged).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it does not build directly. Parts A and B are independent and build in parallel.

### Team Members

- **Builder (routing / Part A)**
  - Name: routing-builder
  - Role: Add the link/pointer fast-path + few-shot line in `bridge/routing.py`; add routing regression tests.
  - Agent Type: builder
  - Domain: conversational-UX (see DOMAIN_FRAMING.md)
  - Resume: true

- **Builder (granite resume / Part B)**
  - Name: granite-builder
  - Role: Populate `claude_session_uuid` from the PM handle in `bridge_adapter.py` (PTY spawn + headless first-turn); add granite/resume regression tests.
  - Agent Type: builder
  - Domain: Redis/Popoto data (see DOMAIN_FRAMING.md)
  - Resume: true

- **Validator (both parts)**
  - Name: reply-resume-validator
  - Role: Verify both parts against Success Criteria; run the full verification table.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reply-resume-docs
  - Role: Update `granite-pty-production.md` and `agent-session-model.md`.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core agents (`builder`, `validator`, `documentarian`) cover all work; domain framing pasted per task.

## Step by Step Tasks

### 1. Part A — link/pointer fast-path
- **Task ID**: build-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_routing.py` (add cases; existing cases still pass)
- **Informed By**: spike-1 (no fast-path fires; `not sender_is_bot` gate required; deterministic testability)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a `not sender_is_bot`-gated fast-path in `classify_conversation_terminus` (after Fast-Path 2, before the LLM call) returning RESPOND when the reply is essentially a bare link/reference with no ack token and no closing signal.
- Add one few-shot line to the LLM prompt mapping a bare-link reply to RESPOND.
- Add regression tests: human bare-URL → RESPOND; "look here: <url>" → RESPOND; bot-sender bare-URL → SILENT (unchanged).

### 2. Part B — populate `claude_session_uuid` from PM handle
- **Task ID**: build-granite-resume
- **Depends On**: none
- **Validates**: `tests/unit/test_session_executor_granite.py`, `tests/unit/test_valor_session_resume_release.py` (add cases)
- **Informed By**: spike-2 (PM role, not dev; spawn-time `_persist_resume_handles` for PTY + first-turn for headless; gate-unblock only; no guard collision)
- **Assigned To**: granite-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_persist_resume_handles`, write the PM handle's non-null `claude_session_id` to `agent_session.claude_session_uuid` and include it in `save(update_fields=...)`.
- Confirm/handle the headless-PM first-turn capture path so `claude_session_uuid` lands for both transports; never write None over an existing value.
- Add regression tests: completed granite session has non-null `claude_session_uuid`; `resume_session()` succeeds against it.

### 3. Validate both parts
- **Task ID**: validate-both
- **Depends On**: build-routing, build-granite-resume
- **Assigned To**: reply-resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; confirm no regression to `_store_claude_session_uuid` and the bot-URL SILENT case.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-routing, build-granite-resume
- **Assigned To**: reply-resume-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and `docs/features/agent-session-model.md` per the Documentation section.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-both, document-feature
- **Assigned To**: reply-resume-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; verify docs updated; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_routing.py tests/unit/test_valor_session_resume_release.py tests/unit/test_session_executor_granite.py -q` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Part A fast-path present | `grep -c "sender_is_bot" bridge/routing.py` | output > 0 |
| Part B writes PM uuid | `grep -c "claude_session_uuid" agent/granite_container/bridge_adapter.py` | output > 0 |
| No dev-basename resume (anti-criterion) | `grep -c "dev_transcript_path" agent/granite_container/bridge_adapter.py \| head -1; grep -rn "claude_session_uuid.*dev_transcript_path\|dev_transcript_path.*claude_session_uuid" agent/granite_container/bridge_adapter.py` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Part B AC interpretation (blocking-ish).** "resume succeeds against a real completed granite session" — is the accepted definition **(a)** the `resume_session()` gate passes and the session transitions to `pending` (what Part B delivers; true transcript re-entry deferred to #1721), or **(b)** the resumed session must actually re-enter the prior PM transcript from turn N (requires #1721's consumption to land first)? The plan defaults to (a) and files re-entry as a #1721 No-Go. Confirm (a) is acceptable, or Part B becomes blocked-on-#1721.
2. **PM vs. Dev for the scalar field.** Spike-2 determined the PM handle is correct (PM owns the human thread; steering injects into PM's PTY). The issue text cited the dev transcript. Confirm PM is the intended resume target. If Dev-session resume is also wanted, that is separate #1721 scope.
3. **Fast-path breadth for Part A.** The plan scopes the fast-path to "essentially a bare link/pointer with no ack/closing signal." Should it also cover a reply that is a short pointer phrase with no URL (e.g. "see the PR description")? The conservative default is to keep it URL/link-anchored and let the few-shot LLM line handle prose pointers — confirm that split is acceptable.
