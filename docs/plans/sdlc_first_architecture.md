---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/227
---

# SDLC-First Agent Architecture

## Problem

The SDLC pipeline is supposed to be the mandatory development workflow, but it's architecturally treated as one of 25 equal skills. The summarizer has a well-designed SDLC template that renders ~10% of the time.

**Current behavior:**

1. **Agent ignores SDLC for natural-language work requests.** Tom says "fix the login bug" → agent investigates directly, creates issues manually, starts coding. Only invokes /sdlc when Tom explicitly types "/sdlc issue 123". The system prompt says to use SDLC but the agent treats it as optional.

2. **Summarizer output is inconsistent.** Audit found (docs/audits/summarizer-output-audit.md):
   - 30% verbose process dumps ("Let me check...", "Now let me read...")
   - 24% unsummarized raw output
   - 10% stage progress line compliance
   - 10% link footer compliance

3. **No pre-routing.** Every message hits the same code path regardless of whether it's a work request, a question, or a status check. The agent decides what to do — and it decides wrong ~60% of the time for work requests.

**Desired outcome:**

1. Work requests auto-route through SDLC without explicit "/sdlc" commands
2. Every SDLC response shows structured template (emoji + stage line + bullets + link footer)
3. Process narration ("Let me...") never reaches Telegram
4. Non-work messages (questions, status checks) bypass SDLC normally

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (plan review)
- Review rounds: 1-2

## Prerequisites

No prerequisites — all changes are internal to the bridge and agent code.

## Solution

### Key Elements

Three layers of improvement, each independent and shippable:

- **Layer 1: Bridge-side work request pre-routing** — Classify incoming messages and auto-prepend SDLC directive for work requests
- **Layer 2: System prompt SDLC dominance** — Move SDLC from "guidance" to "hard constraint" in the prompt hierarchy
- **Layer 3: Summarizer reliability** — Eliminate process narration, ensure template always renders for SDLC work

### Layer 1: Bridge-Side Work Request Pre-Routing

Add a lightweight classifier in the bridge that runs BEFORE the message reaches Claude. This is the most impactful change.

**Location:** New function in `bridge/routing.py`

```python
def classify_work_request(message: str) -> str:
    """Classify if a message is a work request that should go through SDLC.

    Returns:
        "sdlc" - Work request → prepend SDLC directive
        "question" - Q&A → pass through as-is
        "passthrough" - Already has skill invocation or is conversational
    """
```

**Classification approach:** Use local Ollama (fast, no API cost) with a focused prompt. Fall back to regex patterns if Ollama is down.

**Regex fallback patterns for "sdlc":**
- Starts with `/sdlc`, `/do-plan`, `/do-build` → "passthrough" (already routed)
- Contains "fix", "add", "implement", "create", "refactor", "build", "update", "change" + code/feature/bug context → "sdlc"
- Contains "?", "what", "how", "why", "can you explain" → "question"
- "continue", "merge", "👍" → "passthrough"

**Integration point:** `agent/sdk_client.py` `get_agent_response_sdk()` — after building `enriched_message`, before calling `agent.query()`:

```python
from bridge.routing import classify_work_request

classification = classify_work_request(message)
if classification == "sdlc":
    # Prepend SDLC directive so the agent's first action is /sdlc
    enriched_message = f"WORK REQUEST DETECTED — invoke /sdlc immediately.\n\n{enriched_message}"
```

**Why this works:** The agent still has agency, but the message framing makes SDLC the default action instead of an option. It's a nudge, not a hard gate — questions and conversations flow normally.

### Layer 2: System Prompt SDLC Dominance

Currently SDLC rules are appended after SOUL.md (line 206 in sdk_client.py). They need to be more prominent.

**Changes to `agent/sdk_client.py`:**

1. Move SDLC_WORKFLOW to the TOP of the system prompt, before SOUL.md:
```python
def load_system_prompt() -> str:
    soul_prompt = SOUL_PATH.read_text() if SOUL_PATH.exists() else "..."
    # SDLC rules FIRST — they take precedence
    return f"{SDLC_WORKFLOW}\n\n---\n\n{soul_prompt}\n\n---\n\n{criteria}"
```

