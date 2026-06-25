# Granite PTY: OAuth Token Prevention

## Problem

Granite PTY sessions drive two interactive `claude` TUI processes (PM and Dev). The TUIs
authenticate via the Claude Max subscription OAuth path. Short-lived session tokens expire
after roughly an hour, causing the TUI to render a `/login` prompt mid-session. The PTY
container has no mechanism to dismiss an interactive login screen — the session hangs until
the operator kills it, producing a `startup_unresolved` or `pm_hang` exit.

The `CLAUDE_CODE_OAUTH_TOKEN` env var is the prevention credential: when present, the `claude`
TUI uses this long-lived token and never hits the short-lived session expiry during a run.

## The Token

`CLAUDE_CODE_OAUTH_TOKEN` is a long-lived (~1-year) OAuth token minted via:

```bash
claude setup-token
```

The token has the prefix `sk-ant-oat01-`. Its presence and prefix validity are checked by
`python -m tools.doctor`:

```
[GRANITE] CLAUDE_CODE_OAUTH_TOKEN  ok  (prefix sk-ant-oat01-)
```

If the var is absent or has an unexpected prefix, the health check reports a warning — the PTY
container will still run but short-lived token expiry will cause `/login` prompts.

## Minting and Rotation

- The token is minted interactively in a browser-accessible machine via `claude setup-token`.
- The command opens a browser window; the resulting token must be copied manually to the vault.
- Rotation cadence: approximately once per year (Anthropic token lifetime).
- A single token is shared across all machines via iCloud vault propagation (see below).

## Where the Token Lives

The token is stored in the vault `.env` file:

```
~/Desktop/Valor/.env
```

Add or update the line:

```
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

The repo `.env` is a symlink to this vault file. All machines receive the token automatically
after iCloud syncs — no per-machine step required.

## How It Is Injected

`agent/granite_container/pty_driver.py::_build_env()` builds the child environment for every
PTY spawn. It inherits the full parent `os.environ`, blanks the `ANTHROPIC_*` cluster to force
the OAuth path, and then forwards `CLAUDE_CODE_OAUTH_TOKEN` explicitly:

```python
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
if token:
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
else:
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
```

If the var is absent, the key is removed entirely so the TUI falls back to its own credential
lookup. It is intentionally NOT blanked alongside the `ANTHROPIC_*` vars — blanking it would
prevent the fallback path.

## Why No Settings Field

`APISettings` in `config/settings.py` uses `env_nested_delimiter="__"` (pydantic
`BaseSettings`). Binding `CLAUDE_CODE_OAUTH_TOKEN` to a settings field would require the env
var to be named `API__CLAUDE_CODE_OAUTH_TOKEN`, which breaks the convention used by the `claude`
CLI itself. Reading from `os.environ` directly sidesteps this naming mismatch without any
config-layer changes.

## Relationship to ANTHROPIC_* Blanking

The `ANTHROPIC_*` blanking and the `CLAUDE_CODE_OAUTH_TOKEN` injection are complementary, not
conflicting:

| Mechanism | Purpose |
|-----------|---------|
| Blank `ANTHROPIC_API_KEY` | Prevent SDK-path model calls; force OAuth path |
| Blank `ANTHROPIC_BASE_URL` | Remove ollama base URL so TUI hits real Claude API |
| Blank `ANTHROPIC_AUTH_TOKEN` | Remove ollama auth token injected by ollama setup |
| Inject `CLAUDE_CODE_OAUTH_TOKEN` | Supply long-lived token so TUI never prompts for login |

See the `_build_env()` docstring in `agent/granite_container/pty_driver.py` for the original
diagnosis of the ollama-endpoint routing issue that required blanking all three vars.

## Graceful Degradation

If the token is absent or has expired:

1. The PTY TUI starts normally.
2. When the short-lived session token expires (typically within ~1 hour), the TUI renders a
   `/login` prompt.
3. The container's idle detection cannot dismiss the login screen; the session eventually exits
   as `pm_hang` or `dev_hang`.
4. Issue #1750 added a recovery path for granite sessions that hit `/login` mid-run via the
   pure-Python BYOB driver. That path fires as a fallback after the hang is detected.

The right fix is always to rotate the long-lived token. The #1750 recovery path is a backstop,
not a substitute.

## Rollback

Remove `CLAUDE_CODE_OAUTH_TOKEN` from `~/Desktop/Valor/.env`. After iCloud syncs to all
machines and the worker restarts, PTY sessions revert to the short-lived OAuth path with the
#1750 recovery fallback active.

## See Also

- `agent/granite_container/pty_driver.py::_build_env()` — injection logic and rationale
- `tools/doctor.py` — presence + prefix health check
- `docs/features/granite-pty-production.md` — full PTY container design
- Issue #1750 — `/login` re-auth recovery via pure-Python BYOB driver (fallback path)
