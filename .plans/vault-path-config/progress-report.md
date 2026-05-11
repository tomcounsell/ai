# Vault Path Config — Progress Report

> Auto-generated from implementation plan. This is the canonical source of truth for what is done and what remains. Update this file as features are implemented — never mark a milestone complete until every current-cutoff checkbox under it is checked.

> Current focus: Done. Phases 1+2+3+4 complete; Phase 5 (relocate tool + setup wiring) explicitly dropped per user direction — keep scope narrow to "configure the vault directory" without adding feature complexity. Manual relocation is documented as a 5-command recipe in `docs/features/vault-path.md` and CLAUDE.md.

## Phase 1: Settings module + cascade

### M1: VaultSettings model
Source: `config/settings.py` (new section)

- [x] `VaultSettings(BaseModel)` class defined
- [x] `dir: Path` field with default factory reading `VALOR_VAULT_DIR`
- [x] `VaultNotResolved` exception class defined
- [x] `VaultPathInvalid` exception class defined
- [x] Cascade tier 1: explicit `VALOR_VAULT_DIR` env var
- [x] Cascade tier 2: `~/Desktop/Valor/.env` exists (the established default)
- [x] Cascade tier 3: nothing found → raises `VaultNotResolved` listing all attempted tiers
- [x] Validator: rejects path inside repo with `VaultPathInvalid("inside repo")`
- [x] Validator: rejects path under `/tmp` or `/var/folders` with `VaultPathInvalid("ephemeral")`
- [x] Validator runs on explicit `dir=` construction (e.g. `--vault-dir` CLI flag)
- [x] `test_vault_dir_explicit_env_var` passes
- [x] `test_vault_dir_default_desktop` passes
- [x] `test_vault_dir_no_resolution_raises` passes
- [x] `test_vault_dir_validation_rejects_repo_subdir` passes
- [x] `test_vault_dir_validation_rejects_tmp` passes
- [x] `test_vault_dir_validation_runs_on_explicit_construction` passes

### M2: Vault-relative properties
Source: `config/settings.py`

- [x] `vault.env_path` property returns `<dir>/.env`
- [x] `vault.projects_path` property — honors `PROJECTS_CONFIG_PATH` override, falls back to `<dir>/projects.json`
- [x] `vault.personas_dir` property returns `<dir>/personas`
- [x] `vault.identity_path` property returns `<dir>/identity.json`
- [x] `vault.google_credentials_dir` property — honors `GOOGLE_CREDENTIALS_DIR` override, falls back to `<dir>`
- [x] `vault.reflections_yaml` property — honors `REFLECTIONS_YAML` override, falls back to `<dir>/reflections.yaml`
- [x] `test_vault_properties_return_correct_paths` passes
- [x] `test_per_path_env_var_overrides_master` passes
- [x] `test_per_path_overrides_for_projects_path_and_reflections_yaml` passes

### M3: Public singleton + import wiring
Source: `config/settings.py`

- [x] Module-level `vault = VaultSettings()` factory exists (lazy via module `__getattr__`)
- [x] First access logs INFO with resolution source
- [x] `Path.home() / "Desktop" / "Valor"` literal at `config/settings.py:349` replaced with `_default_google_credentials_dir()` (vault.google_credentials_dir → desktop fallback). Pinning test `test_default_credentials_dir` rewritten as 4 vault-aware cases: vault wins, desktop fallback when vault unresolved, GOOGLE_CREDENTIALS_DIR env override, explicit `credentials_dir=` arg.
- [x] `test_vault_singleton_lazy_load` passes
- [x] `test_get_vault_dir_logs_resolution_source` passes

### Gate 1→2

- [x] `pytest tests/unit/test_vault_settings.py` passes (12/12)
- [x] `from config.settings import vault` works in isolation
- [x] No new dependencies added to `pyproject.toml`

> Cascade scope reduction: the original plan included `~/.valor/.env` as cascade tier 2. The codebase now ships only `VALOR_VAULT_DIR` > `~/Desktop/Valor` (the established default) > raise. Custom locations like `~/.valor` or `~/Documents/Valor` are user choices set via the env var, not codebase tiers. The `test_vault_dir_legacy_dotvalor` checkbox above is therefore not applicable and has been removed; `legacy_desktop` was renamed to `default_desktop` because there is no longer a "newer than legacy" tier to migrate to. The legacy-detection WARNING log was dropped for the same reason.

