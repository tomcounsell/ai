# Cursor IDE Lessons - What We Can Learn

## Research Summary

Based on analysis of Cursor's 2026 architecture and user workflows, here are key insights we can apply to Valor.

**Sources:**
- [How Cursor AI IDE Works](https://blog.sshh.io/p/how-cursor-ai-ide-works)
- [A Year with Cursor: Workflow Evolution](https://subramanya.ai/2026/01/04/a-year-with-cursor-how-my-workflow-evolved-from-agent-to-architect/)
- [Cursor 2.0 Has Arrived](https://medium.com/ai-software-engineer/cursor-2-0-has-arrived-and-agentic-ai-coding-just-got-wild-65bbbd3be4ec)

---

## What Cursor Does Well

### 1. **Tool Calling Pattern**
```
read_file(path)
write_file(path, content)
run_command(command)
codebase_search / grep_search / file_search
reapply (self-correction with more expensive model)
```

**Our status:** ✅ We have similar via Claude Agent SDK tools

### 2. **Context Management - Explicit References**
- `@file`, `@folder`, `@codebase`, `@web`, `@docs` syntax
- User explicitly attaches relevant code sections
- Wrapped in `<attached-files>` blocks

**Our status:** ⚠️ **We rely on agent to find context** - could improve with explicit attachment

**Opportunity:** Add syntax like `@file:path/to/file.py` in Telegram messages that get expanded into context

### 3. **Semantic Search with Re-ranking**
- Codebase indexed into vectorstore using encoder LLMs
- At query time, secondary LLM re-ranks results for relevance
- Main agent gets "perfect" results

**Our status:** ⚠️ **No semantic search yet** - agent uses grep/glob

**Opportunity:** Build codebase indexing per project (could use pgvector or similar)

### 4. **Semantic Diff + Apply Model**
- Main agent produces **partial changes** with insertion guidance (not full files)
- Cheaper "apply model" generates actual file contents + fixes syntax
- Results pass through linters with feedback for self-correction

**Our status:** ❌ **Agent generates full files** - wasteful for large files

**Opportunity:** Implement diff-based editing pattern

### 5. **Static System Prompt for Caching**
- System prompt stays static (no personalization)
- Maximizes Anthropic's prompt caching for cost/latency reduction
- User context added via separate blocks

**Our status:** ✅ We use static SOUL.md + project context appended

### 6. **Rules System**
- `.cursorrules` files at project/user/team level
- Agent reads these before every task
- Ensures consistency across codebase

**Our status:** ✅ We have project-specific context in `projects.json`

**Opportunity:** Add per-project `.valorrules` files for coding standards

### 7. **Plan Mode - Separate Architecture from Execution**
- Use powerful model (Opus) for planning
- Use faster model for implementation
- Creates reviewable blueprint before coding

**Our status:** ⚠️ **We have `/sdlc` and plan mode, but not separate model selection**

**Opportunity:** Use Opus for planning, Sonnet for execution (cost optimization)

### 8. **Multi-Agent Parallel Execution**
- Run up to 8 agents in parallel
- Each in isolated environment
- Can work on different parts simultaneously

**Our status:** ❌ **Single agent per session**

**Opportunity:** P-Thread pattern (already documented in SOUL) - spawn multiple agents

### 9. **Custom Commands/Workflows**
- Users build reusable commands: `/plan`, `/refactor`, `/test`, `/review`
- Reduces repetition

**Our status:** ✅ We have Claude Code skills (`/prime`, `/pthread`, `/sdlc`)

---

## Key Architectural Patterns We Should Adopt

### Priority 1: Diff-Based Editing (High Impact, Medium Effort)

**Problem:** Agent generates full files even for small changes

**Solution:**
```python
# Current: agent.write_file(path, entire_content)

# Better: agent.edit_file(path, old_text, new_text)
# Or:     agent.apply_diff(path, diff_patch)
```

**Benefits:**
- Faster execution (less tokens)
- Cheaper (less generation cost)
- Clearer what changed
- Better for large files

**Implementation:**
1. Add `apply_diff` tool to SDK client
2. Modify system prompt to prefer diffs over full rewrites
3. Use Claude's native Edit tool from Claude Code

**Estimate:** 2-4 hours

---

### Priority 2: Explicit Context Attachment (High Impact, Low Effort)

**Problem:** Agent must search for relevant files, wastes time/tokens

**Solution:**
```
User in Telegram:
"Fix the authentication bug @file:bridge/telegram_bridge.py @file:config/SOUL.md"

Bridge expands:
<attached-files>
<file path="bridge/telegram_bridge.py">
[contents]
</file>
<file path="config/SOUL.md">
[contents]
</file>
</attached-files>

Please fix the authentication bug.
```

**Benefits:**
- Agent starts with exact context
- No wasted search operations
- User controls what's relevant

**Implementation:**
1. Detect `@file:path`, `@folder:path` in messages
2. Read contents and wrap in `<attached-files>` block
3. Pass to agent

**Estimate:** 2-3 hours

---

### Priority 3: Semantic Codebase Search (High Impact, High Effort)

**Problem:** Agent uses grep (regex) - misses semantic matches

**Solution:**
- Index codebase using embeddings (OpenAI ada-002 or Voyage)
- Store in pgvector or similar
- At query time, semantic search + LLM re-rank
- Return top results to agent

**Benefits:**
- Agent finds relevant code faster
- Understands concepts, not just keywords

**Challenges:**
- Requires vector DB setup
- Must re-index on code changes
- More infrastructure

**Implementation:**
1. Add pgvector to dependencies
2. Build indexing job (runs after git commits)
3. Add `semantic_search` tool to agent
4. Update system prompt to prefer semantic search

**Estimate:** 1-2 days

---

### Priority 4: Plan Mode with Model Selection (Medium Impact, Low Effort)

**Problem:** Use Sonnet for everything (expensive planning, slow execution)

**Solution:**
```python
# Planning phase: Use Opus (best reasoning)
plan = await agent.query(message, model="opus")

# Execution phase: Use Sonnet (fast, capable)
result = await agent.execute_plan(plan, model="sonnet")
```

**Benefits:**
- Better plans (Opus thinks deeper)
- Faster execution (Sonnet is quicker)
- Cost optimization

**Implementation:**
1. Add `model` parameter to `ValorAgent.query()`
2. Detect "plan" vs "execute" mode
3. Route to appropriate model

**Estimate:** 2-3 hours

---

### Priority 5: Multi-Agent Parallel Execution (High Impact, High Effort)

**Problem:** Single agent blocks - can't parallelize work

**Solution:**
```python
# P-Thread pattern (already documented in SOUL)
agents = [
    BackgroundTask(agent1.query("Implement feature A")),
    BackgroundTask(agent2.query("Write tests for B")),
    BackgroundTask(agent3.query("Update docs for C")),
]

await asyncio.gather(*[a.run() for a in agents])
```

**Benefits:**
- Work completes faster
- Better resource utilization
- Natural for independent tasks

**Challenges:**
- Agents might conflict (same file)
- Coordination needed
- More complex orchestration

**Implementation:**
1. Spawn multiple SDK client instances
2. Assign different working dirs or lock files
3. Aggregate results
4. Resolve conflicts if any

**Estimate:** 1 day

---

## Lessons from Cursor User Workflow

### Shift from "Agent" to "Architect"

**Current Valor:** User sends request → Valor does everything

**Better Valor:** User provides architecture → Valor executes

**How:**
1. Encourage users to use `/plan` first
2. Review plan before execution
3. Separate "what" from "how"

### Encode Principles Locally

**Current:** SOUL.md is universal

**Better:** Per-project `.valorrules`

**Example `.valorrules`:**
```yaml
style:
  - "Always use type hints"
  - "Prefer dataclasses over dicts"
  - "Write docstrings for public functions"

testing:
  - "Write pytest tests for all new functions"
  - "Use fixtures for common test data"

git:
  - "Commit messages: <type>: <description>"
  - "Push after every completed feature"
```

### Custom Workflows

Users build shortcuts like `/fix-bug`, `/add-feature`, `/refactor`

We already have skills - could expand with user-customizable skills

---

## Recommended Implementation Order

| Priority | Feature | Impact | Effort | Est. Time |
|----------|---------|--------|--------|-----------|
| 1 | Diff-based editing | High | Medium | 2-4 hours |
| 2 | Explicit context (`@file`) | High | Low | 2-3 hours |
| 3 | Per-project `.valorrules` | Medium | Low | 2-3 hours |
| 4 | Plan mode with model selection | Medium | Low | 2-3 hours |
| 5 | Semantic codebase search | High | High | 1-2 days |
| 6 | Multi-agent parallel execution | High | High | 1 day |

**Quick wins (< 1 day):** Items 1-4
**High-impact but longer:** Items 5-6

---

## Next Steps

1. **Validate with user** - Which features would be most valuable?
2. **Start with diff-based editing** - Biggest immediate impact
3. **Add explicit context attachment** - Simple but powerful
4. **Build semantic search** - If codebase complexity justifies it

The goal: Move Valor from "AI assistant" to "AI coworker with architect-level understanding"
