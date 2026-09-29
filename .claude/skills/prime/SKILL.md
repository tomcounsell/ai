---
name: prime
description: "Use when onboarding to the Valor AI codebase, understanding the system architecture, or when the user asks 'how does this work'. Comprehensive codebase orientation guide."
disable-model-invocation: true
---

# Prime - Codebase Onboarding

Get oriented in the Valor AI system well enough to add features effectively. CLAUDE.md is already loaded and covers principles, commands, and architecture; this adds the map and the reading order.

Valor is an AI coworker that runs on its own Mac: the supervisor assigns work and Valor executes autonomously.

## Map

```
Telegram → Bridge (Telethon, I/O only) → Redis AgentSession queue
Worker (python -m worker) → headless session runner (agent/session_runner/, harness in agent/sdk_client.py), one `claude -p` per turn
Reflection scheduler (python -m reflections) → own launchd subprocess; enqueues recurring work the worker runs
```

| Path | What lives there |
|------|------------------|
| `bridge/telegram_bridge.py` | Telegram user account; enqueues sessions, routes output (nudge loop); no SDLC awareness |
| `worker/`, `agent/` | Session execution, queue, SDK client, output routing |
| `reflections/`, `config/reflections.yaml` (vault-synced, not committed) | Out-of-process recurring work |
| `tools/` | Python tools behind the `valor-*` CLIs |
| `config/identity.json`, `config/personas/segments/` | Structured identity and composable persona segments |
| `mcp_servers/` | MCP servers; `/update` registers memory and BYOB in `~/.claude.json`. GitHub goes through `gh` |
| `docs/features/README.md` | Feature index: how things actually work |

## Read, in order

1. `config/personas/segments/`: Valor's identity, work patterns, and tools
2. `docs/features/README.md`, then the feature docs your task touches

## Facts worth knowing up front

- A new Python tool is invisible to the agent until wired into a `[project.scripts]` entry in `pyproject.toml` or imported by the bridge directly.
- `curl -s localhost:8500/dashboard.json` returns the full system state as JSON; `./scripts/valor-service.sh status` and `tail -20 logs/bridge.error.log` give a quick health read.
