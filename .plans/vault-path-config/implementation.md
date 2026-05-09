# Vault Path Config — Implementation Plan

## Architecture

Single master env var `VALOR_VAULT_DIR` anchors all vault-relative paths via a Pydantic settings model. Existing per-path env vars (`GOOGLE_CREDENTIALS_DIR`, `PROJECTS_CONFIG_PATH`, `REFLECTIONS_YAML`) remain as overrides. Resolution cascade is `--vault-dir > VALOR_VAULT_DIR > ~/Desktop/Valor (default if .env present) > error`. The codebase ships only the env-var override; `~/Desktop/Valor` remains the established default. Custom locations (`~/.valor`, `~/Documents/Valor`, etc.) are user choices set via `VALOR_VAULT_DIR`, not codebase tiers.

A new `tools/install/prompt.py` shim provides harness-agnostic prompting (Claude Code `AskUserQuestion` or generic TTY) and is the only API the install/setup flow uses to gather user input. The `/setup` skill calls the shim through a Bash bridge that emits a JSON instruction the harness renders natively.

```
                    ┌──────────────────────────────────────┐
                    │ tools/install/prompt.py              │
                    │   ask_choice(question, options)      │
                    │   ask_input(question, default, ...)  │
                    └─────────────┬────────────────────────┘
                                  │ harness detection
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
        ┌─────────────────┐ ┌────────────┐ ┌──────────────────┐
        │ Claude Code     │ │ Generic    │ │ Headless / no-TTY│
        │ (AskUserQuestion│ │ TTY        │ │ raise            │
        │  via JSON       │ │ (readline) │ │ InstallPrompt    │
        │  instruction)   │ │            │ │ Unavailable      │
        └─────────────────┘ └────────────┘ └──────────────────┘
```

<!-- D-001 --> Cloud strategy is the user's concern, not Valor's.
<!-- D-002 --> Harness-agnostic via `tools/install/prompt.py`.
<!-- D-003 --> `--vault-dir` flag for automation; `VALOR_VAULT_DIR` env var.
<!-- D-004 --> Master env var is `VALOR_VAULT_DIR`.
<!-- D-005 --> Cascade: flag > env > default `~/Desktop/Valor` > error.
<!-- D-006 --> Migration is opt-in via `/setup --relocate-vault`.

### Key Constraints

| Constraint | Impact |
|---|---|
| Existing `~/Desktop/Valor/` users must keep working | Cascade falls back to `~/Desktop/Valor/` as the default when no env var is set (<!-- D-005 -->) |
| launchd services start with minimal env | `VALOR_VAULT_DIR` must be baked into plist `<EnvironmentVariables>` at install time (<!-- D-013 -->) |
| Repo `.env` is a symlink to `<vault>/.env` | The symlink encodes vault location for runtime; `VALOR_VAULT_DIR` is for processes that haven't loaded `.env` yet |
| No new dependencies | Stdlib + existing pydantic-settings |
| Per-path env vars cannot break | All three remain valid overrides; only the default cascade changes (<!-- D-012 -->) |

### Boundaries

| Module | Owns |
|---|---|
| `config/settings.py` (`VaultSettings`) | Path resolution, cascade order, validation |
| `tools/install/prompt.py` (new) | Harness detection, `ask_choice` / `ask_input`, `InstallPromptUnavailable` |
| `scripts/relocate_vault.py` (new) | One-shot migration: copy contents, repoint symlinks, update plists |
| `scripts/install_*.sh` | Inject `VALOR_VAULT_DIR` into generated plists |
| Skills (`.claude/skills-global/*/SKILL.md`) | Resolve vault path via `${VALOR_VAULT_DIR:-…}` shell expansion or via Python helper |
| `tests/unit/test_no_hardcoded_vault_path.py` (new) | CI gate: zero `~/Desktop/Valor` literals outside the allowlist |

`config/settings.py` adds module-level comments describing what `VaultSettings` owns (path resolution + cascade) and what it does NOT do (no I/O, no path creation — those belong to install scripts).

### Observability

- `logger.info` once per process at startup: "Vault directory resolved to: <path> (source: env / default_desktop / explicit)"
- `logger.error` on resolution failure: enumerate every cascade step that was tried and why it failed

---

## Phases

### Phase 1: Settings module + cascade

