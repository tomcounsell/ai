# Valor: 3 Years of Evolution (2022-2026)

## The Journey

**748 commits. 28 pull requests. One AI coworker.**

### Phase 1: Neural Network Experiments (2022)

Started as a research playground for brain-inspired computing:

- **Numenta theories** - Hierarchical Temporal Memory experiments
- **Custom agent systems** ("Whiskey", "Excitron") - homegrown neural architectures
- **GPT-3 contributions** - early LLM integration experiments
- **BlackSheep web framework** - Python async API foundation
- **Popoto** - custom Redis ORM for data persistence

_Technologies that came and went: Numenta HTM, custom neural simulators, Beanie ODM_

### Phase 2: Telegram Bot Foundation (Early 2025)

Pivoted from research to practical AI assistant:

- **Telethon** - real user account integration (not a bot API)
- **PydanticAI** - first agentic framework attempt
- **Perplexity AI** - web search and link analysis
- **Notion integration** - knowledge base querying
- **FastAPI server** - HTTP endpoints for tooling

_Key innovation: "Valor Engels" persona established - AI coworker, not assistant_

### Phase 3: Claude Code Unification (May-June 2025)

Major architectural shift to leverage Claude Code's native capabilities:

- **Claude Code as orchestrator** - stopped building custom sub-agent systems
- **MCP servers** - Sentry, GitHub, Linear, Notion, Stripe, Render integrations
- **Intelligent routing** - replaced keyword triggers with LLM-based intent recognition
- **PR #3: CC/Valor Unification** - merged Claude Code and Valor into single system

_Abandoned: Custom PydanticAI orchestration, keyword-based routing_

### Phase 4: Module Builder & Subagents (Sep-Nov 2025)

Attempted autonomous module generation:

- **7 MCP server modules** auto-generated via "Module Builder"
- **Native Claude Code subagents** - 6 specialized subagents for MCP context isolation
- **Gemini CLI analysis** - explored multi-model routing
- **PR #8: Architecture Rebuild** - pivoted to Claude Code native patterns

_Abandoned: Custom subagent framework, Module Builder auto-generation_

### Phase 5: Skill System and Persona (Jan 2026)

- **Skills-based tool management** - modular capability system
- **PR #9: Complete migration** - removed old implementation entirely
- **SOUL.md persona** - formalized Valor's identity and operating principles
- **Ollama** - local LLM for intent classification (llama3.2, llava for vision)

_Technologies adopted: Ollama for lightweight tasks_

### Phase 6: Claude Agent SDK (Jan-Feb 2026)

Current architecture - native Claude Code capabilities:

- **Claude Agent SDK** - same tools as Claude Code CLI, running via API
- **Job queue system** - handles parallel Telegram conversations
- **Steering queue** - mid-execution course correction via reply threads
- **Google Calendar integration** - work time tracking
- **Response summarization** - Ollama-powered message compression

_Current stack: Claude Agent SDK, Telethon, Redis (via Popoto), Ollama, MCP servers_

---

## Technologies That Stayed

| Technology           | Purpose              | Since |
| -------------------- | -------------------- | ----- |
| **Python**           | Core language        | 2022  |
| **Redis**            | State & queues       | 2022  |
| **Popoto**           | Redis ORM            | 2022  |
| **Telethon**         | Telegram integration | 2024  |
| **Claude/Anthropic** | Primary LLM          | 2025  |
| **MCP Protocol**     | Tool integration     | 2025  |

## Technologies That Were Replaced

| Technology               | Replaced By                          | When |
| ------------------------ | ------------------------------------ | ---- |
| Numenta HTM              | LLM-based agents                     | 2023 |
| Custom neural nets       | GPT-3 completions                    | 2024 |
| GPT-3 completions        | Aider                                | 2024 |
| Aider                    | Claude API                           | 2025 |
| Beanie ODM               | Popoto Redis                         | 2025 |
| PydanticAI orchestration | Claude Code native                   | 2025 |
| Keyword routing          | LLM intent recognition               | 2025 |
| Custom subagents         | Claude Agent SDK with dynamic agents | 2026 |

---

## The Philosophy That Emerged

> "I am an AI coworker, not an assistant or a tool. I have my own machine, my own work to do, and my own judgment about how to do it."

The system evolved from "AI research playground" → "Telegram client" → "Claude Code extension" → **"Autonomous AI coworker"**.

Key architectural principle: **Don't build what Claude Code already does.** Valor provides tools, workflows, and skills. Claude Code orchestrates.

---

_Generated from 748 commits across 3 years of development_