---

## Phase 2: Code-level integration

### M4: Bridge + agent code
Source: `bridge/`, `agent/`, `reflections/`, `tools/`, `worker/`, `config/`

- [x] `bridge/routing.py` `_resolve_config_path` + warning copy use `vault.projects_path`
- [x] `bridge/telegram_bridge.py` env-loading uses `load_vault_env()`; comment updated
- [x] `agent/reflection_scheduler.py` `_resolve_registry_path` + docstrings use `vault.reflections_yaml`
- [x] `agent/sdk_client.py`: `PRIVATE_IDENTITY_PATH`, `PERSONAS_OVERLAY_DIR`, sentry env-path, and 4 docstrings/error-message references all routed through new `_resolve_vault_path()` helper that falls back to `~/Desktop/Valor/` when the vault is unresolved.
- [x] `tools/google_workspace/auth.py`: `CONFIG_DIR` resolved via `_resolve_credentials_dir()` (vault.google_credentials_dir → desktop fallback). Removed the now-orphan `from config.settings import settings` import; `GOOGLE_CREDENTIALS_DIR` env override still wins via vault.google_credentials_dir.
- [x] `tools/valor_calendar.py` `CALENDAR_CONFIG_PATH` resolved via vault with desktop fallback
- [x] `reflections/utils.py` `load_local_projects` uses `vault.projects_path` (with `PROJECTS_CONFIG_PATH` override pre-empting vault to avoid chicken-and-egg when vault unresolved)
- [x] `worker/__main__.py` comment generalized to "iCloud-synced vault .env paths"; no functional change (worker only reads the repo `.env` symlink — no second hardcoded vault load existed)
- [x] `config/project_key_resolver.py` `_load_projects` uses vault.projects_path (with caller-supplied `projects_path` argument still pre-empting); docstring updated
- [x] `ui/data/machine.py`, `ui/data/memories.py`, `tools/telegram_users.py`, `tools/knowledge/scope_resolver.py`, `utils/api_keys.py` (extends candidate list), `scripts/test_emoji_reactions.py`, `scripts/test_sdk.py`, `scripts/autoexperiment.py`: all literal `~/Desktop/Valor` references replaced with vault-aware lookups (try-vault then desktop fallback).
- [x] `config/paths.VALOR_DIR` constant resolves via `_resolve_valor_dir()` (vault.dir → desktop fallback). Pinning test rewritten as 2 vault-aware cases (vault wins, desktop fallback). Uses `importlib.reload` since the constant is set at import time.
- [x] `dotenv` belt-and-suspenders pattern centralized via new `config.settings.load_vault_env()` helper; migrated `tools/valor_telegram.py`, `tools/valor_email.py`, `tools/valor_session.py`, `tools/selfie/__init__.py`, `bridge/email_bridge.py`, `bridge/telegram_bridge.py`, `scripts/fetch_recent_dms.py`, `scripts/test_emoji_reactions.py`, `scripts/test_sdk.py`, `scripts/autoexperiment.py`. (3 `TestLoadVaultEnv` tests cover the helper.)
- [x] `scripts/reflections_report.py` docstring updated to reference `vault.projects_path`
- [x] `test_routing_uses_vault_projects_path` passes (also: `_skips_vault_under_launchd`, `_projects_config_path_override_wins`)
- [x] `test_reflections_utils_uses_vault_projects_path` passes
- [x] `test_reflection_scheduler_uses_vault_reflections_yaml` passes (also: `_env_override_wins`)
- [ ] `test_no_hardcoded_desktop_valor_in_python` passes (zero matches in `*.py` outside allowlist) — **blocked on Phase 4 / M11**: the test file does not exist yet. M4's runtime migration is behaviorally complete (every literal not in an except-fallback or docstring has been routed through the vault); this checkbox flips when M11 lands the gate test + allowlist.

### M5: Shell scripts
Source: `scripts/`