**Goal:** `config/settings.VaultSettings` resolves the vault path correctly across the cascade tiers. Pure logic, no integration yet.

#### M1: VaultSettings model

- **Dependencies:** none
- **Effort:** S (1-3d)
- **Tasks:**
  1. RED: `tests/unit/test_vault_settings.py::test_vault_dir_explicit_env_var` — `VALOR_VAULT_DIR=/tmp/foo` → `vault.dir == Path("/tmp/foo")`.
  2. GREEN: add `VaultSettings(BaseModel)` with `dir: Path` field, default factory reads env var; raises `VaultNotResolved` if not found and no fallback.
  3. RED: `test_vault_dir_default_desktop` — `~/Desktop/Valor/.env` exists, no env var → resolves to Desktop with `source == "default_desktop"`.
  4. GREEN: cascade tier 2.
  5. RED: `test_vault_dir_no_resolution_raises` — no env var, no `~/Desktop/Valor` → raises `VaultNotResolved` with message listing all attempted tiers.
  6. GREEN: cascade tier 3.
  7. RED: `test_vault_dir_validation_rejects_repo_subdir` — path inside the repo → raises `VaultPathInvalid("inside repo")`.
  8. GREEN: validator checks against repo root.
  9. RED: `test_vault_dir_validation_rejects_tmp` — path under `/tmp` or `/var/folders` → raises `VaultPathInvalid("ephemeral")`.
  10. GREEN: validator checks against ephemeral roots.

#### M2: Vault-relative properties

- **Dependencies:** M1
- **Effort:** S
- **Tasks:**
  1. RED: `test_vault_properties_return_correct_paths` — `vault.env_path`, `vault.projects_path`, `vault.personas_dir`, `vault.identity_path`, `vault.google_credentials_dir`, `vault.reflections_yaml` all derive from `vault.dir`.
  2. GREEN: add `@property` methods.
  3. RED: `test_per_path_env_var_overrides_master` — `VALOR_VAULT_DIR=/tmp/foo`, `GOOGLE_CREDENTIALS_DIR=/tmp/bar` → `vault.google_credentials_dir == Path("/tmp/bar")`.
  4. GREEN: each property checks its specific env var first, falls back to `dir`-relative.
  5. RED: `test_per_path_overrides_for_projects_path_and_reflections_yaml`
  6. GREEN: same pattern for `PROJECTS_CONFIG_PATH` and `REFLECTIONS_YAML`.

#### M3: Public singleton + import wiring

- **Dependencies:** M2
- **Effort:** S
- **Tasks:**
  1. RED: `test_vault_singleton_lazy_load` — `from config.settings import vault; vault.dir` works without crashing on a fresh import.
  2. GREEN: module-level `vault = VaultSettings()` factory.
  3. RED: `test_get_vault_dir_logs_resolution_source` — first access logs INFO "Vault directory resolved to ... (source: ...)".
  4. GREEN: cached resolution + one-shot logger.
  5. REFACTOR: replace any inline `Path.home() / "Desktop" / "Valor"` literal in `config/settings.py:158` with the new `vault` singleton.

### Gate 1→2

- [ ] `pytest tests/unit/test_vault_settings.py` passes
- [ ] `from config.settings import vault` import works in isolation
- [ ] No new dependencies in `pyproject.toml`

---

### Phase 2: Code-level integration

**Goal:** Every Python module that hardcodes `~/Desktop/Valor` reads from `vault.<property>` instead. Existing per-path env vars still honored.

#### M4: Bridge + agent code

- **Dependencies:** Phase 1
- **Effort:** M (3-7d)
- **Tasks:**
  1. RED: `test_routing_uses_vault_projects_path` — set `VALOR_VAULT_DIR`, drop a projects.json there, call `bridge/routing.py:_load_projects_config()`, assert it loads from the vault.
  2. GREEN: replace `~/Desktop/Valor/projects.json` literal in `bridge/routing.py:51-79` with `vault.projects_path`. Update docstring.
  3. RED: `test_reflections_utils_uses_vault_projects_path` — same for `reflections/utils.py:42-52`.
  4. GREEN: same edit.
  5. RED: `test_reflection_scheduler_uses_vault_reflections_yaml` — `agent/reflection_scheduler.py:50` resolves yaml via vault.
  6. GREEN: same edit (env var override still wins, see <!-- D-012 -->).
  7. Files to touch (replace literals + update docstrings/error messages):
     - `bridge/routing.py:52, 77, 79, 111, 125`
     - `bridge/telegram_bridge.py:45`
     - `agent/reflection_scheduler.py:44, 50, 128`
     - `agent/sdk_client.py` (any references)
     - `tools/google_workspace/auth.py:4, 31, 35, 38`
     - `tools/valor_calendar.py:27`
     - `reflections/utils.py:42, 52`
     - `worker/__main__.py` (any references)
     - `config/project_key_resolver.py:104`
  8. RED: `test_no_hardcoded_desktop_valor_in_python` — grep all `*.py` outside `tests/` and `.plans/` for `~/Desktop/Valor` literals; assert zero matches.
  9. GREEN: confirmed by completing the file edits.

