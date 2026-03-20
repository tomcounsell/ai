# Valor Name References Audit

All hardcoded references to "Valor", "valorengels", and related identifiers in the codebase.
Excludes `.claude/worktrees/` (duplicates of main repo) and `.git/`.

**Audit date**: 2026-03-17

---

## Category A: Persona (External-Facing Identity)

References where "Valor" is the **persona** that users interact with — the name seen in Telegram, the identity in conversations, the "who" that responds. These are what you'd change to deploy under a different persona name.

### A1. Routing & Mention Detection

| File | Line(s) | Reference | Notes |
|------|---------|-----------|-------|
| `bridge/routing.py` | 33-34 | `VALOR_USERNAMES = {"valor", "valorengels"}` | Controls who the bot responds to |
| `bridge/routing.py` | 171-196 | `get_valor_usernames()`, `is_valor_mentioned()`, `is_directed_to_other()` | Mention routing functions |
| `bridge/telegram_bridge.py` | 448 | `["@valor", "valor", "hey valor"]` | Default mention triggers |
| `config/projects.example.json` | 147 | `"mention_triggers": ["@valor", "valor", "hey valor"]` | Config example |
| `~/Desktop/Valor/projects.json` | 30 | Same mention triggers | Private config (iCloud-synced) |

### A2. Message Attribution & Conversation Identity

| File | Line(s) | Reference | Notes |
|------|---------|-----------|-------|
| `bridge/context.py` | 159 | `"Direct message to Valor"` | Context label in prompts |
| `bridge/context.py` | 287-406 | `sender == "Valor"` (6 occurrences) | Identifies our outbound messages |
| `bridge/catchup.py` | 214 | `"reply from us (Valor)"` | Comment |
| `bridge/telegram_bridge.py` | 1339, 1482 | `sender="Valor"` | Message attribution |
| `tools/telegram_history/__init__.py` | 150 | `sender.lower() == "valor"` | Direction detection |
| `scripts/migrate_sqlite_to_redis.py` | 73 | `sender.lower() == "valor"` | Legacy migration |
| `agent/branch_manager.py` | 548 | `"Reply to any Valor message"` | User-facing help text |

### A3. System Prompt & Persona Definition

| File | Line(s) | Reference | Notes |
|------|---------|-----------|-------|
| `config/SOUL.md` | 1, 7-10, 17 | `# Valor`, name/email/identity fields, `"I am Valor Engels"` | **The** persona definition |
| `agent/sdk_client.py` | 371, 405 | `"You are Valor, an AI coworker"` | Fallback system prompt |
| `agent/sdk_client.py` | 1123 | `project.get("name", "Valor")` | Default project name |
| `agent/sdk_client.py` | 586-591 | `"Valor's Claude Agent SDK wrapper"` | Docstrings |

### A4. Telegram Group Names

| File | Line(s) | Reference | Notes |
|------|---------|-----------|-------|
| `config/projects.example.json` | 9 | `"Dev: Valor"` group name | Telegram group |
| `config/SOUL.md` | 268-274 | `"Dev: Valor"` in CLI examples | Documentation |
| `tools/valor_telegram.py` | 6-11 | `"Dev: Valor"` in usage | CLI help text |
| `tools/telegram_history/cli.py` | 8-12 | `"Dev: Valor"` in usage | CLI help text |
| `tools/telegram_history/README.md` | 33-47 | `"Dev: Valor"` in examples | Documentation |

**Total Category A: ~35 references across ~15 files**
**Configurability effort: MEDIUM** — mostly reads from a few constants/config values. A `persona.name` config key + updating ~5 source-of-truth locations would cascade to most of these.

---

## Category B: Internal Branded Tooling

References where "Valor" is a **brand/product name** for the tooling itself — CLI binary names, package name, service labels, data directories. These are the "Valor platform" regardless of what persona is active.

### B1. Package & CLI Entry Points