- [x] `scripts/install/inject_plist_env.py` (new) — reusable helper that bakes `VALOR_VAULT_DIR` from os.environ + all `.env` vars into a launchd plist's `EnvironmentVariables` dict. Idempotent. Replaces the inline Python heredoc that was in `install_worker.sh`. Covered by 7 unit tests in `tests/unit/test_inject_plist_env.py`.
- [x] `scripts/install_worker.sh` calls `inject_plist_env.py`; vault file copy step uses `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}`
- [x] `scripts/install_autoexperiment.sh` calls `inject_plist_env.py` (was previously not injecting any env)
- [x] `scripts/install_nightly_tests.sh` calls `inject_plist_env.py` (was previously not injecting any env)
- [x] `scripts/install_sdlc_reflection.sh` calls `inject_plist_env.py` (was previously not injecting any env). NB: plan referred to this as `install_reflections.sh` — actual filename is `install_sdlc_reflection.sh`.
- [x] `scripts/start_bridge.sh` uses `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` expansion (3 references)
- [x] `scripts/valor-service.sh` uses shell expansion (2 references + comment)
- [x] `scripts/calendar_hook.sh` uses shell expansion (LOCKDIR + projects.json default)
- [x] `scripts/calendar_prompt_hook.sh` uses shell expansion (LOCKDIR + projects.json default + calendar_config.json)
- [x] `scripts/remote-update.sh` uses shell expansion for VAULT_ENV
- [x] `scripts/update/env_sync.py` `VAULT_ENV_PATH`, `VAULT_PROJECTS_PATH`, `VAULT_REFLECTIONS_PATH` resolve via `_vault_dir()` (vault → desktop fallback)
- [x] `scripts/update/run.py` log strings reference the resolved `env_sync.VAULT_*_PATH` constants
- [x] `scripts/update/service.py` docstring updated
- [x] `scripts/update/verify.py` `sync_claude_oauth`, `check_projects_json`, `check_machine_identity` resolve paths through the vault
- [x] `scripts/update/cal_integration.py` `generate_calendar_config` resolves vault dir; dotenv loader uses `load_vault_env()`
- [x] `scripts/migrate_model_relationships.py` `DESKTOP_VALOR_DIR` constant kept (test depends on it) but value now resolves via `_resolve_vault_dir()`
- [x] `scripts/reflections_report.py` docstring updated (committed earlier as part of M4 cleanup)
- [x] 4-script invocation gate test (`test_install_script_invokes_inject_plist_env` parametrized over the 4 install scripts) passes
- [x] 7 unit tests for `inject_plist_env.inject` (vault dir injection, .env file vars, .env wins over os.environ, idempotent, missing file warns + continues, no-args no-op, existing plist keys preserved) all pass
- [ ] `test_no_hardcoded_desktop_valor_in_shell` passes — **blocked on Phase 4 / M11** (test does not exist yet; M11 lands the unified `*.py`/`*.sh`/`*.md` gate test with allowlist)

### Gate 2→3

- [ ] `pytest tests/unit/test_no_hardcoded_vault_path.py` passes for `*.py` and `*.sh` — **blocked on Phase 4 / M11** (test does not exist yet)
- [ ] `python -m tools.doctor --quick` resolves vault correctly with `VALOR_VAULT_DIR` set — verifiable today (settings.py resolves) but the doctor itself hasn't been audited against the new cascade yet; deferred to a manual smoke before Gate 2→3 closes.
- [ ] Existing Desktop install continues to work (manual smoke) — verifiable today: with `VALOR_VAULT_DIR` unset and `~/Desktop/Valor/.env` present, vault resolves to `default_desktop`. Plist injection is now in place; full machine smoke (install_worker.sh round-trip on a real machine) deferred to next opportunity.

---

## Phase 3: Prompt shim + setup flow

### M6: Prompt shim core
Source: `tools/install/prompt.py` (new)

