---
status: Approved
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-16
tracking: https://github.com/tomcounsell/ai/issues/124
---

# Coaching Loop: Context-Aware Auto-Continue Messages

## Problem

When the classifier downgrades a completion to `STATUS_UPDATE` (due to hedging or missing evidence), the auto-continue sends a bare `"continue"` message. The builder has no idea why its completion was rejected or what to do differently — it often repeats the same hedging pattern, burns through the 3 auto-continue cap, and the message gets dumped to chat unresolved.

Beyond rejected completions, the coach has no awareness of what the agent is actually doing. If a `/do-build` or `/do-plan` skill was invoked, the coach could reference the plan's success criteria to guide the agent toward a verifiable completion — rather than sending blind "continue" messages.

**Current behavior:**
- Auto-continue always sends `"continue"` regardless of context
- No distinction between rejected completions and genuine mid-work status updates
- No awareness of active skill/phase (plan, build, test, docs)
- No access to plan success criteria during coaching
- **Critical bug: final SDK result leaks to chat on auto-continued sessions** — When `send_to_chat` suppresses an output and re-enqueues, the `BackgroundTask._run_work()` (messenger.py:172) still calls `messenger.send(result)` with the SDK's return value. This second call bypasses the suppression because the SDK already returned. Each auto-continue cycle sends the final result to chat, producing duplicate messages (e.g., 3 identical completion messages during a /do-build).

**Desired outcome:**
- Rejected completions get specific coaching: why it was rejected + what evidence to include
- When a skill is active, coaching references the plan doc or skill success criteria
- Genuine mid-work status updates still get plain `"continue"`
- The coaching surface is lightweight and easy to tune as models evolve
- **Auto-continued sessions suppress the final SDK result** — when `_defer_reaction` is True (continuation job enqueued), the BackgroundTask's `send_result` path must be skipped to prevent duplicate messages

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on coaching message content and skill detection)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work uses existing infrastructure (classifier, job queue, workflow state).

## Solution

### Key Elements

- **Classification enrichment**: `ClassificationResult` gains a `was_rejected_completion` flag when classifier downgrades COMPLETION → STATUS_UPDATE
- **Skill/phase detection**: Detect active skill from workflow state phase or from the original message text (presence of `/do-plan`, `/do-build`, etc.)
- **Coaching message builder**: Generates context-aware coaching messages based on rejection reason + active skill + plan success criteria
- **Auto-continue branching**: `send_to_chat` in job_queue.py uses coaching message instead of bare `"continue"` when appropriate

### Flow

**Agent outputs hedging completion** → Classifier detects hedging, sets `was_rejected_completion=True` → Auto-continue triggers → Coach checks for active workflow/skill → Coach reads plan success criteria if available → **Coaching message sent** instead of bare "continue" → Agent retries with evidence

**Agent outputs genuine status update** → Classifier returns STATUS_UPDATE normally → Auto-continue triggers → **Plain "continue" sent** (no change)

### Technical Approach

0. **Fix duplicate message bug** in `agent/messenger.py` and `agent/job_queue.py`:
   - Root cause: `BackgroundTask._run_work()` (messenger.py:172) always calls `messenger.send(result)` when the SDK returns, even if `send_to_chat` already auto-continued and re-enqueued. The auto-continue path sets `_defer_reaction = True` and returns early, but the SDK call still completes and BackgroundTask sends the final result to `send_to_chat` again — this second call gets classified independently (often as COMPLETION) and sent to chat.
   - Fix: When `send_to_chat` auto-continues (sets `_defer_reaction = True`), also set a flag that tells BackgroundTask NOT to send the final result. Two options:
     - **Option A (preferred)**: After the SDK call returns in `_execute_job`, check `_defer_reaction` — if True, skip `task.run(send_result=True)` or set `task._send_result = False`. But `task.run()` is called before the SDK runs, so this requires refactoring to pass `send_result` as a check at send-time rather than at run-time.
     - **Option B (simpler)**: In `send_to_chat`, when auto-continuing, set `_completion_sent = True` (repurposing the existing gate). This prevents any subsequent calls to `send_to_chat` from reaching Telegram. The next continuation job will produce its own output. This is the cleanest fix — it uses the existing suppression mechanism.
   - Real-world impact: Issue #119 build sent 3 duplicate completion messages to Tom because each auto-continue cycle leaked the final SDK result.

