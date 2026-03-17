# Valor Name References Audit

All hardcoded references to "Valor", "valorengels", and related identifiers in the codebase.
Excludes `.claude/worktrees/` (duplicates of main repo) and `.git/`.

**Audit date**: 2026-03-17

---

## 1. Identity & Routing (Critical - Core Behavior)

| File | Line(s) | Reference | Purpose |
|------|---------|-----------|---------|
| `bridge/routing.py` | 33-34 | `VALOR_USERNAMES = {"valor", "valorengels"}` | @mention detection set |
| `bridge/routing.py` | 171-196 | `get_valor_usernames()`, `is_valor_mentioned()`, `is_directed_to_other()` | Mention routing functions |
| `bridge/telegram_bridge.py` | 448 | `["@valor", "valor", "hey valor"]` | Default mention triggers |
| `bridge/telegram_bridge.py` | 322 | `"valor"` default for `ACTIVE_PROJECTS` | Default project key |
| `bridge/context.py` | 159 | `"Direct message to Valor"` | Context annotation |
| `bridge/context.py` | 287-406 | `sender == "Valor"` (multiple) | Message attribution |
| `bridge/catchup.py` | 214 | `"reply from us (Valor)"` | Comment |
| `bridge/session_transcript.py` | 57 | `"valor"` in docstring | Example project key |
| `bridge/session_logs.py` | 81 | `"valor"` in docstring | Example project key |
| `agent/sdk_client.py` | 371,405 | `"You are Valor, an AI coworker"` | Fallback system prompt |
| `agent/sdk_client.py` | 1123 | `project.get("name", "Valor")` | Default project name |
| `agent/branch_manager.py` | 548 | `"Reply to any Valor message"` | User-facing message |

## 2. Persona Definition (SOUL.md)

| File | Line(s) | Reference | Purpose |
|------|---------|-----------|---------|
| `config/SOUL.md` | 1 | `# Valor` | Title |
| `config/SOUL.md` | 7-10 | Full name, email, Google Workspace | Identity fields |
| `config/SOUL.md` | 17 | `"I am Valor Engels"` | First-person intro |
| `config/SOUL.md` | 268-280 | `valor-telegram` CLI examples | Tool usage docs |
| `config/SOUL.md` | 326-334 | `valor-service.sh` references | Service management docs |
| `config/SOUL.md` | 360 | `com.valor.issue-poller` | Launchd service ref |

## 3. CLI Tool Names (pyproject.toml entry points)

| File | Line(s) | Reference | Binary Name |
|------|---------|-----------|-------------|
| `pyproject.toml` | 2 | `name = "valor-bridge"` | Package name |
| `pyproject.toml` | 39 | `valor-history` | CLI entrypoint |
| `pyproject.toml` | 40 | `valor-telegram` | CLI entrypoint |
| `pyproject.toml` | 41 | `valor-calendar` | CLI entrypoint |
| `pyproject.toml` | 42 | `valor-image-gen` | CLI entrypoint |
| `pyproject.toml` | 43 | `valor-image-analyze` | CLI entrypoint |
| `pyproject.toml` | 44 | `valor-search` | CLI entrypoint |
| `pyproject.toml` | 45 | `valor-fetch` | CLI entrypoint |

## 4. Tool Source Files (internal references)

| File | References |
|------|-----------|
| `tools/valor_telegram.py` | Filename, CLI usage strings, `prog="valor-telegram"` |
| `tools/valor_calendar.py` | Filename, `valor-calendar` in usage/docstrings |
| `tools/telegram_history/cli.py` | `valor-history` in usage strings, `prog="valor-history"` |
| `tools/web/__init__.py` | `valor-search`, `valor-fetch` in usage strings |
| `tools/image_gen/__init__.py` | `valor-image-gen`, `"X-Title": "Valor Image Gen"` |
| `tools/image_analysis/__init__.py` | `valor-image-analyze`, `"X-Title": "Valor Image Analysis"` |
| `tools/image_tagging/__init__.py` | `"X-Title": "Valor Image Tagging"` |
| `tools/knowledge_search/__init__.py` | `~/.valor/knowledge.db` default path |
| `tools/test_scheduler/__init__.py` | `~/.valor/test_results/` default path |
| `tools/job_scheduler.py` | `DEFAULT_PROJECT_KEY = "valor"` |
| `tools/__init__.py` | `"extend Valor's capabilities"` docstring |
| `tools/telegram_history/__init__.py` | `sender.lower() == "valor"` direction check |

## 5. Service Names (launchd plists)

| File | Service Label |
|------|--------------|
| `com.valor.reflections.plist` | `com.valor.reflections` |
| `com.valor.issue-poller.plist` | `com.valor.issue-poller` |
| `scripts/valor-service.sh` | `com.valor.bridge`, `com.valor.update`, `com.valor.bridge-watchdog` |
| `scripts/update/service.py` | `com.valor.reflections`, `com.valor.daydream` (old), `com.valor.caffeinate` |
| `monitoring/bridge_watchdog.py` | `com.valor.bridge` |

