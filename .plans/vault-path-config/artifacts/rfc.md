# RFC: Configurable Vault Path

## Context

Valor stores private per-machine config in a "vault" directory: `.env` secrets, `projects.json` ownership map, persona overlays, identity overrides, Google OAuth credentials, and reflections schedule. The vault location is currently hardcoded to `~/Desktop/Valor/` in:

- `config/settings.py:158` (Python default for `GOOGLE_CREDENTIALS_DIR`)
- All four global skills: `setup`, `update`, `do-deploy`, `do-pr-review` (~50 references)
- `.claude/agents/baseline-verifier.md`
- `README.md`, `CLAUDE.md`, `config/README.md`, `tools/google_workspace/README.md`
- Docstrings and error messages in `bridge/routing.py`, `reflections/utils.py`, `tools/valor_calendar.py`

Three env vars (`GOOGLE_CREDENTIALS_DIR`, `PROJECTS_CONFIG_PATH`, `REFLECTIONS_YAML`) exist as escape hatches but the setup skill never honors them, no doc mentions them, and there's no master variable that anchors them.

The Desktop choice was originally made for iCloud sync — macOS Desktop is iCloud-synced when a user has Continuity / Universal Clipboard / iCloud Drive on. This created two real problems:

1. **iCloud rename bug** (just hit on Kevin's machine): when `~/Desktop/Valor/` is removed and recreated, iCloud's conflict resolver renames the new copy to `Valor 2`. Every subsequent `/setup` and `/update` invocation silently fails on file lookups.
2. **Single-machine users** get Desktop clutter for no benefit. Users who don't want their secrets in iCloud have no path off Desktop short of editing 50+ files.

Worse, the vault choice is **opinionated about cloud strategy** — Valor implicitly opts users into iCloud Desktop sync. Some users prefer Dropbox, Resilio, rsync, or no sync at all.

## Problem

Make the vault path:
1. Configurable per machine, with the user owning the cloud/backup decision
2. Promptable during interactive setup, in a harness-agnostic way (Claude Code today, other harnesses tomorrow)
3. Settable via flag for automated/scripted installs (CI, headless deploys, infrastructure-as-code)
4. Honored everywhere in the codebase, the skills, and the docs

Without:
- Forcing a specific cloud solution (no iCloud default, no Dropbox default, no opinion)
- Breaking existing `~/Desktop/Valor/` installs (back-compat for the user base already on Desktop)

## Proposal

### Single master env var: `VALOR_VAULT_DIR`

One variable anchors all vault paths. The existing per-path overrides (`GOOGLE_CREDENTIALS_DIR`, `PROJECTS_CONFIG_PATH`, `REFLECTIONS_YAML`) become **secondary** — they win against the cascade if explicitly set, but the default cascade flows from `VALOR_VAULT_DIR`.

```
Resolution cascade (highest priority first):
1. Per-path env var (e.g., GOOGLE_CREDENTIALS_DIR) — explicit override
2. VALOR_VAULT_DIR — master vault location
3. Fallback default (see "Default policy" below)
```

### Default policy: respect Tom's existing default; add an override knob

The codebase ships only the env-var override. Tom's existing default (`~/Desktop/Valor/`) stays as the runtime fallback. The default cascade for a fresh install:

1. `VALOR_VAULT_DIR` env var set → use it
2. `~/Desktop/Valor/` exists and contains `.env` → use it (the established default)
3. Nothing found → setup must prompt, install scripts must require `--vault-dir`

Custom locations like `~/.valor/`, `~/Documents/Valor/`, `~/iCloud Drive/Valor/` are **user choices** that flow through `VALOR_VAULT_DIR` (or `--vault-dir` at install time) — they are not codebase-baked tiers. The `/setup` picker offers them as labeled options at install time, but the runtime resolver doesn't auto-detect any of them.

A code path that can't find a vault and has no way to prompt **must error loudly**, not silently default. This prevents "the worker started in some weird location" surprises.

### Harness-agnostic prompt

When `/setup` runs interactively, it must ask the user where to put the vault. Different harnesses have different prompt primitives:

- **Claude Code**: `AskUserQuestion` tool
- **Other harnesses**: stdin prompts, `gum input`, web UI dialogs, etc.

Approach: introduce a thin `tools/install/prompt.py` shim. It exposes one function — `ask_choice(question, options) -> str` — that detects the active harness and dispatches:

```python
def ask_choice(question: str, options: list[str], header: str = "Vault path") -> str:
    """Prompt the user to choose from a list of options.

    Detects the active harness:
    - Claude Code (env VALOR_HARNESS=claude-code or specific tool sentinel) → AskUserQuestion
    - Generic TTY → readline prompt with numbered options
    - Non-interactive → raise InstallPromptUnavailable
    """
```

Skills call this shim rather than embedding any specific prompt primitive. The Claude Code adapter writes a JSON instruction the skill renders as `AskUserQuestion`; other adapters fall back to TTY.

### CLI flag for automation

Setup-related entry points accept `--vault-dir <path>`:

```bash
# Manual install (interactive prompt fires if --vault-dir absent)
./scripts/install.sh

# Automated install (no prompt, fails if path doesn't pass validation)
./scripts/install.sh --vault-dir ~/.valor
VALOR_VAULT_DIR=~/.valor ./scripts/install.sh
```

A flag wins against the env var; both win against the cascade. Validation rejects paths that:
- Don't exist AND can't be created (permissions)
- Are inside the repo (would commit secrets)
- Are inside `/tmp` or other ephemeral roots

### What "informing all skills" means concretely

| Touchpoint | Change |
|---|---|
| `config/settings.py` | New `VaultSettings` Pydantic model with `dir`, `env_path`, `projects_path`, `personas_dir`, `identity_path`, `google_credentials_dir`, `reflections_yaml` properties. Default factory reads `VALOR_VAULT_DIR` → cascade → error |
| `bridge/routing.py:51-79` | Use `vault.projects_path` instead of `~/Desktop/Valor/projects.json` literal |
| `tools/google_workspace/auth.py:32` | Use `vault.google_credentials_dir`; keep `GOOGLE_CREDENTIALS_DIR` as override |
| `agent/reflection_scheduler.py:50` | Use `vault.reflections_yaml`; keep `REFLECTIONS_YAML` as override |
| `reflections/utils.py:42-52` | Use `vault.projects_path` |
| `bridge/telegram_bridge.py:45` | Update docstring + remove iCloud presumption |
| `tools/valor_calendar.py:27` | Update docstring; calendar config uses `vault.dir` |
| `.claude/skills-global/setup/SKILL.md` | Rewrite to call `prompt.py:ask_choice` for vault path; replace every `~/Desktop/Valor/` literal with `${VALOR_VAULT_DIR}`. Add the choice menu (see "Setup flow" below). |
| `.claude/skills-global/update/SKILL.md` | Replace ~10 hardcoded references with `${VALOR_VAULT_DIR}` resolution |
| `.claude/skills-global/do-deploy/SKILL.md` | Same |
| `.claude/skills-global/do-pr-review/SKILL.md` | Same |
| `.claude/agents/baseline-verifier.md` | Same |
| `README.md`, `CLAUDE.md`, `config/README.md`, `tools/google_workspace/README.md` | Replace presumption "secrets live in `~/Desktop/Valor/`" with "secrets live in your vault directory (set via `VALOR_VAULT_DIR`)". Add a short "Picking a vault location" subsection that lists tradeoffs — Desktop, hidden home dir, iCloud Drive, Dropbox, Resilio, no-sync. **Take no opinion**. |
| `.env.example` | Add `VALOR_VAULT_DIR=` placeholder near the top with a comment. Note that if `.env` is symlinked into the vault, `VALOR_VAULT_DIR` is informational/redundant — the symlink already encodes the location. The OS env var matters for processes that read `.env` indirectly. |

### Setup flow (interactive)

```
Step 0: Vault location (NEW — runs before everything else)
  - If VALOR_VAULT_DIR is set → use it, skip prompt
  - Else if --vault-dir was passed → use it, skip prompt
  - Else, ask via harness:

    "Where should Valor's secrets vault live?"
      1. ~/.valor/ — hidden home dir, no sync
      2. ~/Documents/Valor/ — Documents folder (often iCloud-synced)
      3. ~/iCloud Drive/Valor/ — explicit iCloud Drive root
      4. ~/Desktop/Valor/ — original default (iCloud-synced via Desktop)
      5. Custom path — I'll type one

    [Tradeoff blurbs shown next to each option, no recommendation]

  - Validate the path (writable, not in repo, not in /tmp)
  - Create the directory
  - Write the bootstrap symlink (if user wants `~/.valor` shortcut)
  - Continue to existing Step 1 (uv install)
```

Subsequent steps (envfile creation, projects.json, persona overlays, etc.) write into the chosen vault directly. No `~/Desktop/Valor/` literal anywhere in the skill.

### Migration for existing installs

For users with existing `~/Desktop/Valor/` who run any updated skill:

- **Detect**: `~/Desktop/Valor/.env` exists AND `VALOR_VAULT_DIR` is unset
- **Action**: log INFO once: "Vault directory resolved to ~/Desktop/Valor (source: default_desktop). To relocate, set VALOR_VAULT_DIR or run /setup --relocate-vault." Then continue normally.
- **Opt-in migration**: a new `/setup --relocate-vault` flag prompts for a new path, copies the contents, repoints the repo `.env` symlink, and either deletes or archives the old location based on user choice.

No silent moves. No forced migration. Existing installs keep working unchanged.

### iCloud rename bug — sidestep when picker is used, detect everywhere

The iCloud "Valor 2" rename is a Finder-layer bug we can't reliably fix from Python. The default fallback remains `~/Desktop/Valor/` (Tom's existing default), so users who don't pick anything keep the historical behavior — and the historical iCloud risk. Sidestep it by:
1. The `/setup` picker offers non-Desktop options (hidden home, Documents, explicit iCloud Drive, custom) so users who care can move off Desktop
2. Detecting the symptom in `bridge/config_validation.py`: if `~/Desktop/` contains both `Valor` and `Valor 2`, `Valor 3`, etc., refuse to start with a clear error message pointing at the iCloud conflict

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Default to `~/.valor/`** instead of presuming nothing | Still opinionated; some users want sync, would silently end up in a non-synced location. Better to ask. |
| **Default to `~/iCloud Drive/Valor/`** when iCloud is available | Still opinionated and iCloud-specific. User may want Dropbox or no sync. |
| **Single env var with no per-path overrides** | Removes legitimate use cases — e.g., shared `GOOGLE_CREDENTIALS_DIR` across multiple Valor installs. |
| **Bootstrap config file `~/.valor.conf`** | Adds a third config layer (env, file, .env). YAGNI; env var + symlink covers it. |
| **Detect iCloud Desktop sync and warn before writing to Desktop** | Doesn't solve the underlying opinionation; still creates Desktop clutter for users who pick Desktop knowing the tradeoff. |
| **Auto-migrate existing `~/Desktop/Valor/` users to `~/.valor/`** | Forced moves break user expectation. Opt-in `--relocate-vault` is safer. |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Users on launchd services (worker, watchdog, reflections) start with no `VALOR_VAULT_DIR` in their env | High | The launchd plists must be updated to set `VALOR_VAULT_DIR` explicitly; install scripts inject it at plist generation time. Bake it in at install, not at launch. |
| Existing installs break when an updated skill assumes `VALOR_VAULT_DIR` is set | High | Cascade falls back to `~/Desktop/Valor/` when `VALOR_VAULT_DIR` is unset but `~/Desktop/Valor/.env` exists — Tom's existing default keeps working. |
| Harness-agnostic prompt shim doesn't actually detect non-Claude-Code harnesses | Medium | Ship Claude-Code + TTY-fallback as the only two paths. Other harnesses can add adapters later. Document the contract explicitly. |
| iCloud rename bug strikes someone who picks Desktop anyway | Low | Detection in `config_validation.py` errors loudly; doc warning in the picker. |
| Persona overlay paths inside `projects.json` go stale after vault relocation | Medium | Migration tool rewrites them. Skip-on-not-found tolerated for non-default personas. |
| `.env.example` references in user docs go out of sync after path changes | Low | Add a docs-test that greps for `~/Desktop/Valor` references and fails CI |

## Open questions

1. Should the prompt shim be its own micro-package or live inside the repo?
2. Does the prompt shim need to support multi-step / conditional flows, or is `ask_choice` enough for v1?
3. Should the picker show iCloud-Drive-specific tradeoffs (file-on-demand evictions, sync conflicts) or stay generic?
4. Is `VALOR_VAULT_DIR=~/path` set in `.env` itself useful, or should it strictly be an OS env var? (Today: set in `.env` is fine because the repo `.env` symlink encodes vault location and code reads `.env` first via dotenv.)
5. Migration `/setup --relocate-vault` flow — first-class command or separate tool (`scripts/relocate_vault.py`)?
6. Should the existing per-path env vars (`GOOGLE_CREDENTIALS_DIR`, etc.) be deprecated with a removal timeline, or kept indefinitely?

## Out of scope

- Implementing harness adapters for non-Claude-Code environments (TTY fallback only in v1)
- Deleting or hiding `~/Desktop/Valor/` from existing users (opt-in only)
- Cloud sync orchestration (Valor never manages sync; user picks their own tool)
- Per-machine vault paths via `projects.json` — every machine's vault is its own concern
- Vault encryption-at-rest (separate feature, separate plan)
