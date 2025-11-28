# GEMINI.md - AI System

## Project Overview

This is a comprehensive AI agent and tool management system built with Python. The system is designed to be a "clean rebuild" with a focus on modern practices, modularity, and high-quality code.

The core of the system is a FastAPI server that provides a RESTful API and WebSocket support for real-time communication. It includes a sophisticated agent (`ValorAgent`), context management, and a tool registry for extending the agent's capabilities.

The primary user interface is a Telegram bot, which connects to the AI system to provide a conversational experience. The bot is built using the Telethon library and can be configured to interact in various ways (DMs, groups, mentions).

### Key Technologies

*   **Backend:** Python 3.9+, FastAPI, Uvicorn
*   **Telegram Bot:** Telethon
*   **Data:** Pydantic, aiosqlite
*   **Tooling:** `black`, `isort`, `mypy`, `ruff`, `pytest` for code quality and testing.
*   **Dependency Management:** `pip` with `requirements/base.txt` and `pyproject.toml`.

### Architecture

*   **`server.py`**: The main FastAPI application. It initializes all the core components, including the agent, database, and tool orchestrator. It exposes endpoints for chat, health checks, and system management.
*   **`telegram_bot.py`**: A standalone Telethon client that connects to the Telegram API. It handles incoming messages, determines if a response is needed, and interacts with the AI system's components.
*   **`agents/`**: Contains the core AI agent logic. `ValorAgent` is the main agent implementation.
*   **`config/`**: Manages application settings and configuration loading.
*   **`tools/`**: A registry of tools that the agent can use to perform actions.
*   **`utilities/`**: Shared utilities for database management, logging, and exception handling.
*   **`mcp_servers/`**: Seems to be related to a "Multi-Agent Communication Protocol" for orchestrating multiple agents or tools.
*   **`scripts/`**: Contains shell scripts for managing the application lifecycle (start, stop, logs, etc.).

## Building and Running

### Quick Start

The easiest way to run the system is to use the provided shell scripts.

**Run the Telegram Bot:**

```bash
# This script will handle authentication, start the bot, and tail the logs.
./scripts/telegram_run.sh
```

**Run the Production Server:**

```bash
./scripts/start.sh
```

**Run in Demo Mode (no API keys needed):**

```bash
./scripts/start.sh --demo
```

### Other Commands

*   **View logs:** `./scripts/logs.sh`
*   **Stop all services:** `./scripts/stop.sh`

## Development Conventions

This project has a strong emphasis on code quality and adheres to modern Python development practices.

*   **Code Style:** Code is formatted with `black` and `isort`.
*   **Linting:** `ruff` and `flake8` are used for linting.
*   **Type Checking:** `mypy` is used for static type checking in strict mode.
*   **Testing:** `pytest` is the testing framework. Tests are located in the `tests/` directory.

All of these tools are configured in the `pyproject.toml` file. It's recommended to use a pre-commit hook to automatically run these checks before committing code.