| File | Line(s) | Reference | Notes |
|------|---------|-----------|-------|
| `pyproject.toml` | 2 | `name = "valor-bridge"` | Package name |
| `pyproject.toml` | 39-45 | `valor-history`, `valor-telegram`, `valor-calendar`, `valor-image-gen`, `valor-image-analyze`, `valor-search`, `valor-fetch` | 7 CLI binary names |

### B2. Tool Source Files (CLI branding)

| File | References | Notes |
|------|-----------|-------|
| `tools/valor_telegram.py` | Filename, `prog="valor-telegram"`, usage strings | CLI tool |
| `tools/valor_calendar.py` | Filename, `valor-calendar` in docstring/usage | CLI tool |
| `tools/telegram_history/cli.py` | `prog="valor-history"`, usage strings | CLI tool |
| `tools/web/__init__.py` | `valor-search`, `valor-fetch` in usage | CLI tool |
| `tools/image_gen/__init__.py` | `valor-image-gen`, `"X-Title": "Valor Image Gen"` | CLI + HTTP header |
| `tools/image_analysis/__init__.py` | `valor-image-analyze`, `"X-Title": "Valor Image Analysis"` | CLI + HTTP header |
| `tools/image_tagging/__init__.py` | `"X-Title": "Valor Image Tagging"` | HTTP header |
| `tools/__init__.py` | `"extend Valor's capabilities"` | Docstring |
| `tools/README.md` | `# Valor Tools` | Documentation |
| `tools/STANDARD.md` | `"Tools are capabilities that extend what Valor can do"` | Documentation |

### B3. Service Infrastructure (launchd)

| File | Service Labels |
|------|---------------|
| `com.valor.reflections.plist` | `com.valor.reflections` |
| `com.valor.issue-poller.plist` | `com.valor.issue-poller` |
| `scripts/valor-service.sh` | `com.valor.bridge`, `com.valor.update`, `com.valor.bridge-watchdog` |
| `scripts/update/service.py` | `com.valor.reflections`, `com.valor.daydream` (old), `com.valor.caffeinate` |
| `monitoring/bridge_watchdog.py` | `com.valor.bridge` |
| `scripts/install_reflections.sh` | `com.valor.reflections`, `com.valor.daydream` |
| `scripts/install_issue_poller.sh` | `com.valor.issue-poller` |
| `scripts/remote-update.sh` | `com.valor.reflections`, `com.valor.daydream` |

### B4. Data Paths

| Path | Used By |
|------|---------|
| `~/.valor/knowledge.db` | `tools/knowledge_search/__init__.py` |
| `~/.valor/test_results/` | `tools/test_scheduler/__init__.py` |
| `~/.valor/telegram_history.db` | `scripts/migrate_sqlite_to_redis.py` (legacy) |
| `data/valor.session` | `scripts/reflections.py` (Telegram session) |

### B5. Scripts (branded filenames & strings)

| File | References |
|------|-----------|
| `scripts/valor-service.sh` | Filename, `"Valor Bridge Service Manager"` |
| `scripts/auto-revert.sh` | `valor-service.sh` reference |
| `scripts/calendar_hook.sh` | `EXCLUDED_PROJECTS="valor"`, `valor-calendar` |
| `scripts/calendar_prompt_hook.sh` | `EXCLUDED_PROJECTS="valor"`, `valor-calendar` |
| `scripts/issue_poller.py` | `valor-telegram` CLI call |
| `scripts/update/verify.py` | `valor-calendar` path checks |
| `scripts/update/run.py` | `com.valor.reflections.plist` |
| `scripts/update/__init__.py` | `"Modular update system for Valor"` |
| `scripts/telegram_login.py` | `valor-service.sh` reference |

### B6. Agent/SDK (branded references)

| File | References |
|------|-----------|
| `agent/__init__.py` | `"Claude Agent SDK integration for Valor"` |
| `agent/job_queue.py` | `valor-calendar` CLI path resolution (6 references) |

### B7. Skills & Commands

