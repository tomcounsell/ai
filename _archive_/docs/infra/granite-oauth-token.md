# Session-Runner OAuth Token Prevention

## Problem

Every session role (PM, Dev, Teammate) runs as a headless `claude -p` process
authenticated via the Claude Max subscription OAuth path. Short-lived session
tokens expire after roughly an hour, which would otherwise force a `/login`
re-auth mid-session — a failure mode a headless subprocess cannot interact
with the way a human at a terminal could.

The `CLAUDE_CODE_OAUTH_TOKEN` env var is the prevention credential: when
present, `claude -p` uses this long-lived token and never hits the
short-lived session expiry during a run.

## The Token

`CLAUDE_CODE_OAUTH_TOKEN` is a long-lived (~1-year) OAuth token minted via:

```bash
claude setup-token
```

The token has the prefix `sk-ant-oat01-`. Its presence and prefix validity are checked by
`python -m tools.doctor`:

```
[SESSION_RUNNER] CLAUDE_CODE_OAUTH_TOKEN  ok  (prefix sk-ant-oat01-)
```

If the var is absent or has an unexpected prefix, the health check reports a warning.

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

The [headless session runner](../features/headless-session-runner.md)
(`agent/session_runner/`) builds the child environment for every turn's
`claude -p` subprocess explicitly — not from ambient worker env. It inherits
the full parent `os.environ`, strips `ANTHROPIC_API_KEY` to force the OAuth
path, and forwards `CLAUDE_CODE_OAUTH_TOKEN` explicitly:

```python
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
if token:
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
else:
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
```

If the var is absent, the key is removed entirely so the CLI falls back to its
own credential lookup. It is intentionally NOT blanked alongside
`ANTHROPIC_API_KEY` — blanking it would prevent the fallback path.
`--bare` is never passed on this path — it does not read
`CLAUDE_CODE_OAUTH_TOKEN`.

## Why No Settings Field

`APISettings` in `config/settings.py` uses `env_nested_delimiter="__"` (pydantic
`BaseSettings`). Binding `CLAUDE_CODE_OAUTH_TOKEN` to a settings field would require the env
var to be named `API__CLAUDE_CODE_OAUTH_TOKEN`, which breaks the convention used by the `claude`
CLI itself. Reading from `os.environ` directly sidesteps this naming mismatch without any
config-layer changes.

## Relationship to ANTHROPIC_* Stripping

The `ANTHROPIC_API_KEY` stripping and the `CLAUDE_CODE_OAUTH_TOKEN` injection are
complementary, not conflicting:

| Mechanism | Purpose |
|-----------|---------|
| Strip `ANTHROPIC_API_KEY` | Prevent SDK-path model calls; force the OAuth path |
| Inject `CLAUDE_CODE_OAUTH_TOKEN` | Supply the long-lived token so the CLI never prompts for login |

## Graceful Degradation

If the token is absent or has expired:

1. The `claude -p` subprocess starts normally.
2. When the short-lived session token expires (typically within ~1 hour), the
   next turn's subprocess call fails its OAuth check rather than hanging on
   an interactive prompt — a headless process has no `/login` screen to
   render in the first place. The failure surfaces as a loud, visible
   `exit_reason=error` (never a silent hang), per the [Headless Session
   Runner](../features/headless-session-runner.md#liveness) contract.
3. The right fix is always to rotate the long-lived token.

## Rollback

Remove `CLAUDE_CODE_OAUTH_TOKEN` from `~/Desktop/Valor/.env`. After iCloud syncs to all
machines and the worker restarts, sessions revert to the short-lived OAuth path, which will
begin failing loudly once the short-lived token expires.

## See Also

- `agent/session_runner/role_driver.py` — injection logic and rationale
- `tools/doctor.py` — presence + prefix health check
- [Headless Session Runner](../features/headless-session-runner.md) — full session-execution design