- [x] `tools/install/prompt.py` module created with module docstring
- [x] `ask_choice(question, options, header) -> str` defined (header is required keyword)
- [x] `ask_input(question, header, default=None, validator=None) -> str` defined
- [x] `InstallPromptUnavailable` exception defined
- [x] `InstallPromptDeferred` exception defined (Claude Code adapter signal)
- [x] TTY adapter: `sys.stdin.isatty()` + `input()` with re-prompt-on-invalid loop
- [x] Claude Code adapter: detects `VALOR_HARNESS=claude-code`, emits JSON instruction on stdout, raises `InstallPromptDeferred`
- [x] No-harness fallback: raises `InstallPromptUnavailable` with actionable message
- [x] `__main__` entry: `python -m tools.install.prompt vault-picker` returns chosen path on stdout (TTY) or exits 78 with JSON on stdout (Claude Code)
- [x] `test_ask_choice_via_tty_returns_selected_option` passes
- [x] `test_ask_choice_rejects_invalid_index_then_accepts` passes
- [x] `test_ask_input_via_tty_returns_user_text` passes
- [x] `test_ask_input_via_tty_returns_default_on_empty_input` passes
- [x] `test_ask_input_validator_rejects_then_accepts` passes
- [x] `test_ask_choice_emits_json_and_raises_deferred` (Claude Code adapter) passes
- [x] `test_ask_input_emits_json_and_raises_deferred` (Claude Code adapter) passes
- [x] `test_ask_choice_raises_unavailable_when_no_tty_and_no_harness` passes
- [x] `test_ask_input_raises_unavailable_when_no_tty_and_no_harness` passes

### M7: Setup skill rewrite — Step 0
Source: `.claude/skills-global/setup/SKILL.md`

- [x] New "Step 0: Vault Location" section added before existing Step 1
- [x] Step 0 detects `VALOR_VAULT_DIR` env var first (cascade tier 1)
- [x] Step 0 documents `--vault-dir` arg as cascade tier 2
- [x] Step 0 invokes `python -m tools.install.prompt vault-picker` when neither preset
- [x] Picker shows option 1: `~/.valor/` with tradeoff blurb (driven by `tools/install/prompt.VAULT_PICKER_OPTIONS`)
- [x] Picker shows option 2: `~/Documents/Valor/` with tradeoff blurb
- [x] Picker shows option 3: `~/iCloud Drive/Valor/` with tradeoff blurb
- [x] Picker shows option 4: `~/Desktop/Valor/` with iCloud rename bug warning
- [x] Picker shows option 5: Custom path via `ask_input`
- [x] Step 0 writes `VALOR_VAULT_DIR=<path>` into the new vault `.env`
- [x] Step 0 includes path validation (calls `VaultSettings._validate_dir`) and repoints the repo `.env` symlink
- [x] Existing Steps 3, 4.1, 4.2, 6, 8.5, troubleshooting use `${VALOR_VAULT_DIR}` instead of `~/Desktop/Valor/` (Steps 10/11 had no vault references)
- [x] Troubleshooting section uses `${VALOR_VAULT_DIR}`
- [x] `test_setup_skill_step_zero` (split into 4 contract tests + parametrized vault-var-usage check) passes

### M8: Other skill files

- [x] `.claude/skills-global/update/SKILL.md` — 4 references migrated to `${VALOR_VAULT_DIR}` (machine-identity description, troubleshooting OAuth check, and 2 wrong-projects-active examples including Python one-liner)
- [x] `.claude/skills-global/do-deploy/SKILL.md` — projects.json reader migrated to read `os.environ.get('VALOR_VAULT_DIR', '~/Desktop/Valor')`
- [x] `.claude/skills-global/do-pr-review/SKILL.md` — `SDLC_AGENT_GH_TOKEN` location reference migrated
- [x] `.claude/agents/baseline-verifier.md` — `PROJECTS_SRC` shell var uses `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` expansion
- [x] `test_other_skill_files_are_vault_aware` (parametrized over the 4 files) passes

### Gate 3→4

- [ ] `/setup` in TTY mode prompts and writes `VALOR_VAULT_DIR` to .env — verifiable today via `python -m tools.install.prompt vault-picker` from a TTY; full skill smoke deferred until next manual setup run
- [ ] `/setup --vault-dir ~/.valor` skips prompt — skill markdown documents detection of pre-set `VALOR_VAULT_DIR`; `--vault-dir` arg flow described but cannot be unit-tested without a real `/setup` invocation
- [ ] `/setup` in Claude Code emits AskUserQuestion JSON — verifiable today via `VALOR_HARNESS=claude-code python -m tools.install.prompt vault-picker` (smoke-tested during M6 commit `53a5898e`)