| File | References |
|------|-----------|
| `.claude/skills/new-valor-skill/SKILL.md` | Skill name itself |
| `.claude/skills/*/SKILL.md` (multiple) | Reference "Valor" as the platform |
| `.claude/hooks/validators/validate_tool_structure.py` | Tool naming conventions |
| `.claude/hooks/validators/validate_claude_md_updated.py` | System references |
| `.claude/hooks/post_tool_use.py` | System references |
| `.claude/agents/notion.md` | Agent identity |
| `.claude/agents/agent-architect.md` | System references |
| `.claude/README.md` | System overview |
| `.claude/skills/README.md` | Skill system docs |

**Total Category B: ~80 references across ~40 files**
**Configurability effort: HIGH** — binary names, filenames, service labels, and data paths are deeply baked in. These would stay "Valor" even if the persona changed.

---

## Category C: Documentation & Tests

References in prose, test fixtures, and examples. Low runtime impact — these follow from whatever Categories A and B decide.

### C1. Project Documentation (112+ files)

- `docs/features/` — describe capabilities using "Valor" as subject
- `docs/plans/` — reference "Valor" in design context
- `docs/guides/` — include `valor-*` CLI examples
- `docs/deployment.md` — deployment instructions
- `docs/tools-reference.md` — tool documentation
- `docs/guides/valor-evolution-summary.md` — history
- `config/README.md` — `"Valor AI system"`, `"Valor's persona"`
- `CLAUDE.md` — project instructions reference Valor throughout

### C2. Tests (28 files)

| File | References |
|------|-----------|
| `tests/unit/test_bridge_logic.py` | Routing assertions with "valor" usernames |
| `tests/unit/test_valor_telegram.py` | CLI tool tests |
| `tests/unit/test_sdk_client.py` | Persona name assertions |
| `tests/unit/test_sdk_client_sdlc.py` | SDLC mode tests |
| `tests/unit/test_summarizer.py` | Summarizer tests |
| `tests/unit/test_pm_channels.py` | PM routing tests |
| `tests/conftest.py` | Fixture defaults |
| `tests/e2e/test_message_pipeline.py` | End-to-end flow tests |
| `tests/e2e/test_session_continuity.py` | Session tests |
| `tests/e2e/test_config_bootstrap.py` | Config tests |
| `tests/integration/test_message_routing.py` | Routing integration |
| `tests/integration/test_unthreaded_routing.py` | Unthreaded routing |
| `tests/tools/test_telegram_history.py` | History tool tests |
| + 15 more test files | Various assertions |

### C3. Configuration Examples & Defaults

| File | References |
|------|-----------|
| `config/projects.example.json` | `"valor"` project key, `"Valor AI"` name, `"VALOR"` team |
| `~/Desktop/Valor/projects.json` | Mention triggers (private, iCloud-synced) |
| `bridge/telegram_bridge.py` | 322: `"valor"` default for `ACTIVE_PROJECTS` |
| `bridge/session_transcript.py` | 57: `"valor"` in docstring example |
| `bridge/session_logs.py` | 81: `"valor"` in docstring example |
| `tools/job_scheduler.py` | 37: `DEFAULT_PROJECT_KEY = "valor"` |

**Total Category C: ~150+ references across ~145 files**
**Configurability effort: LOW** — these just follow from A and B. Update last.

---

## The Takeaway

If the goal is **"keep the Valor brand, but make the active persona configurable"**, only **Category A (~35 references in ~15 files)** needs to change. Categories B and C stay as-is.

The key insight: Category A could be driven by a single config value (e.g., `persona.name` in `~/Desktop/Valor/projects.json` or `SOUL.md` metadata) that flows through ~5 source-of-truth constants:

1. `bridge/routing.py` → `VALOR_USERNAMES` (read from config)
2. `bridge/telegram_bridge.py` → mention triggers (already partially configurable via `projects.json`)
3. `bridge/context.py` → sender attribution string
4. `agent/sdk_client.py` → fallback prompt name
5. `config/SOUL.md` → persona definition (already the canonical source)