2. Strengthen the SDLC_WORKFLOW language from "if" to "must":
```
BEFORE: "If no issue number but it's clearly work → invoke /sdlc"
AFTER:  "ANY work request MUST go through /sdlc. You MUST invoke /sdlc
         BEFORE writing any code, creating any issues, or making any changes.
         The ONLY exception is answering questions."
```

3. Add concrete negative examples:
```
NEVER DO THIS:
- "Let me investigate..." → then start coding → then create an issue
- "I'll fix this..." → then make changes directly on main

ALWAYS DO THIS:
- Receive work request → invoke /sdlc → let the pipeline handle it
```

### Layer 3: Summarizer Reliability

Three sub-fixes:

**3a. Process narration stripping (new pre-pass in summarizer.py):**

Add a `_strip_process_narration()` function that runs BEFORE the LLM summarizer:
```python
PROCESS_PATTERNS = [
    r"^Let me .*[.:]$",
    r"^Now let me .*[.:]$",
    r"^I'll .*[.:]$",
    r"^First,? (?:let me|I'll) .*[.:]$",
    r"^Good\.$",
    r"^Now I .*[.:]$",
    r"^Looking at .*[.:]$",
]
```
Strip these lines from the raw output before passing to Haiku. This ensures the LLM only sees outcomes, not process.