1. **Enrich `ClassificationResult`** in `bridge/summarizer.py`:
   - Add `was_rejected_completion: bool = False` field
   - Classifier already provides `reason` — parse it to detect hedging/evidence-missing downgrades
   - Pattern: if LLM reason contains "hedg", "no evidence", "no proof", "without verification" → flag it

2. **Build coaching messages** in new `bridge/coach.py`:
   - Function `build_coaching_message(classification, workflow_context) -> str`
   - Three tiers:
     - **Rejection coaching** (was_rejected_completion=True): Tell the agent exactly what was wrong and what evidence to include
     - **Skill-aware coaching** (workflow phase known): Reference success criteria from plan doc
     - **Plain continue** (fallback): Just `"continue"`
   - Plan doc reading: If workflow_state has `plan_file`, read the `## Success Criteria` section
   - Keep it simple — no LLM call for coaching messages. Use templates.

3. **Wire into auto-continue** in `agent/job_queue.py:891`:
   - Replace `message_text="continue"` with `message_text=coaching_message`
   - Pass classification result and workflow context to the coach

4. **Propagate workflow context to send_to_chat**:
   - The `_execute_job` function already resolves `workflow_id` and `working_dir`
   - Load `WorkflowState` if available and pass to `send_to_chat` closure
   - Also check `job.message_text` for skill invocation patterns (`/do-plan`, `/do-build`, `/do-test`, `/do-docs`)

## Rabbit Holes

- **LLM-powered coaching**: Tempting to use Haiku to generate dynamic coaching messages. Not worth it — templates are faster, cheaper, and more predictable. The coaching surface should be a static string you can read and tune.
- **Parsing all plan sections**: Don't try to understand the full plan structure. Just extract `## Success Criteria` with a simple regex.
- **Retroactive skill detection from conversation history**: Don't scan past messages to figure out what skill was used. Only use current workflow state and the triggering message.
- **Coaching for non-auto-continue scenarios**: This feature is specifically for auto-continue messages. Don't try to coach the agent through the system prompt or other channels.

## Risks

### Risk 1: Coaching messages confuse the agent
**Impact:** Agent misinterprets coaching as user instructions and changes direction
**Mitigation:** Prefix coaching with clear `[System Coach]` marker. Keep messages short and directive. Test with real agent sessions.

### Risk 2: Plan file reading fails or is slow
**Impact:** Coaching degrades to plain "continue" (acceptable fallback)
**Mitigation:** Wrap in try/except, cache parsed success criteria per workflow_id. File reads are local and fast.

## No-Gos (Out of Scope)

