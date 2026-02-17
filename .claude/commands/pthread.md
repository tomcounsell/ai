---
description: "Scale compute by spawning multiple agents in parallel for independent tasks. Use when facing multiple independent tasks that can run concurrently."
---

# Parallel Thread Execution (P-Thread)

Scale compute by spawning multiple agents in parallel. This skill enables autonomous horizontal scaling for independent work.

## Core Principle

When facing independent tasks, don't execute sequentially. Spawn parallel sub-agents and aggregate results. The system should make this decision autonomously based on task structure.

## When to Auto-Parallelize

The system should automatically parallelize when:

1. **Multiple independent searches** - Finding different types of files/patterns
2. **Multi-file analysis** - Reviewing separate modules that don't interact
3. **Exploration tasks** - Investigating multiple hypotheses simultaneously
4. **Fusion patterns** - Getting multiple perspectives on the same problem

## Implementation Pattern

### Using Claude Code Task Tool

```python
# Spawn parallel sub-agents for independent work
# Each agent gets focused context and clear success criteria

# Example: Parallel codebase exploration
tasks = [
    {"agent": "Explore", "prompt": "Find all authentication handlers"},
    {"agent": "Explore", "prompt": "Find all API endpoint definitions"},
    {"agent": "Explore", "prompt": "Find all database models"},
]

# Launch all simultaneously - don't wait for sequential completion
for task in tasks:
    spawn_subagent(task, run_in_background=True)

# Aggregate results when all complete
```

### Decision Logic

```
IF task has independent subtasks:
    - Identify subtask boundaries
    - Spawn sub-agent per subtask (using Task tool)
    - Set run_in_background=True for true parallelism
    - Aggregate results

IF exploring multiple approaches:
    - Spawn N agents with same goal, different strategies
    - Compare outputs
    - Select best or synthesize

IF task is inherently sequential:
    - Do NOT parallelize
    - Execute as single thread
```

## Auto-Parallelization Triggers

The system should recognize these patterns and auto-parallelize:

| User Request Pattern | Action |
|---------------------|--------|
| "Review X, Y, and Z" | Parallel review agents |
| "Find all... and also find..." | Parallel search agents |
| "Explore different approaches to..." | Fusion thread (same goal, multiple agents) |
| "Check A module and B module" | Parallel analysis if modules are independent |

## Aggregation Strategies

After parallel completion:

1. **Merge**: Combine all findings into unified result
2. **Best-of-N**: Select highest quality output
3. **Synthesize**: Final pass to create coherent summary
4. **Deduplicate**: Remove redundant findings across agents

## Metrics to Track

```python
thread_metrics = {
    "parallel_agents_spawned": N,
    "total_tool_calls": sum(agent.tool_calls for agent in agents),
    "wall_clock_time": actual_duration,
    "sequential_equivalent_time": sum(agent.duration for agent in agents),
    "speedup_factor": sequential_time / wall_clock_time
}
```

## Integration with Ralph Wiggum Pattern

P-Threads should not stop until aggregation is complete:

```
1. Spawn all parallel agents
2. Wait for ALL to complete (no partial results)
3. Aggregate findings
4. If aggregation reveals gaps → spawn additional agents
5. Only complete when unified result meets quality bar
```

## Example: Autonomous Parallel Review

```markdown
Input: "Review the authentication system"

System Decision: This spans multiple files/concerns → parallelize

Spawned Agents:
- Agent 1: Security patterns (auth handlers, token validation)
- Agent 2: Performance (query patterns, caching)
- Agent 3: Code quality (style, tests, docs)
- Agent 4: Architecture (dependencies, interfaces)

Aggregation: Synthesize into unified review with sections

Output: Complete review document, not partial results
```

## Key Insight

*"Every time a new powerful model is released, you don't just get the benefits one time. You get it N times if and only if you're delegating to multiple agents."*

The system should be making these parallelization decisions autonomously. Valor (the persona) doesn't need to think about threading - the underlying system handles it.

---

**Thread Type**: P-Thread (Parallel)
**Trigger**: Independent subtasks detected
**Behavior**: Autonomous sub-agent spawning
**Completion**: Only when all threads complete and results are aggregated
