# Cursor IDE Lessons - Adapted for Valor's Autonomous Architecture

## Research Summary

Based on analysis of Cursor's 2026 architecture and user workflows, adapted for Valor's unique model: **Telegram messages trigger autonomous background work with no human in the loop**.

**Sources:**
- [How Cursor AI IDE Works](https://blog.sshh.io/p/how-cursor-ai-ide-works)
- [A Year with Cursor: Workflow Evolution](https://subramanya.ai/2026/01/04/a-year-with-cursor-how-my-workflow-evolved-from-agent-to-architect/)
- [Cursor 2.0 Has Arrived](https://medium.com/ai-software-engineer/cursor-2-0-has-arrived-and-agentic-ai-coding-just-got-wild-65bbbd3be4ec)

---

## Key Difference: Cursor vs Valor

| Aspect | Cursor | Valor |
|--------|--------|-------|
| Interface | Human actively types in IDE | Telegram message → autonomous work |
| Human in loop | Yes (during execution) | No (only at start/end) |
| Context control | Human attaches files via @ | Agent must intelligently discover context |
| Execution model | Synchronous with user | Background (2+ hours possible) |
| Feedback | Real-time | Via messenger when ready |

**Implication:** Many Cursor features assume real-time human interaction. We need different adaptations.

---

## What Cursor Does Well (And How We Can Adapt)

### 1. ✅ **Tool Calling Pattern**
**Cursor:** `read_file`, `write_file`, `run_command`, `codebase_search`
**Valor:** Already have via Claude Agent SDK
**Status:** No action needed

### 2. ❌ **Explicit Context Attachment (@file syntax)**
**Cursor:** Human types `@file:path` in IDE, IDE attaches file
**Valor:** No human typing during execution
**Adaptation needed:** Agent-initiated context gathering (see below)

### 3. ⚠️ **Semantic Search with Re-ranking**
**Cursor:** Human asks question → semantic search finds relevant code
**Valor:** Agent asks question autonomously → semantic search would help
**Status:** Could implement for agent's benefit

### 4. ❌ **Semantic Diff + Apply Model**
**Cursor:** Two-phase editing (plan diff → apply)
**Valor:** Agent SDK has Edit tool but doesn't prefer it
**Status:** Should guide agent to prefer Edit over Write

### 5. ✅ **Static System Prompt for Caching**
**Cursor:** Maximize prompt caching
**Valor:** Already using SOUL.md + project context
**Status:** Already optimized

### 6. ✅ **Rules System (.cursorrules)**
**Cursor:** Project-level coding standards
**Valor:** Have `projects.json` context
**Status:** Could enhance with `.valorrules` files

### 7. ⚠️ **Plan Mode with Model Selection**
**Cursor:** Opus for planning, Sonnet for execution
**Valor:** Use Sonnet for everything
**Status:** Could optimize (Opus for complex planning)

### 8. ❌ **Multi-Agent Parallel Execution**
**Cursor:** Run 8 agents in parallel
**Valor:** Single agent per session
**Status:** Could implement P-Thread pattern

---

## Viable Improvements for Valor's Autonomous Model

### **Feature 1: Prompt Agent to Prefer Edit Over Write** (Priority: High)

**Problem:** Agent uses Write for full file rewrites, wasteful for small changes

**Current behavior:**
```python
Write(file_path="bridge.py", content="[entire 2000 line file]")
```

**Better behavior:**
```python
Edit(file_path="bridge.py",
     old_string="def foo():\n    pass",
     new_string="def foo():\n    return True")
```

**Implementation:**
Add to SOUL.md:
```markdown
## File Editing Best Practices

**CRITICAL:** When modifying existing files:
1. Use the Edit tool for targeted changes (preferred)
2. Only use Write tool for:
   - Creating new files
   - Complete file restructures where >50% changes

**Why:** Edit is 10x faster, 80% cheaper, and clearer in diffs.

**Example:**
- ❌ Read entire file → modify → Write entire file
- ✅ Edit(old_string="...", new_string="...")
```

**Effort:** 30 minutes (prompt change)
**Impact:** High (cost + speed)
**Risk:** Low (just guidance, agent can still use Write if needed)

---

### **Feature 2: Per-Project `.valorrules` Files** (Priority: Medium)

**Problem:** All projects use same generic coding standards from SOUL.md

**Solution:** Let each project define specific standards

**Example `.valorrules` (project root):**
```yaml
# Valor Rules for Django Project Template

style:
  - "Always use type hints in function signatures"
  - "Max line length: 100 characters"
  - "Use dataclasses over dict for structured data"

testing:
  - "Write pytest test for every new function"
  - "Tests go in tests/ with test_ prefix"
  - "Use fixtures in conftest.py"

architecture:
  - "Models in app/models/"
  - "Views in app/views/"
  - "Templates in app/templates/"

git:
  - "Commit format: <type>: <description>"
  - "Always push after completing work"
  - "Merge to main when tests pass"
```

**Implementation:**
1. Bridge checks for `.valorrules` in project working directory
2. Parse YAML
3. Append to system prompt before sending to agent:
   ```
   ## Project-Specific Rules
   {parsed rules}
   ```

**Effort:** 2-3 hours
**Impact:** Medium (consistency across projects)
**Risk:** Low (just additional context)

---

### **Feature 3: Semantic Codebase Search for Agent** (Priority: Medium-High)

**Problem:** Agent uses grep (keyword matching), misses semantically relevant code

**Solution:** Pre-index codebase with embeddings, agent queries semantically

**How it works:**
1. **Indexing (on git commit):**
   - Chunk codebase into functions/classes
   - Generate embeddings (OpenAI or Voyage)
   - Store in SQLite with vector extension (or pgvector)

2. **Query time (agent asks):**
   - Agent: "Find authentication logic"
   - Embed query
   - Semantic search returns: `login.py`, `token_validator.py`, `auth_middleware.py`
   - Even if they don't contain word "auth"

3. **LLM re-ranking:**
   - Quick LLM pass to re-rank by relevance
   - Return top 5 to agent

**Benefits:**
- Agent finds code by meaning, not keywords
- Better context discovery
- Fewer false positives

**Implementation:**
1. Add SQLite with vector extension (or pgvector)
2. Create indexing script (runs post-commit)
3. Add `semantic_search` tool to agent's toolset
4. Update SOUL.md: "Prefer semantic_search over grep for concept queries"

**Effort:** 1-2 days
**Impact:** High (agent effectiveness)
**Risk:** Medium (infrastructure dependency, re-indexing overhead)

---

### **Feature 4: Model Selection Based on Task Complexity** (Priority: Medium)

**Problem:** Use Sonnet for everything - expensive for simple tasks, maybe not deep enough for complex planning

**Solution:** Route to appropriate model based on task

**Heuristics:**
- **Opus (deep reasoning):**
  - Message contains "plan", "design", "architecture"
  - Complex multi-file changes
  - Ambiguous requirements

- **Sonnet (balanced):**
  - Standard implementation
  - Bug fixes
  - Most day-to-day work

- **Haiku (fast/cheap):**
  - Simple queries
  - Code review
  - Documentation updates

**Implementation:**
1. Add `model` parameter to `ValorAgent.query()`
2. Bridge classifies task complexity (regex or Ollama)
3. Route to appropriate model

**Example:**
```python
# In bridge before calling agent
if "plan" in message.lower() or "design" in message.lower():
    model = "opus"
elif "review" in message.lower() or "document" in message.lower():
    model = "haiku"
else:
    model = "sonnet"

agent = ValorAgent(model=model)
```

**Effort:** 2-3 hours
**Impact:** Medium (cost optimization, better planning)
**Risk:** Low (can default to Sonnet if uncertain)

---

### **Feature 5: Intelligent Context Pre-Loading** (Priority: High)

**Problem:** Agent must discover relevant files via grep/glob, wastes time

**Solution:** Bridge intelligently pre-loads likely relevant context before calling agent

**How it works:**

**Phase 1: Simple keyword matching**
```python
# User: "Fix the auth bug in telegram_bridge"
# Bridge detects keywords:
keywords = ["auth", "telegram_bridge"]

# Pre-load related files:
- bridge/telegram_bridge.py (name match)
- config/SOUL.md (always include)
- Recent git log (for context)
- Any file with "auth" in recent commits
```

**Phase 2: Semantic context (if we build Feature 3)**
```python
# User: "Fix the authentication bug"
# Semantic search returns:
- bridge/telegram_bridge.py (has login logic)
- tools/telegram_history.py (stores auth sessions)
- config/projects.json (auth whitelist)

# Pre-attach these to message
```

**Implementation:**
1. Parse user message for file mentions
2. Check recent git commits for related files
3. If semantic search available, query for relevant files
4. Wrap in `<context>` block:
   ```xml
   <context>
   <file path="bridge/telegram_bridge.py">
   [contents]
   </file>
   <recent-commits>
   [last 5 commits touching auth]
   </recent-commits>
   </context>

   User message: Fix the authentication bug
   ```

**Effort:** 4-6 hours
**Impact:** High (faster context discovery)
**Risk:** Low (worst case agent ignores pre-loaded context)

---

### **Feature 6: Multi-Agent Parallel Execution (P-Threads)** (Priority: Low)

**Problem:** Large tasks that could parallelize still run sequentially

**Solution:** Detect parallelizable subtasks, spawn multiple agents

**When to use:**
- User explicitly requests parallel work
- Bridge detects independent subtasks
- Example: "Implement auth + write tests + update docs"

**Implementation:**
1. Bridge detects parallelizable work (regex or LLM)
2. Spawn N `BackgroundTask` instances
3. Each gets isolated session
4. Results aggregate back to user

**Challenges:**
- Agents might conflict (edit same file)
- Need conflict resolution
- More complex orchestration
- Higher cost (N agents running)

**Effort:** 1-2 days
**Impact:** Medium (speed for large parallelizable tasks)
**Risk:** High (conflicts, coordination complexity)

**Decision:** Defer until we see clear need

---

## Recommended Implementation Priority

| # | Feature | Impact | Effort | Risk | Recommend |
|---|---------|--------|--------|------|-----------|
| 1 | Prompt to prefer Edit over Write | High | 30min | Low | ✅ Do first |
| 5 | Intelligent context pre-loading | High | 4-6h | Low | ✅ Do second |
| 2 | Per-project `.valorrules` | Medium | 2-3h | Low | ✅ Do third |
| 4 | Model selection (Opus/Sonnet/Haiku) | Medium | 2-3h | Low | ✅ Quick win |
| 3 | Semantic codebase search | High | 1-2d | Medium | ⏸️ If needed |
| 6 | Multi-agent P-threads | Medium | 1-2d | High | ⏸️ Defer |

**Quick wins (< 1 day total):** Features 1, 2, 4
**High-value but longer:** Feature 5 (context pre-loading)
**Infrastructure projects:** Features 3, 6 (defer until clear need)

---

## What We CANNOT Adapt from Cursor

These Cursor features don't map to Valor's autonomous model:

1. **Real-time @file attachment** - No human typing during execution
2. **Interactive plan review** - Agent works autonomously, can't pause for approval
3. **Inline suggestions** - No IDE interface
4. **Tab completion** - Not applicable
5. **Visual diff review** - User sees results in git, not live

**Our strength:** Autonomous long-running sessions (2+ hours) - Cursor can't do this

---

## Next Steps

1. ✅ **Feature 1** - Add "prefer Edit" to SOUL.md (30 min)
2. ✅ **Feature 2** - Implement `.valorrules` parsing (2-3 hours)
3. ✅ **Feature 4** - Add model selection routing (2-3 hours)
4. ⏸️ **Feature 5** - Intelligent context pre-loading (4-6 hours) - await approval
5. ⏸️ **Feature 3** - Semantic search (1-2 days) - await clear need
6. ⏸️ **Feature 6** - P-threads (1-2 days) - defer

**Goal:** Make Valor's autonomous discovery smarter, faster, and cheaper - not try to replicate Cursor's interactive model.
