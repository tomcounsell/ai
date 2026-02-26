---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/186
---

# Summarizer Quality: Always Summarize, Mandatory Stage Lines, Human Gate Before Merge

## Problem

The [summarizer output audit](https://github.com/tomcounsell/ai/blob/main/docs/audits/summarizer_output_audit.md) (grade C+) found three systemic issues:

**Current behavior:**
1. Messages under 500 chars bypass summarization entirely. ~30% of messages are verbose process dumps ("Let me check... Now let me read...") that slip under the threshold.
2. Stage progress lines and link footers appear ~10% of the time despite working code existing. Root cause: `AgentSession` is never passed through the callback chain to the summarizer — `send_cb` signature has no session parameter.
3. The SDLC pipeline auto-merges after review even when tech debt and nits exist. PR reviews sometimes aren't posted to GitHub at all.

**Desired outcome:**
1. Every message is summarized (no character threshold).
2. Every SDLC message includes stage progress line and link footer — rendered via simple template, not LLM.
3. PRs require a published GitHub review (no exceptions) and a human approval gate before merge.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1 (verify output format looks right)
- Review rounds: 1

## Prerequisites

No prerequisites — all code exists and is already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| AgentSession with stage helpers | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'is_sdlc_job')"` | Stage-aware context |

## Solution

### Key Elements

- **Always summarize**: Remove `SUMMARIZE_THRESHOLD` gating. Every message goes through the summarizer.
- **Template-rendered SDLC format**: For SDLC jobs, build the message from a simple string template in code — not LLM. The LLM summarizes the bullet content only; stage line and link footer are appended mechanically.
- **Thread `AgentSession` through callback chain**: Add session parameter to `SendCallback` type and all callers.
- **Human gate before merge**: SDLC dispatcher stops for human approval after review+docs, never auto-merges.
- **Mandatory PR reviews**: `/do-pr-review` must post to GitHub — enforce in skill and validate in SDLC dispatcher.

### Flow

**Agent output produced** → `send_to_chat()` in job_queue.py → passes `agent_session` to `send_cb` → `send_response_with_files()` receives session → `summarize_response()` receives session → LLM summarizes bullet content → `_compose_sdlc_message()` renders template with stage line + bullets + links → Telegram

**SDLC message template** (rendered in code, not by LLM):
```
{emoji} {first_line_of_request}
{stage_progress_line}
{llm_summary_bullets}
{link_footer}
```

For non-SDLC messages: LLM summarizes as prose, no template.

### Technical Approach

#### 1. Always summarize (no threshold)

In `bridge/summarizer.py`:
- Remove `SUMMARIZE_THRESHOLD = 500` constant
- Remove the early return in `summarize_response()` that skips summarization for short messages
- Keep `FILE_ATTACH_THRESHOLD` for file attachments (separate concern)
- Keep `SAFETY_TRUNCATE` for Telegram hard limit

#### 2. Thread AgentSession through callbacks

In `agent/job_queue.py`:
- Change `SendCallback` type signature: `Callable[[str, str, int, Any], Awaitable[None]]` (add optional session param)
- In `send_to_chat()`, pass `agent_session` when calling `send_cb`
- `agent_session` is already in the `_execute_job()` closure scope (line ~983)

In `bridge/telegram_bridge.py` (or wherever `send_cb` is registered):
- Update the registered callback to accept and forward the session parameter
- Pass session to `send_response_with_files()`

In `bridge/response.py`:
- `send_response_with_files()` already accepts `session=None` — just needs to receive it

#### 3. Simple template rendering for SDLC messages

In `bridge/summarizer.py`, modify `_compose_structured_summary()`:
- When `session.is_sdlc_job()` is True, ALWAYS render stage progress and link footer
- Stage progress line is mandatory — if `get_stage_progress()` returns all pending, still render it (shows pipeline just started)
- Link footer is mandatory — render whatever links exist (may be just Issue, may be Issue+Plan+PR)
- The LLM only generates the bullet-point content (2-4 bullets)
- Template concatenation happens in Python, not in the LLM prompt