#### M5: Shell scripts

- **Dependencies:** M4
- **Effort:** S
- **Tasks:**
  1. RED: `tests/integration/test_install_scripts_inject_vault_dir.py::test_install_worker_plist_contains_vault_dir` — run `install_worker.sh` against a fake home; assert generated plist has `<key>VALOR_VAULT_DIR</key>`.
  2. GREEN: edit `scripts/install_worker.sh` to inject `VALOR_VAULT_DIR` into the plist `EnvironmentVariables` dict (<!-- D-013 -->).
  3. Same for: `scripts/install_reflections.sh`, `scripts/install_autoexperiment.sh`, `scripts/install_nightly_tests.sh`.
  4. Edit shell scripts to use `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` shell expansion for any literal references:
     - `scripts/start_bridge.sh`
     - `scripts/valor-service.sh`
     - `scripts/calendar_hook.sh`
     - `scripts/calendar_prompt_hook.sh`
     - `scripts/remote-update.sh`
     - `scripts/update/env_sync.py`
     - `scripts/update/run.py`
     - `scripts/update/service.py`
     - `scripts/update/verify.py`
     - `scripts/migrate_model_relationships.py`
     - `scripts/reflections_report.py`
  5. RED: `test_no_hardcoded_desktop_valor_in_shell` — grep all `*.sh` for the literal; assert zero outside the allowlist.
  6. GREEN: complete the edits.

### Gate 2→3

- [ ] `pytest tests/unit/test_no_hardcoded_vault_path.py` passes for `*.py` and `*.sh`
- [ ] `python -m tools.doctor --quick` resolves vault path correctly with `VALOR_VAULT_DIR` set
- [ ] Existing install on Desktop continues to work (manual smoke: with `VALOR_VAULT_DIR` unset, ensure `~/Desktop/Valor/.env` is detected and `vault.source == "default_desktop"`)

---

### Phase 3: Prompt shim + setup flow

**Goal:** `/setup` interactively asks the user where to put the vault. Skill never embeds a harness-specific prompt.

#### M6: Prompt shim core

- **Dependencies:** none (parallel to Phase 1)
- **Effort:** S
- **Tasks:**
  1. RED: `tests/unit/test_install_prompt.py::test_ask_choice_via_tty` — TTY harness, mock readline, returns first matching choice.
  2. GREEN: `tools/install/prompt.py` with `ask_choice(question, options) -> str` — TTY adapter using `input()`.
  3. RED: `test_ask_input_via_tty_with_default` — empty input returns default; non-empty returns input; validator rejects invalid.
  4. GREEN: `ask_input(question, default=None, validator=None) -> str`.
  5. RED: `test_install_prompt_unavailable_when_no_tty` — non-TTY, no harness override → raises `InstallPromptUnavailable`.
  6. GREEN: detect `sys.stdin.isatty()` and `os.environ.get("VALOR_HARNESS")`.
  7. RED: `test_claude_code_harness_emits_json_instruction` — `VALOR_HARNESS=claude-code` → emits a JSON line on stdout the skill parses; raises `InstallPromptDeferred` so the calling Bash command exits and the skill runs the AskUserQuestion tool.
  8. GREEN: claude-code adapter — JSON instruction format documented in `tools/install/prompt.py` docstring.

#### M7: Setup skill rewrite — Step 0

