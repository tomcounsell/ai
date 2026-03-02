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

4. **Cross-repo SDLC breaks.** When cwd is a target project (not ai/), SDLC infrastructure fails:
   - `python -m tools.session_progress` — lives in ai/, not the target project
   - `agent/worktree_manager.py` — exists only in ai/
   - `.claude/hooks/` — reference ai/ repo paths
   - Session storage, crash tracking, monitoring — all wired to ai/
   - On another machine — none of the ai/ infrastructure exists

**Desired outcome:**

1. Work requests auto-route through SDLC without explicit "/sdlc" commands
2. SDLC works reliably for ANY project, not just ai/
3. Target project context (CLAUDE.md, docs/*, architecture) is never polluted by ai/ repo context
4. Every SDLC response shows structured template (emoji + stage line + bullets + link footer)
5. Process narration ("Let me...") never reaches Telegram
6. Non-work messages (questions, status checks) bypass SDLC normally

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (plan review)
- Review rounds: 1-2

## Prerequisites

No prerequisites — all changes are internal to the bridge and agent code.

## Architecture: Hybrid Thin Orchestrator

**For SDLC work:** A thin orchestrator runs in ai/ repo (cwd=ai/). It handles ONLY stage sequencing, progress tracking, and gh CLI commands. ALL stages requiring target project context spawn worker agents in the target repo's directory.

**For Q&A:** Sessions launch directly in the target project's cwd — fast, no overhead, full project context.

```
Work request: "Fix the auth bug in PsyOptimal"
  ↓
Thin Orchestrator (cwd=ai/) — dispatch + progress tracking ONLY
  ├─ ISSUE: gh issue create --repo yudame/psyoptimal (gh CLI, no project context needed)
  ├─ PLAN: Worker(cwd=psyoptimal/) reads CLAUDE.md, docs/* → writes plan to psyoptimal/docs/plans/
  ├─ BUILD: Worker(cwd=psyoptimal/) implements plan, creates branch, writes code
  ├─ TEST: Worker(cwd=psyoptimal/) runs test suite
  ├─ PATCH: Worker(cwd=psyoptimal/) fixes failures
  ├─ REVIEW: gh pr view --repo (gh CLI, no project context needed)
  ├─ DOCS: Worker(cwd=psyoptimal/) updates project docs
  └─ MERGE: gh pr merge --repo (gh CLI, no project context needed)

Question: "How does auth work in PsyOptimal?"
  ↓
Direct session (cwd=psyoptimal/) — full project context, no orchestrator overhead
```

**The thin orchestrator principle:** Any step that needs to understand the target project's code, architecture, conventions, or documentation MUST run as a worker agent in the target repo's directory. The orchestrator only handles:
- Stage sequencing and progress tracking (`tools/session_progress.py`)
- gh CLI commands that just need `--repo` flag (ISSUE, REVIEW, MERGE)
- Routing decisions and session management

**Why this works:**
- SDLC infrastructure always resolves (it's in its home repo)
- Workers get full project context — CLAUDE.md, docs/*, .claude/settings.json
- No ai/ context pollution — workers never see ai/'s CLAUDE.md or README
- Works on any machine — orchestrator just needs the ai/ repo
- Already partially implemented — do-build spawns Task agents with worktree paths
- Q&A stays fast — no orchestrator overhead for questions
- Backward compatible — change is in `get_agent_response_sdk()`, not the SDK

## Solution

### Key Elements

Four layers of improvement, each independent and shippable:

- **Layer 0: Thin orchestrator** — SDLC sessions run from ai/ for dispatch + tracking only; ALL context-dependent stages (PLAN, BUILD, TEST, PATCH, DOCS) spawn workers in target project
- **Layer 1: Bridge-side pre-routing** — Classify incoming messages and route SDLC to orchestrator vs Q&A to target project
- **Layer 2: System prompt SDLC dominance** — Move SDLC from "guidance" to "hard constraint" in the prompt hierarchy
- **Layer 3: Summarizer reliability** — Eliminate process narration, ensure template always renders for SDLC work

### Layer 0: Thin Orchestrator

**The key change:** In `agent/sdk_client.py` `get_agent_response_sdk()`, the working directory decision becomes classification-dependent:

```python
AI_REPO_ROOT = str(Path(__file__).parent.parent)  # /Users/valorengels/src/ai

classification = classify_work_request(message)
if classification == "sdlc":
    # Orchestrator runs in ai/ repo where all SDLC infrastructure lives
    working_dir = AI_REPO_ROOT
    # Tell the agent which project it's working on and where the code lives
    enriched_message = (
        f"WORK REQUEST for project {project_name}.\n"
        f"TARGET REPO: {project['working_directory']}\n"
        f"GITHUB: {project['github']['org']}/{project['github']['repo']}\n"
        f"Invoke /sdlc immediately.\n\n{enriched_message}"
    )
else:
    # Q&A runs directly in the target project for fast context
    working_dir = project.get("working_directory", AI_REPO_ROOT)
```

**What the thin orchestrator handles (in ai/ cwd):**
- Stage sequencing: decide which stage to run next
- Progress tracking: `tools/session_progress.py` updates
- gh CLI commands: `gh issue create --repo`, `gh pr view --repo`, `gh pr merge --repo`
- Session management: logging, crash tracking, monitoring

**What workers handle (in target project cwd):**
- PLAN: Read target's CLAUDE.md, docs/*, architecture → write plan in target project's docs/plans/
- BUILD: Create branch, write code using target project's patterns and conventions
- TEST: Run target project's test suite
- PATCH: Fix failures using target project context
- DOCS: Update target project's documentation

**What changes for SDLC skills:**
- `/sdlc` becomes a thin dispatcher that spawns workers for context-dependent stages
- `/do-plan` runs as worker in target project — sees all project docs and conventions
- `/do-build` already spawns workers in target directory (via worktrees) — same pattern
- `gh` commands use `--repo` flag when target != ai/
- Plans stored in the target project's `docs/plans/` directory (not ai/ unless working on ai/)

### Layer 1: Bridge-Side Work Request Pre-Routing

Add a lightweight classifier in the bridge that runs BEFORE the message reaches Claude. This serves double duty: it determines the SDLC directive AND the working directory.

**Location:** New function in `bridge/routing.py`

```python
def classify_work_request(message: str) -> str:
    """Classify if a message is a work request that should go through SDLC.

    Returns:
        "sdlc" - Work request → orchestrator in ai/, prepend SDLC directive
        "question" - Q&A → direct in target project, pass through as-is
        "passthrough" - Already has skill invocation or is conversational
    """
```

**Classification approach:** Ollama (fast, local) with fallback to Haiku (cheap, reliable). Must reuse a single Ollama session — never spawn per-request. If Ollama shows any memory leak risk, default to Haiku.

**Fast-path patterns (before LLM):**
- Starts with `/sdlc`, `/do-plan`, `/do-build` → "passthrough" (already routed)
- "continue", "merge", "👍" → "passthrough"
- These bypass the LLM entirely for zero-latency routing.

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

4. Update SOUL.md (`config/SOUL.md`) to reinforce identity as a responsible senior developer who can answer questions directly, but defaults to the professional SDLC process for any meaningful work. This is character-level reinforcement — Valor WANTS to use SDLC because it's the right thing to do, not because he's forced to.

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

**Work request for any project:**
```
Telegram message → bridge/routing.py classify_work_request() → "sdlc"
  → SDK launches with cwd=ai/ (thin orchestrator)
  → Enriched message includes TARGET REPO path + GITHUB org/repo
  → Orchestrator invokes /sdlc skill → Pipeline runs:
    - ISSUE: orchestrator runs gh CLI (no project context needed)
    - PLAN: Worker(cwd=target/) reads codebase → writes plan in target/docs/plans/
    - BUILD: Worker(cwd=target/) implements plan, creates branch
    - TEST: Worker(cwd=target/) runs test suite
    - PATCH: Worker(cwd=target/) fixes failures
    - REVIEW: orchestrator runs gh CLI
    - DOCS: Worker(cwd=target/) updates project docs
    - MERGE: orchestrator runs gh CLI
  → Output produced → summarizer renders SDLC template → Telegram
```

**Question for any project:**
```
Telegram message → classify_work_request() → "question"
  → SDK launches with cwd=target_project/ (direct access)
  → Agent answers using project's codebase context
  → summarizer condenses if long → Telegram
```

### Technical Approach

- Layer 0 is the architectural foundation — thin orchestrator in ai/ for dispatch + tracking, workers in target project for all context-dependent stages
- Layer 1 determines both the SDLC directive and the cwd routing — one classifier, two decisions
- Layer 2 reinforces at the prompt level — belt and suspenders
- Layer 3 is output quality — ensures the template renders when it should
- Layers can be built incrementally: 0+1 together (they're coupled), then 2, then 3

## Rabbit Holes

- **Do NOT build a complex NLP classifier** — Ollama with Haiku fallback is sufficient for v1. Keep the prompt simple and focused.
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

### Risk 4: Context pollution from ai/ repo
**Impact:** If the orchestrator does too much, ai/ repo's CLAUDE.md and docs pollute the agent's understanding of the target project
**Mitigation:** Thin orchestrator principle — the orchestrator ONLY dispatches and tracks progress. ALL context-dependent stages (PLAN, BUILD, TEST, PATCH, DOCS) run as workers in the target project's directory with full project context. The orchestrator never reads or reasons about target project code.

## No-Gos (Out of Scope)

- SDK modifications or custom wrappers
- Redesigning the summarizer's LLM+Python architecture
- Adding new SDLC stages or changing the pipeline itself
- Building a complex ML-based message classifier
- Storing non-ai project plans in ai/docs/plans/ (plans live in the target project's repo alongside their GitHub issues)
- **Flat session with absolute paths** — fragile, couples all projects to ai/ filesystem layout, breaks on other machines
- **Full orchestrator (all stages in ai/)** — causes ai/ context pollution in planning/building stages that need target project context

## Update System

No update system changes required — these are bridge-internal code changes that deploy with the normal git pull workflow.

## Agent Integration

No new MCP server or tool needed. Changes are to:
- `bridge/routing.py` — new `classify_work_request()` function
- `agent/sdk_client.py` — orchestrator cwd routing + prompt ordering + pre-routing integration
- `config/SOUL.md` — reinforce SDLC-first identity (responsible senior dev who defaults to process)
- `bridge/summarizer.py` — process stripping pre-pass + threshold change
- `bridge/response.py` — threshold change
- `.claude/skills/sdlc/SKILL.md` — update to accept TARGET_REPO context from enriched message
- `.claude/skills/do-build/WORKFLOW.md` — ensure worker agents use target project cwd
- `.claude/skills/do-plan/` — update to write plans in target project's docs/plans/

The orchestrator pattern means SDLC skills and their tool dependencies (`tools/session_progress.py`, etc.) are always accessible because the orchestrator runs from ai/. Worker agents spawned for PLAN/BUILD/TEST/PATCH/DOCS run in the target project's directory.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` with process stripping behavior
- [ ] Create `docs/features/work-request-routing.md` describing the pre-routing and orchestrator system
- [ ] Update `docs/features/README.md` index table
- [ ] Update CLAUDE.md "Development Workflow" section to reference auto-routing

## Success Criteria

- [ ] SDLC work requests run thin orchestrator in ai/ cwd (not target project)
- [ ] Q&A messages run directly in target project cwd
- [ ] Work requests in natural language auto-route through SDLC without explicit "/sdlc" command
- [ ] SDLC works for non-ai projects (PsyOptimal, Django Template, etc.) — tools resolve correctly
- [ ] Workers see target project's CLAUDE.md and docs, NOT ai/'s
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
  - Role: Implement work request classifier, orchestrator routing, and bridge integration
  - Agent Type: builder
  - Resume: true

- **Builder (prompt-and-summarizer)**
  - Name: prompt-builder
  - Role: Reorder system prompt, add process stripping, fix thresholds
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Verify all layers work together
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
- Implement Ollama-based classification with Haiku fallback
- Reuse single Ollama session (no per-request spawning) — verify no memory leaks
- Fast-path patterns for `/sdlc`, `continue`, `👍` bypass LLM entirely
- Add unit tests for classification accuracy

### 2. Implement thin orchestrator routing in SDK client
- **Task ID**: build-orchestrator
- **Depends On**: build-classifier
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- In `get_agent_response_sdk()`: use classifier result to set cwd (ai/ for sdlc, target for question)
- Prepend SDLC directive with TARGET_REPO context for work requests
- Update SDLC skill to be a thin dispatcher: gh CLI for ISSUE/REVIEW/MERGE, worker agents for PLAN/BUILD/TEST/PATCH/DOCS
- Ensure /do-plan runs as worker in target project cwd (reads target's CLAUDE.md, docs/*, architecture)
- Log classification decisions to Redis for later analysis
- Add logging for routing decisions (cwd chosen, classification result)

### 3. Reorder system prompt + update SOUL.md
- **Task ID**: build-prompt
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Move SDLC_WORKFLOW to top of system prompt in `load_system_prompt()`
- Strengthen SDLC language from "if" to "must"
- Add negative examples
- Update `config/SOUL.md` to reinforce Valor as a responsible senior developer who defaults to SDLC for meaningful work

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
- **Depends On**: build-orchestrator, build-prompt, build-strip
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

## Open Questions (Resolved)

1. **Classifier approach:** Ollama with fallback to Haiku. Must verify Ollama sessions don't leak memory — if any risk, default to Haiku (cheap enough). Single Ollama session reuse, not per-request spawning.

2. **False-positive SDLC routing correctable?** Yes, VERY lightweight — the directive is a strong nudge, not a hard gate. Agent can skip SDLC if it determines the message is actually a question.

3. **SOUL.md reinforcement:** Update SOUL.md to reinforce that Valor is a responsible senior developer who can answer questions directly, but defaults to using the professional SDLC process for any meaningful work.

4. **Plan doc location:** Plans live in the TARGET PROJECT's directory, NOT in ai/docs/plans/ (unless we're working on the ai/ repo itself). This matters because plan links appear in GitHub issues of the same repo and should live within the same contextual area.

5. **"Continue" command routing:** Continue commands work identically whether from Tom or the stop hook. Session context in Redis carries the original classification — both sources trigger the same resume path.
