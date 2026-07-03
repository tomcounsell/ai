# Configurable Vault Path (`VALOR_VAULT_DIR`)

The vault directory is where Valor stores per-machine private state: the secrets `.env`, `projects.json`, persona overlays, identity overrides, Google OAuth credentials, and the reflections registry. The repo `.env` is a symlink to `<vault>/.env`. Most code paths read from the vault via the `config.settings.vault` singleton; a small set of legitimate fallbacks name the defaults explicitly.

The vault location is configurable per machine via the `VALOR_VAULT_DIR` environment variable. When unset, the code probes `~/.valor/.env` (preferred default) and then `~/Desktop/Valor/.env` (legacy default for existing installs). Custom locations like `~/Documents/Valor/` or `~/iCloud Drive/Valor/` flow through `VALOR_VAULT_DIR` (or the `--vault-dir` install flag), not codebase-baked tiers.

## Cascade

```
VALOR_VAULT_DIR (env var) → <chosen path>
↓ (if unset)
~/.valor/.env exists → ~/.valor/  (preferred default — non-TCC path)
↓ (if neither)
~/Desktop/Valor/.env exists → ~/Desktop/Valor/  (legacy default — iCloud + TCC)
↓ (if none)
raise VaultNotResolved — caller must prompt or fail loudly
```

Per-path env vars (`PROJECTS_CONFIG_PATH`, `GOOGLE_CREDENTIALS_DIR`, `REFLECTIONS_YAML`) remain valid overrides. They're checked at property-access time, so they win over the master vault dir for their specific path even when `VALOR_VAULT_DIR` is set.

## Security model: TCC-restricted vs non-TCC paths

The cascade includes two default tiers because macOS TCC (Transparency, Consent, Control) treats some directories as protected: `~/Desktop`, `~/Documents`, and `~/iCloud Drive`. Processes spawned by launchd do not inherit the user's TCC consent and **hang indefinitely** when calling `open()`/`stat()` on files in those directories. Terminal-spawned processes (`/setup`, the install scripts) are fine because the user granted consent once at install time.

This forces a binary choice at install time: bake the secrets into the plist, or pick a non-TCC vault.

`VaultSettings.path_is_tcc_restricted(path)` (config/settings.py) is the canonical check. `scripts/install/inject_plist_env.py` duplicates the check (no project-package dependency at install time) and uses it to decide between two injection modes:

| Mode | Trigger | What lands in `~/Library/LaunchAgents/*.plist` | Plist permission | Secret location |
|---|---|---|---|---|
| **Lean** | Vault NOT on a TCC path (`~/.valor`, custom paths) | Only an operational allowlist: `VALOR_VAULT_DIR`, `VALOR_PROJECT_KEY`, `VALOR_LAUNCHD`, `ACTIVE_PROJECTS`, `SERVICE_LABEL_PREFIX`, `PATH`, `HOME` | `0644` (no secrets, no need to tighten) | `<vault>/.env` only (`0600`). Worker loads via `pydantic-settings` at runtime. |
| **Full** | Vault on a TCC path (`~/Desktop`, `~/Documents`, `~/iCloud Drive`) | Every key from `<vault>/.env`, including API keys | `0600` (close the world-readable hole) | `<vault>/.env` (`0600`) AND the plist (`0600`). Worker reads from `os.environ`, never opens `.env` at runtime. |

The runtime side mirrors the install-side logic:
- `Settings.model_config.env_file` is `None` only when `VALOR_LAUNCHD=1` AND `vault.is_tcc_restricted` — otherwise pydantic-settings reads `.env` at startup as normal.
- `bridge/routing.py` and `agent/reflection_scheduler.py` skip the vault path under launchd ONLY when `vault.is_tcc_restricted` — otherwise they read from the vault directly.

The picker (`tools/install/prompt.py`) labels each option with its security posture so users can pick informed.

### Why we don't just always inject

Two reasons:
1. **Security surface**: a `0644` plist in `~/Library/LaunchAgents/` is world-readable by other local users, scraped by anything with Full Disk Access (some VPNs, antivirus, cleaner apps), and included in Time Machine / sysdiagnose / iCloud Backup. Secrets at `0600` in one place beats secrets at `0600` in two places.
2. **Operational simplicity**: lean plists have ~7 keys; full plists have whatever's in `.env` (~30+ keys, growing). Lean plists make the launchd surface easier to audit and reason about.

## Where the cascade is implemented

