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

## Architectural Decision: Orchestrator vs Flat Session

### The Cross-Repo Problem

The current architecture launches ONE SDK session with `cwd=target_project`. When Tom messages in Dev: PsyOptimal, the bridge sets `cwd=/Users/valorengels/src/psyoptimal`. This creates a fundamental problem:

**What breaks when cwd != ai/:**
1. `python -m tools.session_progress` — every SDLC skill calls this, but `tools/` lives in the ai/ repo, not psyoptimal/
2. `agent/worktree_manager.py` — referenced by do-build, exists only in ai/
3. `scripts/post_merge_cleanup.py` — called by SDLC merge phase, exists only in ai/
4. `.claude/hooks/` — pre/post tool hooks reference ai/ repo paths
5. Session transcript storage, crash tracking, monitoring — all wired to ai/ repo's data/ directory
6. **On another machine entirely** — none of the ai/ infrastructure exists. User-level skills at `~/.claude/skills/` only work if the update script installed them AND if the tools they reference are accessible.

**What works regardless of cwd:**
- User-level skills discovery (with `setting_sources=["user", ...]`)
- The SDLC skill text itself (it's just markdown instructions)
- gh CLI commands (global)
- git operations (per-repo)

### Option A: Flat Session (Current) — Fix tools paths

Keep one session, but make all tool paths absolute:
```python
# Instead of: python -m tools.session_progress
# Use:        python -m tools.session_progress  (with PYTHONPATH set to ai/)
```

**Pros:** Minimal change, no architectural shift
**Cons:** Fragile. Every skill must know about ai/ paths. On another machine, PYTHONPATH won't resolve. Couples all projects to the ai/ repo's filesystem layout.

### Option B: Orchestrator + Worker Architecture (Recommended)

The top-level SDK session ALWAYS runs with `cwd=ai/`. This is the **orchestrator**. It has full access to:
- All SDLC skills and their tool dependencies
- Session progress tracking
- Worktree management
- Hooks and monitoring

For code-touching SDLC stages (BUILD, TEST, PATCH), the orchestrator spawns **worker agents** via Task tool with the target project's working directory:

```
Message: "Fix the auth bug in PsyOptimal"
  ↓
Orchestrator (cwd=ai/)
  ├─ ISSUE: gh issue create --repo yudame/psyoptimal (runs from ai/, uses gh CLI)
  ├─ PLAN: writes docs/plans/ in ai/ (orchestrator's concern)
  ├─ BUILD: Task(cwd=psyoptimal/, prompt="implement the plan...")
  │    └─ Worker agent operates in psyoptimal/, creates branch, writes code
  ├─ TEST: Task(cwd=psyoptimal/, prompt="run tests...")
  ├─ REVIEW: gh pr view (runs from ai/, uses gh CLI)
  ├─ DOCS: Task(cwd=psyoptimal/, prompt="update docs in the project...")
  └─ MERGE: gh pr merge (runs from ai/, uses gh CLI)
```

**Pros:**
- SDLC infrastructure always works (it's in its home repo)
- Clean separation: orchestration logic vs project code
- Works on any machine — orchestrator just needs the ai/ repo
- Worker agents are disposable — they only need the target repo
- Already partially implemented — do-build spawns Task agents with worktree paths

**Cons:**
- Requires changing how `get_agent_response_sdk()` determines working directory
- Plan docs live in ai/ repo, not the target project
- Extra agent spawn overhead for simple tasks

### Option C: Hybrid — Orchestrator for SDLC, Direct for Q&A

Combine A and B:
- **Q&A / non-work messages**: Launch directly in the target project's cwd (fast, no overhead)
- **Work requests (SDLC)**: Launch orchestrator in ai/ cwd, which spawns workers in target project

This maps naturally to the work request classifier from Layer 1:
```python
classification = classify_work_request(message)
if classification == "sdlc":
    working_dir = AI_REPO_ROOT  # Orchestrator in ai/
else:
    working_dir = project["working_directory"]  # Direct in target
```

### Recommendation: Option C (Hybrid)

Option C gives us the best of both worlds:
- SDLC work is reliable because the orchestrator always runs from ai/ where all infrastructure lives
- Q&A is fast because it runs directly in the target project (no orchestrator overhead)
- The work request classifier (Layer 1) naturally doubles as the routing decision for cwd
- On another machine, SDLC still works as long as the ai/ repo is cloned and updated
- Backward compatible — the change is in `get_agent_response_sdk()`, not the SDK itself

**Key implementation detail:** The orchestrator doesn't need to "spin up a separate agent" for every SDLC stage. Most stages (ISSUE, PLAN, REVIEW, MERGE) use gh CLI and git, which work from any directory. Only BUILD, TEST, and PATCH need worker agents in the target project's directory. The do-build skill ALREADY spawns Task agents with explicit working directories — we just need to make this the consistent pattern.

## Solution

### Key Elements

Four layers of improvement, each independent and shippable:

- **Layer 0: Orchestrator architecture** — SDLC sessions run from ai/ repo, spawning workers in target project for code stages
- **Layer 1: Bridge-side work request pre-routing** — Classify incoming messages and route SDLC to orchestrator vs Q&A to target project
- **Layer 2: System prompt SDLC dominance** — Move SDLC from "guidance" to "hard constraint" in the prompt hierarchy
- **Layer 3: Summarizer reliability** — Eliminate process narration, ensure template always renders for SDLC work

### Layer 0: Orchestrator Architecture

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

**What this enables:**
- `tools/session_progress.py` always resolves (it's in ai/)
- SDLC skills can call `python -m tools.*` without path hacks
- Hooks in `.claude/hooks/` fire correctly
- do-build already spawns Task agents with explicit worktree paths — same pattern works for cross-repo builds where the Task agent gets `cwd=psyoptimal/`
- For the ai/ project itself (Valor), behavior is unchanged since cwd was already ai/

**What changes for SDLC skills:**
- `/sdlc` SKILL.md needs to know the target repo (passed via enriched message context)
- `gh issue create` / `gh pr create` need `--repo` flag when target != ai/
- `/do-build` spawns workers in the target project's directory (already does this via worktrees)
- Plans still live in ai/ `docs/plans/` (they're orchestration artifacts, not project code)

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

**Classification approach:** Use local Ollama (fast, no API cost) with a focused prompt. Fall back to regex patterns if Ollama is down.

**Regex fallback patterns for "sdlc":**
- Starts with `/sdlc`, `/do-plan`, `/do-build` → "passthrough" (already routed)
- Contains "fix", "add", "implement", "create", "refactor", "build", "update", "change" + code/feature/bug context → "sdlc"
- Contains "?", "what", "how", "why", "can you explain" → "question"
- "continue", "merge", "👍" → "passthrough"

**Why this works:** The classifier naturally splits the routing: work goes to the orchestrator (ai/ cwd), questions go to the target project (direct cwd). The agent still has agency within each path.

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

**Work request for any project:**
```
Telegram message → bridge/routing.py classify_work_request() → "sdlc"
  → SDK launches with cwd=ai/ (orchestrator)
  → Enriched message includes TARGET REPO path + GITHUB org/repo
  → Orchestrator invokes /sdlc skill → Pipeline runs:
    - ISSUE/PLAN/REVIEW/MERGE: orchestrator handles directly (gh CLI, docs/)
    - BUILD/TEST/PATCH: Task agent spawned with cwd=target_project/
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

- Layer 0 is the architectural foundation — ensures SDLC infrastructure is always accessible regardless of target project
- Layer 1 determines both the SDLC directive and the cwd routing — one classifier, two decisions
- Layer 2 reinforces at the prompt level — belt and suspenders
- Layer 3 is output quality — ensures the template renders when it should
- Layers can be built incrementally: 0+1 together (they're coupled), then 2, then 3

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

### Risk 4: Orchestrator loses target project context
**Impact:** Agent running in ai/ doesn't have psyoptimal's CLAUDE.md, .claude/settings.json, or project-specific context
**Mitigation:** Worker agents spawned via Task for BUILD/TEST/PATCH run in the target project's directory and get full project context. The orchestrator only needs the GitHub repo identifier and working directory path, both of which are in `config/projects.json`.

## No-Gos (Out of Scope)

- SDK modifications or custom wrappers
- Redesigning the summarizer's LLM+Python architecture
- Adding new SDLC stages or changing the pipeline itself
- Building a complex ML-based message classifier
- Moving plan docs into target project repos (plans stay in ai/)

## Update System

No update system changes required — these are bridge-internal code changes that deploy with the normal git pull workflow.

## Agent Integration

No new MCP server or tool needed. Changes are to:
- `bridge/routing.py` — new `classify_work_request()` function
- `agent/sdk_client.py` — orchestrator cwd routing + prompt ordering + pre-routing integration
- `bridge/summarizer.py` — process stripping pre-pass + threshold change
- `bridge/response.py` — threshold change
- `.claude/skills/sdlc/SKILL.md` — update to accept TARGET_REPO context from enriched message
- `.claude/skills/do-build/WORKFLOW.md` — ensure worker agents use target project cwd

The orchestrator pattern means SDLC skills and their tool dependencies (`tools/session_progress.py`, etc.) are always accessible because the orchestrator runs from ai/. Worker agents spawned for BUILD/TEST/PATCH run in the target project's directory.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` with process stripping behavior
- [ ] Create `docs/features/work-request-routing.md` describing the pre-routing system
- [ ] Update `docs/features/README.md` index table
- [ ] Update CLAUDE.md "Development Workflow" section to reference auto-routing

## Success Criteria

- [ ] SDLC work requests run orchestrator in ai/ cwd (not target project)
- [ ] Q&A messages run directly in target project cwd
- [ ] Work requests in natural language auto-route through SDLC without explicit "/sdlc" command
- [ ] SDLC works for non-ai projects (PsyOptimal, Django Template, etc.) — tools resolve correctly
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

### 2. Implement orchestrator routing + pre-routing in SDK client
- **Task ID**: build-orchestrator
- **Depends On**: build-classifier
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- In `get_agent_response_sdk()`: use classifier result to set cwd (ai/ for sdlc, target for question)
- Prepend SDLC directive with TARGET_REPO context for work requests
- Update SDLC skill to read TARGET_REPO from enriched message and pass `--repo` to gh commands
- Add logging for routing decisions (cwd chosen, classification result)

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

4. **Where should plan docs live for non-ai projects?** Currently all plans live in ai/ `docs/plans/`. This makes sense — plans are orchestration artifacts, not project code. But should we eventually support project-local plans? Recommendation: keep in ai/ for now, revisit later.

5. **Should the orchestrator also handle "continue" messages for SDLC sessions?** When Tom replies "continue" to an SDLC thread, the session should resume in ai/ (not the target project). The session context from Redis should carry the original classification. Recommendation: yes, this falls naturally from session-based routing.