- **Dependencies:** M6, M1
- **Effort:** M
- **Tasks:**
  1. Rewrite `.claude/skills-global/setup/SKILL.md` Step 0 (new) to call the shim:
     - Detect `VALOR_VAULT_DIR` and `--vault-dir` arg first
     - If neither, invoke `python -m tools.install.prompt vault-picker` which either runs TTY directly or emits the AskUserQuestion JSON instruction the skill renders
     - The picker shows five options with tradeoff blurbs (<!-- D-009 -->):
       1. `~/.valor/` — hidden home directory, no sync. Single-machine, no cloud backup.
       2. `~/Documents/Valor/` — Documents folder. Often iCloud-synced if iCloud Documents is on. Subject to iCloud Drive sync conflicts.
       3. `~/iCloud Drive/Valor/` — explicit iCloud Drive root. Synced but visible only via Finder's iCloud sidebar. Subject to file-on-demand evictions.
       4. `~/Desktop/Valor/` — original default. iCloud-synced via Desktop. Known iCloud rename bug if path is recreated.
       5. Custom path — free-form input (`ask_input`).
  2. Replace every `~/Desktop/Valor/...` literal in the skill's subsequent steps (3, 4.1, 4.2, 6, 8, 10, 11, troubleshooting) with `${VALOR_VAULT_DIR}/...`.
  3. RED: `tests/integration/test_setup_skill_step_zero.py` (synthetic — invoke the picker bash command in a subshell with `VALOR_HARNESS=tty`; assert it writes `VALOR_VAULT_DIR` into a temp `.env`).
  4. GREEN: skill changes pass the test.

#### M8: Other skill files

- **Dependencies:** M6
- **Effort:** S
- **Tasks:**
  1. Rewrite `~/Desktop/Valor` references in:
     - `.claude/skills-global/update/SKILL.md` (5 references)
     - `.claude/skills-global/do-deploy/SKILL.md`
     - `.claude/skills-global/do-pr-review/SKILL.md`
     - `.claude/agents/baseline-verifier.md` (2 references)
  2. Pattern: `${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}` for shell snippets; `python -c "from config.settings import vault; print(vault.<property>)"` for Python snippets.
  3. RED: `test_no_hardcoded_desktop_valor_in_skills` — grep `.claude/` for literal; zero matches outside the allowlist.
  4. GREEN: complete edits.

### Gate 3→4

- [ ] `/setup` invocation in TTY mode prompts and writes `VALOR_VAULT_DIR` to .env
- [ ] `/setup --vault-dir ~/.valor` skips the prompt and uses the path
- [ ] `/setup` invocation in Claude Code emits AskUserQuestion-formatted JSON

---

### Phase 4: Docs + CI gate

**Goal:** All user-facing docs reflect the new model. CI prevents regressions.

#### M9: Top-level docs

- **Dependencies:** Phase 1-3
- **Effort:** S
- **Tasks:**
  1. Rewrite vault references in:
     - `README.md`
     - `CLAUDE.md` (section "Secrets" and "Configuration Files")
     - `config/README.md`
     - `tools/google_workspace/README.md`
  2. Each adds a "Picking a vault location" subsection with the same five options + tradeoffs (<!-- D-009 -->). No recommendation.
  3. `.env.example`: add `VALOR_VAULT_DIR=` near the top with a comment block explaining the cascade and the launchd injection caveat (<!-- D-010 -->).

#### M10: Feature docs