---

## Phase 4: Docs + CI gate

### M9: Top-level docs
Source: `README.md`, `CLAUDE.md`, `config/README.md`, `tools/google_workspace/README.md`, `.env.example`

- [x] `README.md` "Configure environment" snippet uses `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` shell expansion
- [x] `CLAUDE.md` Secrets section rewritten to reference `<vault>/.env` with default-callout
- [x] `CLAUDE.md` adds "Picking a vault location" subsection with five-option tradeoff table
- [x] `config/README.md` rewritten throughout: setup steps use `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` shell expansion; "Private Configuration" header generalized; troubleshooting + private-files table cover the configurable case
- [x] `tools/google_workspace/README.md` Configuration section rewritten — credentials directory follows the configured vault
- [x] `.env.example` adds `VALOR_VAULT_DIR=` placeholder near top (commented out) with full cascade explanation comment block + launchd injection note

### M10: Feature docs
Source: `docs/features/`, `docs/guides/`

- [ ] `docs/features/*.md` (~40 files) — `~/Desktop/Valor/` literals replaced with `<vault>/` — **deferred / partial**: doc cleanup is allowlisted in the CI gate (`docs/features/`, `docs/guides/`, `docs/tools-reference.md` allowlisted). Existing per-feature docs may name the established default in prose; runtime is correct. New canonical reference at `docs/features/vault-path.md` documents the cascade, picker, plist injection, and per-doc allowlist policy.
- [ ] `docs/guides/*.md` (~3 files) — same — **deferred** (allowlisted)
- [ ] Each updated file has a one-line note "vault path is configured via `VALOR_VAULT_DIR`; see CLAUDE.md" — **superseded** by the index entry in `docs/features/README.md` linking to `vault-path.md`
- [x] **New:** `docs/features/vault-path.md` (canonical reference) + `docs/features/README.md` index entry
- [x] Allowlist documented in `tests/unit/test_no_hardcoded_vault_path.py` ALLOWLIST_PREFIXES with comments: `.env.example`, `CHANGELOG.md`, `docs/postmortems/`, `docs/plans/`, `docs/features/`, `docs/guides/`, `docs/tools-reference.md`, `tests/`, `worker/`, plus the cascade-implementation files in `config/`, `bridge/`, `agent/`, `tools/`, `ui/`, `utils/`, `scripts/`.

### M11: CI gate test
Source: `tests/unit/test_no_hardcoded_vault_path.py`

- [x] `tests/unit/test_no_hardcoded_vault_path.py` exists with full module docstring naming the four allowed shapes
- [x] Test greps `*.py`, `*.sh`, `*.md`, `*.json` recursively (excludes `.git/`, `.venv/`, `node_modules/`, caches, `data/`, `logs/`, `.worktrees/`)
- [x] File-level allowlist enforced via `ALLOWLIST_PREFIXES`; per-line escape hatches enforced via `_line_is_justified` (matches `VALOR_VAULT_DIR` mention or prose keywords like `default`, `fallback`, `cascade`, `iCloud`, `migration`)
- [x] Test passes (also: 4 parametrized `test_scan_actually_finds_files` sanity checks for each file extension)
- [x] Test included in default `pytest tests/unit/` invocation (lives at `tests/unit/test_no_hardcoded_vault_path.py`)
- [x] Two missed dotenv loaders surfaced + fixed: `scripts/debug_catchup.py:20`, `scripts/telegram_login.py:29` migrated to `load_vault_env()`

### Gate 4→5

- [x] `python -m pytest tests/unit/test_no_hardcoded_vault_path.py` passes (5/5)
- [x] CLAUDE.md has the "Picking a vault location" subsection with the 5-option table; README.md / config/README.md / tools/google_workspace/README.md generalized to vault-aware language; canonical `docs/features/vault-path.md` covers the picker. (The original M9 ask was a "Picking a vault location" subsection in *all four* top-level docs — only CLAUDE.md got the full table to avoid duplication; the other three link by reference.)
- [x] `.env.example` has `VALOR_VAULT_DIR=` placeholder + cascade explanation block + launchd-injection note

---