- No changes to the classifier prompt itself (that's issue #99 territory)
- No coaching for outputs that go to chat (only auto-continue path)
- No coaching for the initial message to the agent (only re-enqueued continuations)
- No persistent coaching history or learning across sessions
- No changes to MAX_AUTO_CONTINUES cap

## Update System

No update system changes required — this feature is purely internal to the bridge/agent code. No new dependencies, no config file changes, no migration steps.

## Agent Integration

No agent integration required — coaching messages flow through the existing auto-continue re-enqueue mechanism. The agent receives them as `message_text` on continuation jobs, same as it receives `"continue"` today. No new MCP tools or bridge imports needed.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/coaching-loop.md` describing the coaching system, message templates, and tuning guide
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments in `bridge/coach.py` explaining template structure and how to tune messages
- [ ] Docstrings on `build_coaching_message()` with examples

## Success Criteria

- [ ] Auto-continued sessions do not leak duplicate SDK results to chat (fix for the BackgroundTask re-send bug)
- [ ] Rejected completions (hedging/no evidence) produce specific coaching messages instead of bare "continue"
- [ ] When a workflow is active with a plan file, coaching references the plan's success criteria
- [ ] Genuine status updates still produce plain "continue" (no regression)
- [ ] Coaching messages are prefixed with `[System Coach]` for agent clarity
- [ ] Plan success criteria extraction handles missing/malformed plan files gracefully
- [ ] Session logs capture coaching message content for audit trail
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (coach)**
  - Name: coach-builder
  - Role: Implement coaching message builder and wire into auto-continue
  - Agent Type: builder
  - Resume: true

- **Validator (coach)**
  - Name: coach-validator
  - Role: Verify coaching messages are correct and don't regress auto-continue
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: coach-documentarian
  - Role: Create feature docs for the coaching system
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix duplicate message leak on auto-continue
- **Task ID**: fix-duplicate-leak
- **Depends On**: none
- **Assigned To**: coach-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/job_queue.py:send_to_chat`, when auto-continuing (after re-enqueue, before `return`), set `_completion_sent = True` to gate all subsequent `send_to_chat` calls for this job
- This prevents `BackgroundTask._run_work()` from re-sending the SDK result after auto-continue already handled it
- Add a test: mock an auto-continue scenario and verify only ONE message reaches the send callback
- Verify existing tests still pass

### 2. Add `was_rejected_completion` to ClassificationResult
- **Task ID**: build-classification
- **Depends On**: fix-duplicate-leak
- **Assigned To**: coach-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `was_rejected_completion: bool = False` field to `ClassificationResult` dataclass in `bridge/summarizer.py`
- In `classify_output()`, after LLM classification, detect if the reason indicates a rejected completion (hedging, no evidence patterns)
- Set the flag when detected

### 3. Create coaching message builder
- **Task ID**: build-coach
- **Depends On**: build-classification
- **Assigned To**: coach-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/coach.py` with `build_coaching_message(classification, workflow_state, job_message_text) -> str`
- Implement three tiers: rejection coaching, skill-aware coaching, plain continue
- Add plan success criteria extraction (regex for `## Success Criteria` section)
- Add skill detection from message text patterns (`/do-plan`, `/do-build`, `/do-test`, `/do-docs`)

### 4. Wire coaching into auto-continue path
- **Task ID**: build-wiring
- **Depends On**: build-coach
- **Assigned To**: coach-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py:_execute_job`, load `WorkflowState` if `workflow_id` is available
- Pass classification and workflow context into coaching message builder
- Replace `message_text="continue"` (line ~891) with the coaching message
- Log coaching message content in session snapshot

### 5. Validate coaching integration
- **Task ID**: validate-coach
- **Depends On**: build-wiring
- **Assigned To**: coach-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `ClassificationResult.was_rejected_completion` is set correctly for hedging patterns
- Verify coaching messages contain specific guidance for rejected completions
- Verify plain "continue" is preserved for genuine status updates
- Verify plan success criteria extraction handles edge cases (missing file, no section, malformed markdown)
- Verify `[System Coach]` prefix is present on coaching messages

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-coach
- **Assigned To**: coach-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/coaching-loop.md`
- Add entry to `docs/features/README.md` index table
- Include coaching message templates and tuning guide

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: coach-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/ -k "auto_continue" -x` - auto-continue duplicate suppression test passes
- `python -c "from bridge.coach import build_coaching_message; print('import ok')"` - coach module imports
- `python -c "from bridge.summarizer import ClassificationResult; r = ClassificationResult(output_type='status', confidence=0.9, reason='test', was_rejected_completion=True); print(r)"` - enriched classification works
- `pytest tests/ -x` - all tests pass
- `ruff check bridge/coach.py agent/job_queue.py bridge/summarizer.py` - lint clean
- `black --check bridge/coach.py agent/job_queue.py bridge/summarizer.py` - format clean

---

## Open Questions

1. **Coaching message tone**: Should coaching messages be directive ("Run your tests and paste output") or explanatory ("Your completion was rejected because it contained hedging language. To complete successfully, include...")? The issue example uses explanatory — confirm this is preferred.

2. **Success criteria depth**: When referencing plan success criteria, should the coach quote the full criteria list or just remind the agent to check the plan file? Full quoting adds context but lengthens the message; a pointer keeps it short but requires the agent to re-read the file.

3. **Skill detection scope**: The issue mentions detecting `/do-plan`, `/do-build`, `/do-test`, `/do-docs`. Should we also detect other skills (e.g., `/commit`, `/review-pr`) or keep it strictly to the SDLC skills that have plan docs?