Template logic (pseudocode):
```python
def _compose_sdlc_message(session, bullets: str) -> str:
    emoji = _get_status_emoji(session)
    label = _get_request_label(session)
    stage_line = _render_stage_progress(session)  # Always render for SDLC
    link_footer = _render_link_footer(session)  # Always render for SDLC

    parts = [f"{emoji} {label}"]
    if stage_line:
        parts.append(stage_line)
    parts.append(bullets.strip())
    if link_footer:
        parts.append(link_footer)
    return "\n".join(parts)
```

#### 4. Human approval gate before merge

In `.claude/skills/sdlc/SKILL.md`:
- After REVIEW and DOCS stages complete, the dispatcher MUST stop and report to user
- The dispatcher NEVER auto-merges — it reports "Ready for merge" and waits for human
- Add explicit instruction: "After all stages complete, STOP. Report completion and wait for human to say 'merge'."

In the SDLC decision matrix, change:
- Current: `PR approved, docs done → Report ready for human merge`
- New: `PR approved, docs done → STOP. Report completion. Wait for explicit 'merge' instruction.`

#### 5. Mandatory published PR reviews

In `.claude/skills/do-pr-review/SKILL.md`:
- Add hard requirement: "You MUST post the review to GitHub using `gh pr review` or `gh pr comment`. A review that exists only in your output but not on the PR is NOT a review."
- Add validation step: after posting, verify the review exists with `gh pr view {number} --json reviews`

In `.claude/skills/sdlc/SKILL.md`:
- Before advancing past REVIEW stage, verify a review comment/review exists on the PR: `gh api repos/{owner}/{repo}/pulls/{number}/reviews --jq length`
- If no review exists on GitHub, re-invoke `/do-pr-review`

#### 6. Tech debt patching policy

In `.claude/skills/do-pr-review/SKILL.md`:
- Update the severity guidelines: tech debt items are "should fix before merge" (not "doesn't block merge")
- Nits: "fix unless genuinely subjective"

In `.claude/skills/sdlc/SKILL.md`:
- After REVIEW, if any tech debt or nits exist in the review, invoke `/do-patch` before DOCS
- Only skip patch if the review found zero issues (clean approval)

## Rabbit Holes

- **Redesigning the summarizer prompt**: The LLM prompt is fine for bullet generation. Don't rewrite it — just use template rendering for the structural parts.
- **Adding structured JSON output from Claude**: Tempting but overkill. The simple template approach achieves the same result without changing the agent.
- **Making stage progress interactive**: Buttons, reactions, etc. Just render text.
- **Caching session lookups**: The session is already in scope. Don't add Redis caching layers.

## Risks

### Risk 1: Always-summarize increases API costs
**Impact:** Every message hits Haiku, even short ones like "Done."
**Mitigation:** Short messages are cheap (few tokens). Haiku is fast and cheap. The quality improvement justifies the cost. Could add a floor (e.g., skip if < 20 chars) but probably not worth it.

### Risk 2: SendCallback signature change breaks callers
**Impact:** Existing code that registers callbacks needs updating
**Mitigation:** Make the session parameter optional with default `None`. Existing callers that don't pass it still work. Grep for all `register_callbacks` calls and update.

### Risk 3: Human gate slows down pipeline
**Impact:** Work sits waiting for Tom to say "merge"
**Mitigation:** This is the desired behavior. Tom explicitly asked for this gate. The pipeline does all automated work first, then stops.

## No-Gos (Out of Scope)