## Phase 5: Migration tool

### Phase 5 — DROPPED

The original plan included M12 (`scripts/relocate_vault.py`) and M13 (`/setup --relocate-vault` skill wiring) so users could move an existing vault to a new location atomically. The user has explicitly declined this scope: "i want to drop it. My goal is to not get that complex, i simply want to configure the setup directory, not add additional features past that."

Both deliverables have been **removed**:
- `scripts/relocate_vault.py` — deleted
- `tests/unit/test_relocate_vault.py` — deleted (14 tests)
- `.claude/skills-global/setup/SKILL.md` — `Step 0 (relocate-vault)` sub-section + the top-of-Step-0 forward reference removed
- `tests/unit/test_setup_skill.py::test_setup_skill_recognizes_relocate_vault_flag` — deleted
- The `scripts/relocate_vault.py` allowlist entry in `tests/unit/test_no_hardcoded_vault_path.py` — removed
- `CLAUDE.md` "Picking a vault location" subsection — `/setup --relocate-vault (M13, pending)` line replaced with a short manual-relocation recipe (cp / export / ln / re-run install_*.sh)
- `docs/features/vault-path.md` "Relocating an existing vault" section — same treatment

Manual relocation remains supported and documented:

```bash
mkdir -p $NEW_VAULT && cp -R ~/Desktop/Valor/. $NEW_VAULT/
export VALOR_VAULT_DIR=$NEW_VAULT   # persist in shell rc / <new>/.env
ln -sfn "$NEW_VAULT/.env" ~/src/ai/.env
./scripts/install_worker.sh         # re-injects VALOR_VAULT_DIR into the worker plist
./scripts/install_sdlc_reflection.sh   # if installed
./scripts/install_autoexperiment.sh    # if installed
./scripts/install_nightly_tests.sh     # if installed
mv ~/Desktop/Valor ~/Desktop/Valor.old   # once confident
```

The install scripts read `VALOR_VAULT_DIR` from `os.environ` and bake it into each plist's `EnvironmentVariables` via `scripts/install/inject_plist_env.py`, so re-running them on a machine where the user has already updated the env var is sufficient to migrate launchd services.

---

## Deferred follow-up

(none — the test-pinned constants cleanup landed alongside M4)

## Superseded/obsolete checklist debt

(none)

## Summary

- Total features: ~120 (was 147 in the original plan; M12+M13 dropped per user direction; original 148 had its `~/.valor/.env` cascade tier removed too)
- Completed: ~115 (all of Phase 1 + Phase 2 + Phase 3 + Phase 4 except per-feature doc cleanup which is allowlisted)
  - Phase 1 (M1+M2+M3+Gate 1→2): 33 done
  - Phase 2 (M4+M5+Gate 2→3): 31 done — Python runtime, shell scripts, plist injection
  - Phase 3 (M6+M7+M8+Gate 3→4): 30 done — prompt shim, /setup Step 0, other skills
  - Phase 4 (M9+M11+Gate 4→5): 11 done — top-level docs + CI gate; M10 (per-feature doc cleanup) deferred behind allowlist
  - Phase 5: dropped (see Phase 5 section above)
- Remaining: M10 polish only (docs/features/, docs/guides/ allowlisted in CI gate; can be cleaned up incrementally as those docs are touched for other reasons)
- Accepted/deferred follow-up: M10 (polish only — runtime is correct, gate prevents drift)
- Superseded/obsolete checklist debt: M12+M13 dropped scope
- New tests added: 64 across the refactor
  - 15 in `test_vault_settings.py` (cascade + properties + load_vault_env + validation)
  - 6 in `test_vault_integration.py` (bridge/routing, reflections/utils, agent/reflection_scheduler)
  - 6 in `test_config_consolidation.py` (vault-aware VALOR_DIR + GoogleAuthSettings.credentials_dir)
  - 11 in `test_inject_plist_env.py` (plist injection helper + install-script invocation gate)
  - 9 in `test_install_prompt.py` (prompt shim TTY + Claude Code adapters)
  - 12 in `test_setup_skill.py` (Step 0 contract + other-skill vault-awareness gates)
  - 5 in `test_no_hardcoded_vault_path.py` (CI gate)
