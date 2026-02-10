---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-02-10
tracking: https://github.com/tomcounsell/ai/issues/75
---

# Bridge Workflow Gaps

## Problem

The documented development workflow in CLAUDE.md describes behaviors that aren't fully implemented in the bridge and agent code. Users experience friction when:

- The agent pauses for status updates that don't require human input, interrupting autonomous work
- Session logs are a single chronological stream (`logs/bridge.log`), making per-session debugging difficult
- Thumbs-up (👍) reactions on completion don't signal to the system that work is done
- PR links after `/build` must be manually extracted from agent output

**Current behavior:**
- Summarizer sends all agent output to chat, regardless of whether it contains a question
- Session breakpoint logs aren't saved separately — only the main bridge.log exists
- 👍 reactions are recognized as valid emoji but don't trigger any workflow action
- `/build` completes but PR link delivery depends on agent remembering to include it

**Desired outcome:**
- Agent only pauses when there's a genuine open question requiring human input
- Per-session logs are saved at pause/resume/complete transitions for debugging
- 👍 reaction on a message signals work completion to the system
- `/build` and `/make-plan` automatically deliver their output links to chat

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (clarify what constitutes "open question" vs "status update")
- Review rounds: 1 (verify auto-continue logic doesn't skip genuine questions)

The core work is classifying agent output (requires LLM call) and wiring up reactions/links. The risk is over-engineering the classification when simple heuristics might suffice.

## Prerequisites

No external prerequisites — all dependencies already exist in the project.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge running | `./scripts/valor-service.sh status` | Test auto-continue behavior |
| Anthropic API key | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | LLM classification |

## Solution

### Key Elements

- **Output classifier**: LLM-based classification of agent output into categories (question, status update, completion, blocker)
- **Auto-continue logic**: Summarizer sends "continue" when output is a status update with no question
- **👍 reaction handler**: Bridge intercepts 👍 on messages and triggers `mark_work_done()`
- **Session log snapshots**: Save full session context at breakpoints to `logs/sessions/{session_id}/`
- **Automatic link delivery**: `/build` and `/make-plan` skills include link output as required artifact

### Flow

**Message arrives** → Agent processes → Output generated → **Classifier runs** →
- If question: summarize and send, wait for reply
- If status update: send "continue" (auto-continue)
- If completion: send summary, mark done

**👍 reaction** → Bridge detects → Lookup original message's session → `mark_work_done()` → Session marked complete

### Technical Approach

#### 1. Output Classification (bridge/summarizer.py)

Add `classify_output()` function that categorizes agent output:

```python
class OutputType(Enum):
    QUESTION = "question"       # Needs human input
    STATUS_UPDATE = "status"    # Progress report, no input needed
    COMPLETION = "completion"   # Work finished
    BLOCKER = "blocker"         # Stuck, needs help
    ERROR = "error"             # Something failed
```

Use Haiku for classification (fast, cheap) with 80% confidence threshold — below that, default to pausing (conservative). Prompt should detect:
- Direct questions ("Should I...?", "Which do you prefer?", "Do you want...?")
- Open-ended asks ("Let me know if...", "Any feedback on...?")
- vs. status reports ("Done", "Fixed X", "Pushed commit abc123")

#### 2. Auto-Continue in Summarizer (bridge/summarizer.py)

After classifying output as STATUS_UPDATE, instead of sending to chat and waiting:
1. Log the status to bridge.log
2. Inject "continue" as a steering message to the active session
3. Or simply don't pause — let the agent continue autonomously

The summarizer already has the logic to decide whether to send; extend it to auto-continue.

#### 3. 👍 Reaction Semantics

**Note:** Telethon cannot receive emoji reaction events — this is a Telegram API limitation for user accounts (only bots can receive reaction callbacks). The 👍 reaction serves as a **human-to-human communication signal** in the group chat indicating "this task is handled and complete." It's documentation of completion status for the team, not a programmatic trigger.

The `mark_work_done()` function is already called automatically at job completion in `agent/job_queue.py`. No additional reaction handler needed.

#### 4. Session Log Snapshots (bridge/ or agent/)

Create `logs/sessions/{session_id}/` directory structure:
- `{timestamp}_pause.log` — context at pause
- `{timestamp}_resume.log` — context at resume
- `{timestamp}_complete.log` — final state

Each snapshot includes:
- Session ID, project, branch
- Last N messages from the conversation
- Current task list state
- Git status/diff summary

#### 5. Skill Link Delivery

Update `/build` and `/make-plan` skills to ensure link output is reliable:
- `/make-plan`: After creating issue, send `"Issue: {url}\nPlan: {plan_url}"` as final output
- `/build`: After creating PR, send `"PR: {pr_url}"` as final output

The skill definitions in `.claude/skills/` already do this — verify the links flow through to Telegram.

## Rabbit Holes

- **Over-complex classification** — Start with simple heuristics (question mark, specific keywords). Only add LLM if heuristics fail.
- **Real-time reaction streaming** — Telethon reaction events can be tricky. Don't try to handle all edge cases; focus on 👍 specifically.
- **Session log verbosity** — Don't log everything. Capture only what's needed for debugging breakpoint issues.
- **Auto-continue loops** — Risk of agent talking to itself indefinitely. Max 3 auto-continues per session (resets on human reply).

## Risks

### Risk 1: Auto-continue skips genuine questions
**Impact:** Agent continues working when it should have asked for clarification, wasting time on wrong path.
**Mitigation:** Conservative classification — when in doubt, treat as question. Log all auto-continues for audit. Add escape hatch (e.g., "⚠️" prefix forces pause).

### Risk 2: Classification model unavailable
**Impact:** Auto-continue can't run, agent pauses on every output.
**Mitigation:** Fall back to keyword heuristics (question marks, specific phrases). Log when LLM classification fails.

## No-Gos (Out of Scope)

- Not implementing full session replay/debugging UI — just log snapshots
- Not adding reaction event handlers — Telethon can't receive them for user accounts
- Not changing how sessions resume from reply-to — that already works
- Not modifying task list scoping — already handled by #62/session-isolation

## Update System

No update system changes required — all changes are Python code that propagates via normal `git pull`. The `/update` skill already restarts the bridge after pulling.

## Agent Integration

No new MCP server needed. Changes are internal to the bridge/summarizer. The auto-continue logic runs before sending output to Telegram, so no agent-side changes required.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to mention 👍 reaction completion signal
- [ ] Add entry for session logging to `docs/features/README.md` if significant
- [ ] Update CLAUDE.md "Auto-Continue Rules" section if behavior differs from documented
- [ ] Add inline comments explaining the classification logic

## Success Criteria

- [ ] Agent output classified correctly (questions vs status updates) at 95%+ accuracy
- [ ] Status updates trigger auto-continue, no human wait
- [ ] `mark_work_done()` called automatically on job completion (already works)
- [ ] Session logs saved at pause/resume/complete transitions
- [ ] `/make-plan` delivers issue + plan URLs to chat automatically
- [ ] `/build` delivers PR URL to chat automatically
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (classifier)**
  - Name: classifier-builder
  - Role: Implement output classification logic in summarizer
  - Agent Type: builder
  - Resume: true

- **Builder (session-logs)**
  - Name: session-logs-builder
  - Role: Implement per-session log snapshots at breakpoints
  - Agent Type: builder
  - Resume: true

- **Validator (workflow)**
  - Name: workflow-validator
  - Role: Verify auto-continue, reaction handling, and link delivery work end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs and CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement output classification
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `OutputType` enum and `classify_output()` function to `bridge/summarizer.py`
- Use Haiku with a classification prompt
- Add fallback to keyword heuristics if LLM unavailable
- Return classification + confidence

### 2. Implement auto-continue logic
- **Task ID**: build-autocontinue
- **Depends On**: build-classifier
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `summarize_response()` to call classifier
- When STATUS_UPDATE: inject "continue" via steering queue
- Add max auto-continue counter (default: 3, resets on human reply)
- Log all auto-continues

### 3. Implement session log snapshots
- **Task ID**: build-session-logs
- **Depends On**: none
- **Assigned To**: session-logs-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `save_session_snapshot()` function
- Save to `logs/sessions/{session_id}/{timestamp}_{event}.log`
- Include session context, messages, tasks, git state
- Call from pause/resume/complete transitions

### 4. Validate workflow integration
- **Task ID**: validate-workflow
- **Depends On**: build-autocontinue, build-session-logs
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify classification accuracy on sample outputs
- Verify auto-continue triggers correctly
- Verify mark_work_done() is called on job completion
- Verify session logs are created
- Run `black . && ruff check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-workflow
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update session-isolation.md with 👍 completion signal
- Update CLAUDE.md auto-continue section if needed
- Add inline code comments

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `black . && ruff check .`
- Run `pytest tests/`
- Verify all success criteria
- Generate final report

## Validation Commands

- `black --check . && ruff check .` — Code formatting and linting
- `pytest tests/` — All tests pass
- `grep -n "classify_output\|OutputType" bridge/summarizer.py` — Classifier exists
- `grep -n "mark_work_done" agent/job_queue.py` — Completion marking exists
- `ls logs/sessions/` — Session log directory exists