- Changing the classifier or coaching system
- Modifying auto-continue logic (already handled by PR #185)
- Adding new SDLC stages
- Redesigning the Telegram markdown rendering
- Two-phase message delivery (send placeholder, replace with final)

## Update System

No update system changes required — these are bridge-internal changes and skill file updates. Skill files sync via existing hardlinks.

## Agent Integration

No new MCP servers or tools needed. The changes are to the bridge response pipeline and skill definitions. The agent continues to produce output normally — the bridge handles formatting.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` with new always-summarize behavior and template rendering
- [ ] Update `docs/features/bridge-workflow-gaps.md` auto-continue section to note human merge gate
- [ ] Update `docs/features/README.md` index if descriptions change
- [ ] Code comments on template rendering in summarizer.py

## Success Criteria

- [ ] Every agent message goes through summarization (no threshold bypass)
- [ ] Every SDLC message includes stage progress line (rendered from AgentSession)
- [ ] Every SDLC message includes link footer when links exist
- [ ] Stage line and link footer are template-rendered (not LLM-generated)
- [ ] AgentSession flows through callback chain: job_queue → bridge → summarizer
- [ ] SDLC pipeline stops after REVIEW+DOCS and waits for human "merge" instruction
- [ ] PR reviews are always posted to GitHub (verified by SDLC dispatcher)
- [ ] Tech debt items from review trigger /do-patch before merge
- [ ] Existing auto-continue and stage-aware tests pass without modification
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (callback-threading)**
  - Name: callback-builder
  - Role: Thread AgentSession through SendCallback chain from job_queue to summarizer
  - Agent Type: builder
  - Resume: true

- **Builder (summarizer-template)**
  - Name: template-builder
  - Role: Remove threshold, implement SDLC template rendering, always-summarize
  - Agent Type: builder
  - Resume: true

- **Builder (sdlc-policy)**
  - Name: policy-builder
  - Role: Update SDLC dispatcher and do-pr-review skills for human gate and mandatory reviews
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: quality-validator
  - Role: Verify callbacks thread correctly, templates render, human gate works
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs)**
  - Name: docs-writer
  - Role: Update feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Thread AgentSession through callback chain
- **Task ID**: build-callbacks
- **Depends On**: none
- **Assigned To**: callback-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `SendCallback` type in `agent/job_queue.py` to include optional session parameter
- Update `send_to_chat()` to pass `agent_session` through `send_cb`
- Update callback registration in `bridge/telegram_bridge.py` to forward session
- Verify `send_response_with_files()` receives and passes session to summarizer

### 2. Remove threshold + implement SDLC template
- **Task ID**: build-template
- **Depends On**: build-callbacks
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `SUMMARIZE_THRESHOLD` and early return in `summarize_response()`
- Create `_compose_sdlc_message()` template function
- Make stage progress mandatory for SDLC jobs (render even if all pending)
- Make link footer mandatory for SDLC jobs (render whatever links exist)
- LLM only generates bullet content for SDLC messages

### 3. Update SDLC policy (human gate + mandatory reviews + tech debt patching)
- **Task ID**: build-policy
- **Depends On**: none
- **Assigned To**: policy-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `sdlc/SKILL.md`: add human merge gate — never auto-merge, always stop after REVIEW+DOCS
- Update `sdlc/SKILL.md`: verify PR review exists on GitHub before advancing past REVIEW
- Update `sdlc/SKILL.md`: trigger /do-patch when tech debt or nits found (not just blockers)
- Update `do-pr-review/SKILL.md`: mandatory GitHub posting, add verification step
- Update `do-pr-review/SKILL.md`: tech debt = "fix before merge", nits = "fix unless subjective"

### 4. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-callbacks, build-template, build-policy
- **Assigned To**: quality-validator
- **Agent Type**: validator
- **Parallel**: false
- Run existing tests: `pytest tests/test_auto_continue.py tests/test_stage_aware_auto_continue.py tests/test_agent_session_lifecycle.py -v`
- Verify AgentSession flows through callback chain (trace the code path)
- Verify template rendering produces correct format
- Verify skill files have human gate and mandatory review language

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/summarizer-format.md`
- Update `docs/features/bridge-workflow-gaps.md`
- Update `docs/features/README.md` index

### 6. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: quality-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/test_auto_continue.py -v` — Existing auto-continue tests
- `pytest tests/test_stage_aware_auto_continue.py -v` — Stage-aware tests
- `pytest tests/test_agent_session_lifecycle.py -v` — Lifecycle tests
- `black --check bridge/summarizer.py bridge/response.py agent/job_queue.py` — Formatting
- `ruff check bridge/summarizer.py bridge/response.py agent/job_queue.py` — Linting
