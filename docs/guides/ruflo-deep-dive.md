# Ruflo Deep Dive: Transferable Patterns for Our Orchestration

> Research completed 2026-03-24 for [#493](https://github.com/tomcounsell/ai/issues/493)

## Executive Summary

[ruflo](https://github.com/ruvnet/ruflo) is a multi-agent orchestration framework built on Claude Code with 5,875 commits and 24k stars. An investigation into its actual implementation revealed significant gaps between marketing claims and reality. However, the broader landscape research surfaced genuinely useful patterns from other frameworks (Citadel, Mem0, RouteLLM, ComposioHQ) that informed three new issues and enriched five existing ones.

**Bottom line:** ruflo's core value is a well-structured metadata/state layer for multi-agent coordination via MCP. The concepts worth adopting come from the broader ecosystem, not from ruflo specifically.

---

## Ruflo: Claims vs. Reality

| Claim | Reality | Verdict |
|-------|---------|---------|
| 60+ specialized agent types | 89 `SKILL.md` prompt templates; `agent spawn` supports 15 types with hardcoded defaults | Marketing overstatement |
| Self-learning neural architecture (SONA) | Trajectory storage + keyword routing + RL weight adjustment. Real TypeScript (835 lines), but tunes heuristics, not trains models | Genuine but modest |
| WASM-based classification | `@ruvector/rvagent-wasm` published 6 days before review (v0.1.0, 620KB). Handles trivial AST transforms. Model router uses keyword matching | Nascent/aspirational |
| 75% cost savings from tiered routing | Keyword matching: "architect"→opus, "typo"→haiku. No benchmarks provided | Unverified |
| Enterprise orchestration engine | MCP tool wrapper for Claude Code. Agents are JSON records in `store.json`. Actual execution is Claude Code's Task tool | Accurate but misleading framing |

### What's Genuinely Good

- **Task orchestrator** (`v3/@claude-flow/swarm/src/coordination/task-orchestrator.ts`): Well-structured dependency graph resolution, priority queues, status tracking
- **RL algorithms** (9 files): Legitimate Q-Learning, SARSA, A2C, PPO, DQN implementations with proper data structures
- **Domain-driven design**: TypeScript codebase is well-organized with clear separation of concerns
- **Graceful degradation**: All advanced features (`@ruvector/*`) are `optionalDependencies` — system works without them

### Architecture

ruflo is an MCP server providing tools to Claude Code. It manages state and metadata (what agents exist, what tasks are assigned, their status), but actual "agent execution" is Claude Code calling these MCP tools and using its own Task tool to spawn sub-agents. ruflo is essentially a state-tracking and routing layer wrapping Claude Code's native capabilities.

---

## Landscape: What Other Frameworks Do Better

### Frameworks Surveyed

| Framework | Stars | Key Innovation |
|-----------|-------|----------------|
| [Citadel](https://github.com/SethGammon/Citadel) | 236 | 4-tier routing by orchestration overhead; circuit breaker (3 failures → force approach change); wave-based discovery relay |
| [Genie](https://github.com/automagik-dev/genie) | 261 | 10-Critic Council for pre-implementation review; severity gates block shipping |
| [Superset](https://github.com/superset-sh/superset) | 7.8k | Provider-agnostic orchestration; 10+ parallel agents with automatic worktree isolation |
| [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) | 11k | Teams-first orchestration; "deep interview" Socratic requirements clarification |
| [ComposioHQ](https://github.com/ComposioHQ/agent-orchestrator) | — | CI failures + review comments auto-routed to agents; 8 swappable plugin slots |
| [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) | Built-in | File-lock task claiming; mailbox messaging; no session resumption (limitation) |
| [Claude Code Swarm](https://github.com/rekpero/claude-code-swarm) | — | 24/7 daemon watches GitHub issues; CI review loop |

### Key Research: Memory Systems

[Mem0](https://mem0.ai/) ([paper](https://arxiv.org/abs/2504.19413)) provides the most rigorous benchmarks for agent memory:
- 26% accuracy improvement over naive approaches
- 91% lower p95 latency
- 90% token savings
- Graph-based memory variant captures relational structures (+2% over base)
- Hierarchical: short-term episodic buffers → medium-term stores → long-term semantic knowledge
- Importance scoring + dynamic forgetting to manage storage costs

**Relevance:** Validates our Popoto Agent Memory approach (#394). Mem0's architecture maps directly to Popoto primitives (DecayingSortedField, WriteFilter, CoOccurrenceField, CompositeScoreQuery).

### Key Research: Model Routing

[RouteLLM](https://github.com/lm-sys/RouteLLM) (UC Berkeley/Anyscale) provides benchmarked cost reduction:
- 85% cost reduction on MT Bench, 45% on MMLU, 35% on GSM8K vs. GPT-4-only
- Four trained routers: similarity-weighted ranking, matrix factorization, BERT classifier, causal LLM
- Generalizes to new model pairs without retraining
- 3.66x cost savings at CPT 50%, 2.49x at CPT 80%

**Relevance:** At our current scale (~50-100 sessions/day), the infrastructure cost of trained routers likely exceeds savings. Worth revisiting at 500+ sessions/day. See [#502](#model-routing-future).

### Key Research: Self-Improving Prompts

[DSPy](https://dspy.ai/) (Stanford) has concrete benchmarks:
- ReAct scores: 24% → 51% on gpt-4o-mini
- Classification accuracy: 46.2% → 64.0%
- F1 scores doubled with few lines of code

OpenAI's [GEPA](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining) (Genetic-Pareto) implements evolutionary prompt optimization with `VersionedPrompt` tracking for rollback safety.

**Relevance:** Future direction for our `PolicyCache` primitive (#394). Once behavioral episode memory (#393) captures success patterns, DSPy-style optimization could tune crystallized patterns automatically.

---

## Transferable Patterns: What We Adopted

### Pattern 1: Q&A Mode (from Citadel's tiered routing)

**Issue:** [#499 — ChatSession Q&A mode](https://github.com/tomcounsell/ai/issues/499)

Citadel routes by orchestration overhead, not model tier. We adapted this narrowly: ChatSession answers informational queries directly without spawning DevSession. All real work still goes through full SDLC — no shortcuts.

**What we took:** The concept that not every message needs the same orchestration weight.
**What we rejected:** Tiers 2-4 (simple fixes, parallel fleet). Risk of bypassing SDLC quality gates too high.

### Pattern 2: Cross-Agent Knowledge Relay (from Mem0 + Citadel's discovery relay)

**Issue:** [#500 — Cross-agent knowledge relay](https://github.com/tomcounsell/ai/issues/500)

Sub-agent findings currently evaporate after each block of work. Mem0's consolidation pattern (episodic → semantic with importance scoring) combined with Citadel's brief compression concept informed a memory-based approach: persist findings in Popoto, make them searchable by future sub-agents.

**What we took:** Persistent, searchable findings with natural decay.
**What we rejected:** Wave orchestration mechanics, new `/pthread` parameters. Builds on memory system (#394) instead.

### Pattern 3: Async Job Queue with Dependencies (from ComposioHQ + landscape)

**Issue:** [#501 — Async job queue with branch-session mapping](https://github.com/tomcounsell/ai/issues/501)

Multiple frameworks solve parallel execution via worktree isolation. We adapted this as a sequential queue with dependency tracking and automatic branch-session mapping — more valuable for our workflow than true parallelism.

**What we took:** Job dependency graph (`depends_on`), deterministic branch resolution per slug+stage, session pause/resume with branch state.
**What we rejected:** Parallel DevSessions on same machine. One at a time is fine; PM reorders the queue.

---

## Enriched Existing Issues

| Issue | What Was Added |
|-------|---------------|
| [#394](https://github.com/tomcounsell/ai/issues/394) (Popoto Agent Memory) | Mem0 benchmarks validating approach; DSPy/GEPA as future PolicyCache directions; ruflo SONA reality check |
| [#393](https://github.com/tomcounsell/ai/issues/393) (Behavioral Episode Memory) | Success pattern signals to mine; Mem0's episodic→semantic consolidation pattern |
| [#189](https://github.com/tomcounsell/ai/issues/189) (More Souls) | Reframe from browsing to structured multi-persona compositions (Genie's 10-Critic Council) |
| [#26](https://github.com/tomcounsell/ai/issues/26) (Multi-agent group conversation) | Claude Code Agent Teams, Citadel discovery relay, wave-based as dominant coordination pattern |
| [#104](https://github.com/tomcounsell/ai/issues/104) (Iterative review loops) | ComposioHQ CI feedback routing; Citadel circuit breaker (3 failures → force approach change) |

---

## Future Considerations

### Model-Tier Routing {#model-routing-future}

RouteLLM proves 35-85% cost reduction with trained routers. At our current scale (~50-100 sessions/day), the infrastructure overhead likely exceeds savings. Revisit when:
- Session volume exceeds 500/day
- API costs become a significant line item
- Simple heuristic routing (stage-based model selection) has been tried and found insufficient

### Self-Improving Prompts

DSPy and GEPA provide concrete frameworks for automated prompt optimization. Prerequisites:
- Behavioral Episode Memory (#393) shipping — need success/failure data
- PolicyCache primitive (#394) — need storage for optimized prompts
- Evaluation datasets — need measurable quality criteria per task type

### Claude Code Agent Teams

Anthropic's built-in multi-agent feature is experimental but worth monitoring. Current limitations (no session resumption, no nested teams) make it less robust than our session management. As it matures, we may want to adopt its file-lock task claiming and mailbox messaging patterns.

---

## Methodology

Three parallel research agents investigated:
1. **Ruflo repo deep dive** — Fetched actual source files, traced claim→implementation for each feature
2. **Landscape scan** — Surveyed 8+ frameworks, Mem0, RouteLLM, DSPy, OpenAI cookbooks
3. **Our gap analysis** — Read job_queue.py, sdk_client.py, session_router.py, reflections.py, worktree_manager.py, pipeline_state.py, steer_child.py

All findings cross-referenced against our codebase before issuing recommendations.