**3b. Session freshness guarantee (already partially fixed in PR #226):**

The PR #226 fix re-reads session from Redis. Verify this is working and add a retry: if `get_stage_progress()` returns all-pending but `is_sdlc_job()` is True based on message text containing "/sdlc", wait 1 second and re-read. Stage data may not have been written yet by the time the final output arrives.

**3c. Lower summarization threshold:**

Change `should_summarize` from `len(text) >= 500` to ALWAYS summarize for SDLC sessions (already done) and lower the non-SDLC threshold from 500 to 200 chars. The audit showed short but verbose messages slipping through.

### Flow

**Work request arrives:**
```
Telegram message → bridge/routing.py classify_work_request()
  → "sdlc" → prepend directive → SDK client → Agent sees "WORK REQUEST DETECTED — invoke /sdlc"
  → Agent invokes /sdlc skill → Pipeline runs → Output produced
  → send_to_chat() → response.py strips process narration → summarizer re-reads session
  → _compose_structured_summary() renders template → Telegram delivery
```

**Question arrives:**
```
Telegram message → bridge/routing.py classify_work_request()
  → "question" → pass through unchanged → SDK client → Agent answers directly
  → send_to_chat() → summarizer condenses if long → Telegram delivery
```

### Technical Approach

- Layer 1 is the highest-impact change — auto-routing makes SDLC the default without removing agent autonomy
- Layer 2 reinforces Layer 1 at the prompt level — belt and suspenders
- Layer 3 is output quality — ensures the template renders when it should
- Each layer is independently shippable and testable

## Rabbit Holes

- **Do NOT build a complex NLP classifier** — regex + Ollama fallback is sufficient for v1. The patterns are obvious: "fix", "add", "implement" = work.
- **Do NOT add SDK-level skill filtering** — the SDK has no API for this, and building a wrapper is overengineered. Prompt-level enforcement is sufficient.
- **Do NOT redesign the summarizer architecture** — the LLM-generates-bullets + Python-renders-structure pattern is sound. Fix the inputs (session freshness, process stripping), not the architecture.
- **Do NOT try to intercept every possible edge case** — a few false positives (question classified as work) or false negatives (work classified as question) are fine. The agent still has agency.

## Risks

### Risk 1: False positive work classification
**Impact:** Question gets "/sdlc" prepended, agent tries to create an issue for a question
**Mitigation:** Use "passthrough" as default for ambiguous messages. Only classify as "sdlc" on high-confidence patterns. Agent can still answer questions even with the prepended directive — it just adds a small amount of wasted tokens.

### Risk 2: System prompt ordering breaks SOUL.md behavior
**Impact:** SDLC rules dominate so much that conversational behavior suffers
**Mitigation:** SDLC rules are only ~30 lines. They don't override personality or communication style — they only constrain the workflow for code changes. Test with both work and Q&A messages.

### Risk 3: Process stripping removes meaningful content
**Impact:** "Let me explain..." lines that ARE the answer get stripped
**Mitigation:** Only strip lines that match process patterns AND are followed by more content. Don't strip if the line is the only content. Test with real examples from the audit.

## No-Gos (Out of Scope)

- Per-project skill visibility (all user-level skills remain shared)
- SDK modifications or custom wrappers
- Redesigning the summarizer's LLM+Python architecture
- Adding new SDLC stages or changing the pipeline itself
- Building a complex ML-based message classifier

## Update System

No update system changes required — these are bridge-internal code changes that deploy with the normal git pull workflow.

## Agent Integration

No new MCP server or tool needed. Changes are to:
- `bridge/routing.py` — new `classify_work_request()` function
- `agent/sdk_client.py` — prompt ordering + pre-routing integration
- `bridge/summarizer.py` — process stripping pre-pass + threshold change
- `bridge/response.py` — threshold change

The agent calls these indirectly through the existing bridge pipeline.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` with process stripping behavior
- [ ] Create `docs/features/work-request-routing.md` describing the pre-routing system
- [ ] Update `docs/features/README.md` index table
- [ ] Update CLAUDE.md "Development Workflow" section to reference auto-routing

## Success Criteria

- [ ] Work requests in natural language auto-route through SDLC without explicit "/sdlc" command
- [ ] SDLC responses consistently show stage progress line + link footer (>80% compliance)
- [ ] Process narration ("Let me...", "Now let me...") stripped before reaching Telegram
- [ ] Questions and conversational messages still flow normally (no false-positive SDLC routing)
- [ ] System prompt has SDLC rules before SOUL.md
- [ ] Non-SDLC summarization threshold lowered to 200 chars
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement work request classifier and bridge integration
  - Agent Type: builder
  - Resume: true

- **Builder (prompt-and-summarizer)**
  - Name: prompt-builder
  - Role: Reorder system prompt, add process stripping, fix thresholds
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Verify all three layers work together
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build work request classifier
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classify_work_request()` to `bridge/routing.py`
- Implement Ollama-based classification with regex fallback
- Add unit tests for classification accuracy

### 2. Integrate pre-routing in SDK client
- **Task ID**: build-prerouting
- **Depends On**: build-classifier
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Wire classifier into `get_agent_response_sdk()` in `agent/sdk_client.py`
- Prepend SDLC directive for work requests
- Add logging for routing decisions

### 3. Reorder system prompt
- **Task ID**: build-prompt
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Move SDLC_WORKFLOW to top of system prompt in `load_system_prompt()`
- Strengthen SDLC language from "if" to "must"
- Add negative examples

### 4. Add process narration stripping
- **Task ID**: build-strip
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_strip_process_narration()` to `bridge/summarizer.py`
- Integrate into `summarize_response()` pipeline
- Lower non-SDLC threshold to 200 chars
- Add tests with real examples from audit

### 5. Validate all layers
- **Task ID**: validate-all
- **Depends On**: build-prerouting, build-prompt, build-strip
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify classifier accuracy with sample messages
- Verify system prompt ordering
- Verify process stripping removes narration
- Verify SDLC template renders with fresh session data
- Run full test suite

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/work-request-routing.md`
- Update `docs/features/summarizer-format.md`
- Update `docs/features/README.md`

### 7. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `grep -n 'classify_work_request' bridge/routing.py` — classifier exists
- `grep -n 'SDLC_WORKFLOW' agent/sdk_client.py | head -5` — prompt ordering
- `grep -n '_strip_process_narration' bridge/summarizer.py` — stripping exists
- `grep -n 'WORK REQUEST DETECTED' agent/sdk_client.py` — pre-routing wired
- `pytest tests/ -x -q 2>&1 | tail -5` — tests pass

---

## Open Questions

1. **Should the work request classifier be Ollama-only, regex-only, or hybrid?** Hybrid recommended — Ollama for nuance, regex as fast fallback when Ollama is down.

2. **Should we log classification decisions to Redis for later analysis?** Useful for tuning the classifier. Recommended yes, lightweight.

3. **Should false-positive SDLC routing be correctable?** e.g., if the agent gets a work directive but realizes it's a question, should it be able to skip SDLC? Recommended yes — the directive is a strong nudge, not a hard gate.
