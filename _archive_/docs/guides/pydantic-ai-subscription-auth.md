# PydanticAI on Claude Max Subscription (Server-Side)

Using PydanticAI on a server without burning API credits, by routing requests
through a local proxy that swaps API key auth for your Claude Max OAuth session.

## How It Works

The Claude Code CLI authenticates via OAuth (`claude login`) and holds a Max
subscription session locally. **CLIProxyAPI** runs a local HTTP server that
accepts standard Anthropic API requests (with any dummy key) and re-signs them
as OAuth bearer token requests before forwarding to Anthropic. PydanticAI just
needs its `base_url` pointed at the proxy.

```
PydanticAI → CLIProxyAPI (localhost) → Anthropic API (OAuth/Max)
```

## Prerequisites

- Claude Code CLI installed and authenticated (`claude login` completed)
- Node.js available on the server (for CLIProxyAPI)
- PydanticAI installed (`pip install pydantic-ai`)

## Setup

### 1. Install CLIProxyAPI on the server

```bash
git clone https://github.com/luispater/CLIProxyAPI
cd CLIProxyAPI
npm install
npm run build
```

### 2. Start the proxy

```bash
node dist/index.js
# Listens on http://localhost:3456 by default
```

To run it persistently (e.g. via a systemd service or launchd plist), point the
service at `node /path/to/CLIProxyAPI/dist/index.js`. The proxy inherits the
Claude OAuth session from the user who ran `claude login`.

### 3. Sync your OAuth session to the server

The OAuth session lives in `~/.claude/` on your local machine. Your existing
sync process should include:

```
~/.claude/.credentials.json   # OAuth tokens
~/.claude/settings.json        # Optional but harmless to include
```

After syncing, verify the session is valid on the server:

```bash
claude --version   # should not prompt for login
```

If it does prompt, run `claude login` on the server directly.

### 4. Configure PydanticAI with subscription-first, API key fallback

```python
import logging
import os
import time

from anthropic import AsyncAnthropic, AuthenticationError
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

logger = logging.getLogger(__name__)

PROXY_URL = "http://localhost:3456"
MODEL = "claude-sonnet-4-6"
FALLBACK_CACHE_SECONDS = 4 * 60 * 60  # 4 hours before retrying subscription

_fallback_until: float = 0.0  # epoch time; 0 means "use subscription"


def _subscription_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key="subscription", base_url=PROXY_URL)


def _api_key_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _active_client() -> tuple[AsyncAnthropic, bool]:
    """Return (client, is_subscription). Uses API key if within fallback window."""
    if time.monotonic() < _fallback_until:
        return _api_key_client(), False
    return _subscription_client(), True


async def run(prompt: str, system_prompt: str = "") -> str:
    """Run a prompt, preferring subscription auth with API key fallback.

    On a 401, switches to API key billing and caches that decision for
    FALLBACK_CACHE_SECONDS before attempting the subscription proxy again.
    """
    global _fallback_until

    client, is_subscription = _active_client()
    agent = Agent(AnthropicModel(MODEL, anthropic_client=client), system_prompt=system_prompt)

    try:
        result = await agent.run(prompt)
        return result.data
    except AuthenticationError:
        if not is_subscription:
            raise  # API key itself is broken — don't swallow that

        logger.warning(
            "Subscription auth failed (401) — falling back to API key for %dh",
            FALLBACK_CACHE_SECONDS // 3600,
        )
        _fallback_until = time.monotonic() + FALLBACK_CACHE_SECONDS

        fallback_agent = Agent(
            AnthropicModel(MODEL, anthropic_client=_api_key_client()),
            system_prompt=system_prompt,
        )
        result = await fallback_agent.run(prompt)
        return result.data
```

The `_fallback_until` timestamp is module-level state — once a 401 is seen,
every call in the same process uses the API key until the window expires, at
which point the next call probes the subscription again automatically. If the
API key itself returns a 401, that error is re-raised rather than silently
looping.

## Caveats

- **Unofficial / unsupported** — CLIProxyAPI is a third-party tool. If Anthropic
  changes the OAuth token format or the CLI's auth flow, the proxy may break.
- **Single-user session** — the proxy uses one OAuth session. Concurrent heavy
  usage still counts against one Max plan's rate limits.
- **Local only** — the proxy must run on the same host as your app (or be
  reachable on an internal network). Do not expose it publicly.
- **Session expiry** — OAuth tokens expire. If calls start failing with 401s,
  re-run `claude login` and re-sync `~/.claude/`.

## Verifying It Works

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from anthropic import AsyncAnthropic

async def main():
    client = AsyncAnthropic(api_key="subscription", base_url="http://localhost:3456")
    model = AnthropicModel("claude-haiku-4-5-20251001", anthropic_client=client)
    agent = Agent(model=model, system_prompt="Reply with one word.")
    result = await agent.run("Say hello.")
    print(result.data)  # Should print something like "Hello"

asyncio.run(main())
```

## Monitoring

Check that the proxy is running before your app starts:

```python
import httpx

def check_proxy():
    try:
        httpx.get("http://localhost:3456/health", timeout=2)
        return True
    except Exception:
        return False
```

Or add a health check to your service startup script:

```bash
curl -sf http://localhost:3456/health || { echo "CLIProxyAPI not running"; exit 1; }
```