- **Dependencies:** M9
- **Effort:** M (volume, not complexity)
- **Tasks:**
  1. Update vault references across `docs/features/*.md` and `docs/guides/*.md` (~50 files):
     - Replace literal `~/Desktop/Valor/` with `<vault>/` (variable indication)
     - Each file gains a one-line note "vault path is configured via `VALOR_VAULT_DIR`; see CLAUDE.md"
  2. RED: `test_no_hardcoded_desktop_valor_in_docs_features` — grep `docs/features/` and `docs/guides/` for literal; zero matches outside the allowlist.
  3. GREEN: complete edits.
  4. **Allowlist** (the test ignores these):
     - `.env.example` (illustrative comments)
     - `CHANGELOG.md`
     - `docs/postmortems/*` (historical)
     - `docs/plans/completed/*` (historical)
     - `docs/plans/critiques/*` (historical)
     - `.plans/zip-archive-ingestion/*` (older plan)
     - `.plans/vault-path-config/*` (this plan's RFC and implementation)

#### M11: CI gate test

- **Dependencies:** M10
- **Effort:** S
- **Tasks:**
  1. RED: `tests/unit/test_no_hardcoded_vault_path.py` (already partly built across M4/M5/M8/M10) — single comprehensive test that asserts zero `~/Desktop/Valor` literal in any `.py`, `.sh`, `.md`, `.json` file under the repo, except the explicit allowlist.
  2. GREEN: passes once M4/M5/M8/M9/M10 are done.
  3. Add the test to the default `pytest tests/unit/` invocation so it runs in CI by default.

### Gate 4→5

- [ ] `python -m pytest tests/unit/test_no_hardcoded_vault_path.py` passes
- [ ] All four top-level docs have the "Picking a vault location" subsection
- [ ] `.env.example` has the `VALOR_VAULT_DIR` placeholder

---

### Phase 5: Migration tool

**Goal:** Existing `~/Desktop/Valor/` users can opt into a relocation without manual file shuffling.

#### M12: relocate_vault.py

- **Dependencies:** Phase 1-2
- **Effort:** M
- **Tasks:**
  1. RED: `tests/unit/test_relocate_vault.py::test_dry_run_lists_files_no_writes` — `python scripts/relocate_vault.py --to /tmp/new --dry-run`; lists files; nothing on disk changes.
  2. GREEN: `scripts/relocate_vault.py` accepting `--from <path>` (default: detect), `--to <path>` (required), `--archive {delete,keep,rename}` (default: `rename`), `--dry-run`.
  3. RED: `test_relocate_copies_contents` — runs against a temp old vault; new path has same files, perms preserved, repo `.env` symlink repointed.
  4. GREEN: `shutil.copytree(...)` + symlink repoint via `os.replace`.
  5. RED: `test_relocate_updates_launchd_plists` — generated plists in `~/Library/LaunchAgents/com.valor.*.plist` get their `VALOR_VAULT_DIR` rewritten; services re-loaded.
  6. GREEN: `plistlib` rewrite + `launchctl bootout/bootstrap`.
  7. RED: `test_relocate_archive_modes` — `--archive rename` renames old to `Valor.legacy.<timestamp>`; `--archive delete` removes; `--archive keep` leaves untouched.
  8. GREEN: archive logic.
  9. RED: `test_relocate_aborts_if_target_nonempty` — non-empty `--to` path raises unless `--force`.
  10. GREEN: pre-flight check.

#### M13: /setup --relocate-vault wiring

- **Dependencies:** M12, M7
- **Effort:** S
- **Tasks:**
  1. Update `.claude/skills-global/setup/SKILL.md` to recognize the `--relocate-vault` flag.
  2. The skill invokes `python scripts/relocate_vault.py --to <new> --archive rename` after prompting for the new path via the shim.
  3. RED: `test_setup_relocate_vault_invokes_script` — synthetic skill test that asserts the relocate script is called with the right args.
  4. GREEN: skill changes pass.

### Gate 5→done

- [ ] `python scripts/relocate_vault.py --to /tmp/new --dry-run` reports planned actions, makes no writes
- [ ] Real relocate against a test vault round-trips successfully (manual smoke)
- [ ] `/setup --relocate-vault` end-to-end works

---

## Risk Register

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| launchd-managed processes (worker, watchdog, reflections) start with no `VALOR_VAULT_DIR` after upgrade | High | Medium | M5 bakes the var into plist generation; M12 rewrites existing plists during relocation. Document the regen requirement in CHANGELOG. |
| Cascade silently selects default `~/Desktop/Valor/` for users who *want* a different location | Medium | Low | M3 logs the resolution source loudly at every process startup; doc points to `--relocate-vault`. |
| Prompt shim hangs indefinitely waiting for input in non-TTY context | Medium | Medium | M6 raises `InstallPromptUnavailable` if no harness available; calling code must handle it. |
| Migration leaves stale references in user's persona overlay paths | Low | Medium | M12 grep-rewrites paths inside `<old>/projects.json` to point at `<new>/personas/`. |
| Doc-test allowlist drifts and starts ignoring real regressions | Low | Medium | Allowlist is a tiny list of stable file patterns (postmortems, completed plans). Reviewed at PR time. |
| iCloud rename bug strikes a user who picks Desktop in the picker | Low | Low | M2-M4 detection in `bridge/config_validation.py` (existing module) flags `Valor 2`, `Valor 3` siblings on Desktop. |
| Persona overlay paths inside `projects.json` stay hardcoded after relocation | Medium | Medium | M12 step rewrites them; users editing projects.json by hand need to rewrite manually (documented). |
| Existing per-path env var users see surprise behavior changes | Low | Low | Per-path env vars unchanged in semantics; cascade only affects the default fallback (<!-- D-012 -->). |
| Tests that mock `Path.home() / "Desktop" / "Valor"` break | Medium | High | Coordinate test rewrite in M4-M11; the allowlist covers historical plan files. |

---

## No-Gos

- **No changes to required keys, API keys, secrets, auth flows, or auth behavior.** This plan is exclusively about the *location* of the config/vault directory. Files like `google_credentials.json`, `google_token.json`, `.env`, and any API key handling stay semantically identical — they just live in a configurable directory. No new keys required, no existing keys removed, no OAuth flow rewrites, no permission scope changes. <!-- D-017 -->
- **No new dependencies.** Stdlib + existing pydantic/dotenv only.
- **No silent migration.** Existing installs keep working as-is until the user opts in (<!-- D-006 -->).
- **No shipped cloud default.** Picker shows tradeoffs; no recommendation (<!-- D-001 -->).
- **No deletion of existing per-path env vars.** They remain valid overrides indefinitely (<!-- D-012 -->).
- **No deletion of `~/Desktop/Valor/` during relocation unless user explicitly chose `--archive delete`.**
- **No `bcu`-style opt-in sentinel for vault location.** This is a config concern, not a permission grant.
- **No per-machine default beyond `~/Desktop/Valor`.** All other paths require explicit `VALOR_VAULT_DIR` / `--vault-dir` / picker selection.
- **No support for non-Claude-Code, non-TTY harnesses in v1.** Adapters can be added later.

---

## Update System

The `/update` skill is affected:
- It reads `~/Desktop/Valor/projects.json` directly (lines 30, 117, 123, 126 of update SKILL.md). M8 replaces these with vault-aware resolution.
- `scripts/update/env_sync.py` and `scripts/update/run.py` write/read vault paths. M5 updates them.
- `scripts/update/run.py:check_python_alias()` is unchanged (Python alias is not vault-related).
- Updated installs need launchd plist regeneration to pick up the injected `VALOR_VAULT_DIR`. The update flow runs `install_worker.sh` etc. on every invocation, so this happens automatically — but the first post-upgrade run must succeed for older installs without the var. Tested explicitly in M5.

---

## Agent Integration

No new CLI entry point. `/setup` and `/setup --relocate-vault` route through the existing setup skill, which now uses `tools/install/prompt.py`. The prompt shim is invoked via Bash:

```bash
python -m tools.install.prompt vault-picker
```

The bash command emits either a result on stdout (TTY mode) or a JSON instruction (Claude Code mode) that the skill parses and renders via `AskUserQuestion`. No new top-level CLI in `pyproject.toml [project.scripts]`.

`scripts/relocate_vault.py` is invoked directly (`python scripts/relocate_vault.py ...`). It does not need a `[project.scripts]` entry — it's an install-time tool, not an agent-callable one.

---

## Failure Path Test Strategy

Every cascade tier gets a dedicated test:
1. `test_vault_dir_explicit_env_var` — tier 1 (`VALOR_VAULT_DIR`)
2. `test_vault_dir_default_desktop` — tier 2 (`~/Desktop/Valor/.env` fallback, `source=default_desktop`)
3. `test_vault_dir_no_resolution_raises` — tier 3 (asserts message lists all attempted tiers)

Validation rejections each get their own test (`_rejects_repo_subdir`, `_rejects_tmp`).

`InstallPromptUnavailable`: tested by stripping `sys.stdin.isatty()` and `VALOR_HARNESS`.

Migration failures: `test_relocate_aborts_if_target_nonempty`, `test_relocate_dry_run_no_writes`.

Crash safety: `relocate_vault.py` writes to a staging dir first, then atomic rename (`os.replace`) at the end. If the script dies mid-copy, the original location is untouched.

---

## Test Impact

- [ ] `tests/unit/test_vault_settings.py` — NEW. M1-M3 coverage.
- [ ] `tests/unit/test_install_prompt.py` — NEW. M6 coverage.
- [ ] `tests/unit/test_relocate_vault.py` — NEW. M12 coverage.
- [ ] `tests/unit/test_no_hardcoded_vault_path.py` — NEW. M4/M5/M8/M11 grep gate.
- [ ] `tests/integration/test_install_scripts_inject_vault_dir.py` — NEW. M5 plist injection.
- [ ] `tests/integration/test_setup_skill_step_zero.py` — NEW. M7 picker invocation.
- [ ] Existing tests that import `Path.home() / "Desktop" / "Valor"` directly (audit at start of M4) — UPDATE to import `from config.settings import vault` and use `vault.dir`.
- [ ] `tests/unit/test_settings.py` (if it exists) — UPDATE to cover `VaultSettings`.
- [ ] `tests/unit/test_routing.py` — UPDATE if it asserts the projects.json path literal.
- [ ] `tests/integration/test_telegram_bridge_smoke.py` — UPDATE if it presupposes Desktop.

---

## Rabbit Holes

Avoid during build:

- **Building a generic "harness adapter SDK".** Two adapters (Claude Code, TTY) is enough for v1. Don't generalize prematurely.
- **Auto-detecting iCloud sync state** via filesystem heuristics. Brittle and macOS-specific. The picker explains the tradeoff; user decides.
- **Cleaning up `Valor 2`, `Valor 3` siblings on Desktop**. Detection only. Cleanup is the user's call.
- **Encrypting the vault.** Separate concern, separate plan.
- **Per-machine default vault paths via projects.json.** Vault is a per-machine concern; projects.json is a multi-machine concern. Don't conflate.
- **Migrating from non-Valor secret managers (1Password, Bitwarden).** Out of scope; users with those tools wire them manually.
- **Auto-archiving old vault on relocation by default.** `--archive rename` is the default; user explicitly chooses delete.
- **Writing a generic `ask_form(fields)` API.** YAGNI. Two functions (`ask_choice`, `ask_input`) cover the picker.

---

## Documentation

- [ ] `README.md` — replace "secrets live in `~/Desktop/Valor/.env`" with "secrets live in your configured vault directory (default: `~/Desktop/Valor/`; override via `VALOR_VAULT_DIR` or pick during `/setup`)". Add link to "Picking a vault location" in CLAUDE.md.
- [ ] `CLAUDE.md` — replace the Secrets section. Add new "Picking a vault location" subsection with the five-option tradeoff table.
- [ ] `config/README.md` — same treatment.
- [ ] `tools/google_workspace/README.md` — same.
- [ ] `.env.example` — add `VALOR_VAULT_DIR=` at the top with a multi-line comment explaining the cascade.
- [ ] `docs/features/vault-path.md` (NEW) — detailed feature doc covering: cascade order, prompt shim, relocate flow, allowlist for the docs-test, launchd injection.
- [ ] `docs/features/README.md` — add entry.
- [ ] `CHANGELOG.md` — entry: "Configurable vault path via VALOR_VAULT_DIR; default ~/Desktop/Valor/ preserved when env var is unset; opt-in relocation via /setup --relocate-vault."

---

## Verification

| Verification | Command / Method | Pass criterion |
|---|---|---|
| Unit tests pass | `python -m pytest tests/unit/test_vault_settings.py tests/unit/test_install_prompt.py tests/unit/test_relocate_vault.py tests/unit/test_no_hardcoded_vault_path.py -n0 -v` | Exit 0 |
| Integration tests pass | `python -m pytest tests/integration/test_install_scripts_inject_vault_dir.py tests/integration/test_setup_skill_step_zero.py -n0 -v` | Exit 0 |
| Lint clean | `python -m ruff check . && python -m ruff format --check .` | Exit 0 |
| `vault.dir` resolves with env var | `VALOR_VAULT_DIR=/tmp/x python -c "from config.settings import vault; print(vault.dir)"` | Prints `/tmp/x` |
| `vault.dir` resolves via default fallback | `unset VALOR_VAULT_DIR; touch ~/Desktop/Valor/.env; python -c "..."` | Prints `~/Desktop/Valor` with `source=default_desktop` |
| `vault.dir` raises with no resolution | `unset VALOR_VAULT_DIR; rm ~/Desktop/Valor/.env; python -c "..."` | Exits non-zero with `VaultNotResolved` |
| Setup picker (TTY) works | `VALOR_HARNESS=tty python -m tools.install.prompt vault-picker` | Prompts with five options; honors choice |
| Setup picker (Claude Code) works | `VALOR_HARNESS=claude-code python -m tools.install.prompt vault-picker` | Emits JSON instruction on stdout |
| `/setup --vault-dir ~/.valor` automation | invoke from shell | No prompt, vault created at given path |
| Relocate dry-run | `python scripts/relocate_vault.py --to /tmp/new --dry-run` | Lists files, exits 0, no writes |
| Relocate round-trip | end-to-end test with synthetic vault | Files copied, symlinks repointed, plists rewritten |
| Hardcoded-path gate | `python -m pytest tests/unit/test_no_hardcoded_vault_path.py` | Exit 0 |
| Existing Desktop install boots | `unset VALOR_VAULT_DIR; ~/Desktop/Valor/.env exists; ./scripts/valor-service.sh start` | Bridge starts, log shows "Vault directory resolved to ~/Desktop/Valor (source: default_desktop)" |
| Launchd plist injection | `cat ~/Library/LaunchAgents/com.valor.worker.plist | grep VALOR_VAULT_DIR` | Match present |

---

## Escape Hatches

1. **If the prompt shim's harness detection is unreliable**: ship TTY-only for v1; the Claude-Code adapter becomes a follow-up plan. The skill falls back to running the picker as a Bash subprocess.
2. **If launchd injection breaks an existing user's setup**: the cascade's default fallback (`~/Desktop/Valor/`) still resolves correctly without `VALOR_VAULT_DIR` in the plist, so the worst case is degraded logging — service still starts.
3. **If relocate_vault.py corrupts a user's vault during testing**: the script's `--archive rename` default leaves the original intact; users can manually move files back.
4. **If `~/Desktop/Valor/` audit ends up too tedious to fully clean**: ship Phase 1-3 + CI gate; Phase 4 docs can be staged across multiple PRs without blocking the runtime change.

---

## Validation Commands

```bash
# Unit tests
python -m pytest tests/unit/test_vault_settings.py tests/unit/test_install_prompt.py \
  tests/unit/test_relocate_vault.py tests/unit/test_no_hardcoded_vault_path.py -n0 -v

# Integration tests
python -m pytest tests/integration/test_install_scripts_inject_vault_dir.py \
  tests/integration/test_setup_skill_step_zero.py -n0 -v

# Lint + format
python -m ruff check . && python -m ruff format --check .

# Doctor
VALOR_VAULT_DIR=~/.valor python -m tools.doctor --quick

# Manual smoke
./scripts/valor-service.sh restart
tail -20 logs/bridge.log | grep "Vault directory resolved"
```

---

## Decisions

Canonical decisions are in `.plans/vault-path-config/plan.db`. Query with:

```bash
npx tsx ~/dev/skills/planner/scripts/plan-db.ts query-decisions --plan vault-path-config
```

| Code | Topic |
|---|---|
| D-001 | Cloud strategy is user's concern; no shipped default |
| D-002 | Harness-agnostic prompt shim at `tools/install/prompt.py` |
| D-003 | `--vault-dir` flag for automation |
| D-004 | Master env var: `VALOR_VAULT_DIR` |
| D-005 | Cascade: flag > env > default `~/Desktop/Valor` > error |
| D-006 | Migration is opt-in via `/setup --relocate-vault` |
| D-007 | Prompt shim lives in repo, not external package |
| D-008 | Shim API: `ask_choice` + `ask_input` |
| D-009 | Picker shows tradeoffs, no recommendation |
| D-010 | `VALOR_VAULT_DIR` works in both `.env` and OS env, with launchd caveat |
| D-011 | `relocate_vault.py` is a standalone script |
| D-012 | Existing per-path env vars kept indefinitely |
| D-013 | Launchd plists bake `VALOR_VAULT_DIR` at install time |
| D-014 | Implementation includes a complete hardcoded-path audit list |
| D-015 | CI gate: `tests/unit/test_no_hardcoded_vault_path.py` with explicit allowlist |
| D-016 | Bootstrap: `--vault-dir` is the entry-point bootstrap before any `.env` exists |
| D-017 | Auth and key handling are out of scope — this plan moves files only, never changes auth contracts |
