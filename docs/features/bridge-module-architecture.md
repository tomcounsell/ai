# Bridge Module Architecture

The Telegram bridge (`bridge/telegram_bridge.py`) is organized into focused sub-modules:

| Module | Responsibility |
|--------|---------------|
| `bridge/media.py` | Media detection, download, transcription, image description |
| `bridge/routing.py` | Message routing, project config, mention/response classification |
| `bridge/context.py` | Context building, conversation history, reply chains |
| `bridge/response.py` | Message formatting, reactions, file extraction, sending |
| `bridge/agents.py` | Agent invocation, retry logic, self-healing |

## telegram_bridge.py

The main module (`bridge/telegram_bridge.py`) serves as the entry point and coordinator:
- Initializes the Telegram client and event handlers
- Loads configuration and propagates it to sub-modules
- Contains the `handler()` event callback and `main()` startup function
- Maintains backward-compatible imports so existing code continues to work

## Import Guidelines

New code should import directly from sub-modules:

```python
# Preferred
from bridge.media import get_media_type
from bridge.routing import find_project_for_chat
from bridge.context import build_context_prefix

# Still works (backward compat) but not preferred
from bridge.telegram_bridge import get_media_type
```

## Configuration Propagation

Sub-modules that depend on runtime configuration (loaded from `config/projects.json` and `.env`) receive it via module-level attribute assignment in `telegram_bridge.py` at startup. This avoids circular imports while ensuring sub-module functions have access to config, project mappings, and active project lists.