## 6. Scripts

| File | References |
|------|-----------|
| `scripts/valor-service.sh` | Filename, `"Valor Bridge Service Manager"`, all `com.valor.*` labels |
| `scripts/install_reflections.sh` | `com.valor.reflections`, `com.valor.daydream` (migration) |
| `scripts/install_issue_poller.sh` | `com.valor.issue-poller` |
| `scripts/remote-update.sh` | `com.valor.reflections`, `com.valor.daydream` |
| `scripts/auto-revert.sh` | `valor-service.sh` reference |
| `scripts/calendar_hook.sh` | `EXCLUDED_PROJECTS="valor"`, `valor-calendar` |
| `scripts/calendar_prompt_hook.sh` | `EXCLUDED_PROJECTS="valor"`, `valor-calendar` |
| `scripts/reflections.py` | `valor.session` file path |
| `scripts/issue_poller.py` | `valor-telegram` CLI call |
| `scripts/migrate_sqlite_to_redis.py` | `~/.valor/`, `sender.lower() == "valor"` |
| `scripts/update/verify.py` | `valor-calendar` path checks |
| `scripts/update/run.py` | `com.valor.reflections.plist` check |
| `scripts/update/service.py` | Multiple `com.valor.*` labels |
| `scripts/update/__init__.py` | `"Modular update system for Valor"` |

## 7. Agent/SDK

| File | References |
|------|-----------|
| `agent/__init__.py` | `"Claude Agent SDK integration for Valor"` |
| `agent/sdk_client.py` | `"Valor"` as persona name, fallback prompt, project defaults |
| `agent/job_queue.py` | `valor-calendar` CLI path resolution |
| `agent/branch_manager.py` | `"Reply to any Valor message"` |

## 8. Configuration

| File | References |
|------|-----------|
| `config/projects.example.json` | `"valor"` project key, `"Valor AI"` name, `"Dev: Valor"` group, `"VALOR"` team |
| `config/projects.json.example` | `"@valor"`, `"valor"`, `"hey valor"` mention triggers |
| `config/README.md` | `"Valor AI system"`, `"Valor's persona"`, `valor-service.sh` |

## 9. Data Paths

| Location | Used By |
|----------|---------|
| `~/.valor/knowledge.db` | Knowledge search tool |
| `~/.valor/test_results/` | Test scheduler |
| `~/.valor/telegram_history.db` | Migration script (legacy) |
| `data/valor.session` | Telegram session file |

## 10. Documentation (112 files in `docs/`)

Too numerous to list individually. Nearly every doc references "Valor" as the system/persona name. Key categories:
- Feature docs (`docs/features/`) — describe Valor's capabilities
- Plan docs (`docs/plans/`) — reference Valor in context
- Guide docs (`docs/guides/`) — include `valor-*` CLI examples
- `docs/deployment.md` — deployment instructions reference Valor services

## 11. Skills & Commands (`.claude/`)

| File | References |
|------|-----------|
| `.claude/skills/new-valor-skill/SKILL.md` | Skill name itself |
| `.claude/skills/*/SKILL.md` (multiple) | Reference Valor as the agent/system |
| `.claude/hooks/validators/validate_tool_structure.py` | Valor tool naming conventions |
| `.claude/hooks/validators/validate_claude_md_updated.py` | Valor system references |
| `.claude/hooks/post_tool_use.py` | Valor references |
| `.claude/agents/notion.md` | Valor as the agent identity |
| `.claude/agents/agent-architect.md` | Valor system references |
| `.claude/README.md` | Valor system overview |
| `.claude/skills/README.md` | Valor skill system |
| `.claude/commands/queue-status.md` | Valor queue references |

## 12. Tests (28 files)

Test files reference "Valor" in assertions, fixtures, and test data. Key files:
- `tests/unit/test_bridge_logic.py` — routing assertions with "valor" usernames
- `tests/unit/test_valor_telegram.py` — CLI tool tests
- `tests/unit/test_sdk_client.py` — persona name assertions
- `tests/conftest.py` — fixture defaults
- `tests/e2e/test_message_pipeline.py` — end-to-end flow tests

---

## Summary Statistics

| Category | File Count | Coupling Level |
|----------|-----------|----------------|
| Identity & Routing | 7 | **Critical** — defines who responds to what |
| Persona (SOUL.md) | 1 | **Critical** — entire persona definition |
| CLI Entry Points | 1 (7 binaries) | **High** — user-facing tool names |
| Tool Source Files | 12 | **High** — embedded in help text, HTTP headers |
| Service Names | 5 | **High** — launchd labels, watchdog |
| Scripts | 13 | **Medium** — operational scripts |
| Agent/SDK | 4 | **High** — system prompt, defaults |
| Configuration | 3 | **High** — project definitions |
| Data Paths | 4 | **Medium** — `~/.valor/` directory |
| Documentation | 112+ | **Low** — prose references |
| Skills/Commands | 10+ | **Medium** — skill definitions |
| Tests | 28 | **Medium** — assertions and fixtures |
