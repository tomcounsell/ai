# gws CLI Auth (Per-Machine Setup)

How to authenticate the `gws` (`@googleworkspace/cli`) binary on a Valor machine **without** gcloud, by reusing the shared OAuth client already stored in every machine's iCloud-synced vault.

## Why this is its own procedure

`gws` is installed automatically on every machine by `/update` (`npm install -g @googleworkspace/cli`), but it ships **unauthenticated**. Its documented bootstrap, `gws auth setup`, **requires a working `gcloud`** to create a GCP project + OAuth client. On Valor machines that path is unreliable:

- gcloud is frequently broken (e.g. pinned to a Python its bundled `requests` can't import — `ImportError: cannot import name 'JSONDecodeError' from requests.compat`).
- We already have a valid Desktop-app OAuth client in the vault, so creating a *new* one via `gws auth setup` is wasteful and produces drift.

The shortcut: skip `gws auth setup` entirely and hand `gws` the existing OAuth client directly.

## The shared credential

Every Valor machine has the **same** OAuth client secret saved in its local vault folder:

```
~/Desktop/Valor/google_credentials.json
```

This is iCloud-synced, so it is byte-identical across all machines. It is a Desktop-app ("installed") OAuth client — the same client used by the Python `valor-calendar` auth path (see [Google Workspace Auth](google-workspace-auth.md)). `gws` and `valor-calendar` are two independent consumers of this one client; they store their *tokens* in different places.

## Setup procedure (per machine, one-time)

```bash
# 1. Hand gws the shared OAuth client (this replaces `gws auth setup`)
cp ~/Desktop/Valor/google_credentials.json ~/.config/gws/client_secret.json
chmod 600 ~/.config/gws/client_secret.json

# 2. Run the OAuth consent flow — opens a browser; HUMAN step
gws auth login --full      # --full grants all scopes up front; omit for the interactive scope picker

# 3. Verify
gws auth status            # auth_method should become "oauth2"
gws gmail users getProfile --params '{"userId": "me"}'   # live probe
```

Step 1 is fully scriptable/agent-doable. Step 2 is the only human step — Google's consent screen must be approved in a browser by the account owner (valor@yuda.me).

## Where things live

| Artifact | Path | Notes |
|----------|------|-------|
| Shared OAuth client (vault) | `~/Desktop/Valor/google_credentials.json` | Same on every machine (iCloud). Source of truth. |
| gws client config | `~/.config/gws/client_secret.json` | Copy of the above. `gws auth status` reports this as `client_config`. |
| gws encrypted token | `~/.config/gws/credentials.enc` | Per-machine, AES-256-GCM, key in OS keyring. Created by `gws auth login`. Do **not** sync this between machines. |

## Gotchas

- **`gws` prints a preamble line** `Using keyring backend: keyring` to **stdout** before its JSON output. Strip it before piping to a JSON parser, or the parse fails on `line 1 column 1`.
- **Headless machines**: the OS-keyring encryption key is unavailable without a login session. Set `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` so `gws` stores the key in a local `.encryption_key` file instead. The `gws auth login` browser step still needs a machine with a browser.
- **gcloud is not required** and should not be needed for any `gws` operation once `client_secret.json` is in place. If you genuinely need gcloud for something else and it's broken on a Python-version mismatch, point it at a compatible interpreter: `export CLOUDSDK_PYTHON=/usr/local/bin/python3.12`.
- **`/update` detection only**: the update orchestrator (`scripts/update/gws_auth.py`) only *detects* the unauthenticated state and warns `gws auth setup --login`. It never opens a browser or copies the client (it runs non-interactively under launchd). Follow this doc instead of the literal warning command — the `cp` shortcut here avoids gcloud.

## Related

- [Google Workspace Auth](google-workspace-auth.md) — the Python `valor-calendar` OAuth path that uses the same vault OAuth client (different token store).
- `CLAUDE.md` → "Google Workspace CLI (`gws`)" — usage patterns once authenticated.
- `.claude/skills-global/do-deploy/SKILL.md` — references this doc as a per-machine manual step not covered by auto-update.
- `scripts/update/gws_auth.py` — the `/update` detection step.