| Component | File | Notes |
|---|---|---|
| Vault resolution | `config/settings.py` (`VaultSettings`, `_get_vault`, `vault` singleton) | Lazy via module `__getattr__`; first access logs INFO with the resolution source. |
| Validation | `config/settings.VaultSettings._validate_dir` | Rejects in-repo paths and ephemeral roots (`/tmp`, `/var/folders`). Always runs, including for explicit `dir=` construction. |
| Per-path properties | `vault.env_path`, `vault.projects_path`, `vault.personas_dir`, `vault.identity_path`, `vault.google_credentials_dir`, `vault.reflections_yaml` | Each honors its specific env-var override. |
| Belt-and-suspenders dotenv loader | `config.settings.load_vault_env()` | Used by CLI tools and the bridge to load `<vault>/.env` on top of the repo-relative `.env` (covers fresh checkouts, broken symlinks, launchd contexts). |
| TCC restriction check | `VaultSettings.path_is_tcc_restricted` / `vault.is_tcc_restricted` | True when the path is under `~/Desktop`, `~/Documents`, or `~/iCloud Drive`. Drives conditional injection at install time and conditional skip-list behavior at runtime. |
| launchd plist injection | `scripts/install/inject_plist_env.py` | Lean (allowlist) or full (entire .env + chmod 0600) injection, decided by the vault's TCC status. See [security model](#security-model-tcc-restricted-vs-non-tcc-paths) above. |

## Picking a vault location

Run `/setup` for the interactive picker. It runs `python -m tools.install.prompt vault-picker`, which presents five labeled options:

1. **`~/.valor/` (Recommended)** — hidden home directory, no sync, single-machine. Non-TCC: secrets stay in `.env`, plists stay lean.
2. **`~/Documents/Valor/`** — Documents folder; often iCloud-synced. TCC-restricted: secrets baked into plists at `chmod 0600`.
3. **`~/iCloud Drive/Valor/`** — explicit iCloud Drive root. TCC-restricted: secrets baked into plists at `chmod 0600`.
4. **`~/Desktop/Valor/`** — legacy default; iCloud-synced via Desktop. TCC-restricted: secrets baked into plists at `chmod 0600`. Subject to the iCloud rename bug if the path is recreated.
5. **Custom path…** — type one (e.g. for Dropbox, Resilio, or a non-synced location). TCC status determined by path prefix.

The picker is the single source of truth for the option list. Skill markdown reads it via the prompt shim and renders via `AskUserQuestion` under Claude Code or `input()` under a TTY.

## Moving an existing vault

There's no dedicated "relocate" tool. To move an existing vault to a new location:

1. `mkdir -p $NEW_VAULT && cp -R ~/Desktop/Valor/. $NEW_VAULT/`
2. `export VALOR_VAULT_DIR=$NEW_VAULT` (and persist in your shell rc / `<new>/.env`).
3. `ln -sfn "$NEW_VAULT/.env" ~/src/ai/.env`
4. Re-run install scripts so the launchd plists pick up the new `VALOR_VAULT_DIR` (the install scripts read it from `os.environ` and bake it into each plist's `EnvironmentVariables` via `scripts/install/inject_plist_env.py`):
   - `./scripts/install_worker.sh`
   - `./scripts/install_sdlc_reflection.sh` (if installed)
   - `./scripts/install_autoexperiment.sh` (if installed)
   - `./scripts/install_nightly_tests.sh` (if installed)
5. Once you're confident the new location works, `mv ~/Desktop/Valor ~/Desktop/Valor.old` (or delete it).

## CI gate

`tests/unit/test_no_hardcoded_vault_path.py` greps `*.py`, `*.sh`, `*.md`, `*.json` for bare `~/Desktop/Valor` literals. It allows lines that:

- mention `VALOR_VAULT_DIR` (the literal is part of a fallback expansion), or
- contain prose keywords like `default`, `fallback`, `cascade`, `iCloud`, `migration` (the literal is descriptive copy).

A handful of files are allowlisted because they legitimately contain the literal as the cascade fallback or the picker option list — see `ALLOWLIST_PREFIXES` in the test for the full list. Doc directories under `docs/features/` and `docs/guides/` are also allowlisted; that doc cleanup is tracked separately and not gated by CI.

## See also

- [`config/settings.py`](../../config/settings.py) — `VaultSettings`, `vault` singleton, `load_vault_env`
- [`tools/install/prompt.py`](../../tools/install/prompt.py) — the picker and prompt shim
- [`scripts/install/inject_plist_env.py`](../../scripts/install/inject_plist_env.py) — launchd plist env injection
- [`.claude/skills-global/setup/SKILL.md`](../../.claude/skills-global/setup/SKILL.md) — Step 0 vault picker flow
- [CLAUDE.md § Secrets](../../CLAUDE.md#secrets) — vault-location tradeoffs table
