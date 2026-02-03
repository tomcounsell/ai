# Update & Restart

Pull the latest changes from the remote repository and restart the bridge service.

## Steps

1. **Pull latest changes**
   ```bash
   cd /Users/valorengels/src/ai && git pull --ff-only
   ```
   - If the pull fails due to local changes, stash them first with `git stash`, pull, then `git stash pop`.
   - If the pull fails due to diverged branches, stop and inform the user. DO NOT force-pull.

2. **Check for pending critical dependency upgrades**

   The cron (`remote-update.sh`) defers critical dep upgrades to manual `/update` runs.
   Critical deps are pinned with `==` in pyproject.toml: telethon, anthropic, claude-agent-sdk.

   ```bash
   cd /Users/valorengels/src/ai
   if [ -f data/upgrade-pending ]; then
       echo "Critical dependency upgrade pending since:"
       cat data/upgrade-pending
   fi
   ```

   If the flag exists:
   - Show the user what critical deps changed: `git log --oneline -5 -- pyproject.toml`
   - Inform the user which critical versions are changing before syncing
   - After syncing (next step), verify the bridge starts and connects
   - Run a quick health check: `sleep 3 && tail -5 logs/bridge.log` (look for "Connected to Telegram")
   - If healthy: remove the flag (`rm data/upgrade-pending`)
   - If unhealthy: warn user, do NOT remove flag, suggest rollback with `git revert`

3. **Sync dependencies with uv**

   Always use `uv` for package management (faster, more reliable than pip).

   ```bash
   cd /Users/valorengels/src/ai

   # Ensure uv is installed
   if ! command -v uv &> /dev/null; then
     echo "Installing uv package manager..."
     curl -LsSf https://astral.sh/uv/install.sh | sh
     export PATH="$HOME/.local/bin:$PATH"
   fi

   # Sync all dependencies including dev tools (only reinstalls if changed)
   uv sync --all-extras

   # Install the package in editable mode
   uv pip install -e .
   ```

   This will:
   - Create/update `.venv/` if needed
   - Install all dependencies from `pyproject.toml`
   - Install dev tools (pytest, ruff, mypy)
   - Install CLI tools (`valor-calendar`, `valor-history`)
   - Be significantly faster than pip

4. **Verify critical dependency versions**

   Critical deps are pinned to exact versions in `pyproject.toml`. Verify installed versions match:

   ```bash
   cd /Users/valorengels/src/ai
   .venv/bin/python -c "
   import telethon, anthropic, claude_agent_sdk
   print(f'telethon=={telethon.__version__}')
   print(f'anthropic=={anthropic.__version__}')
   print(f'claude-agent-sdk=={claude_agent_sdk.__version__}')
   "
   ```

   Compare output against the `==` pins in `pyproject.toml`. If any version doesn't match, run `uv sync --all-extras --reinstall` and re-check. Report the versions to the user.

5. **Ensure Ollama summarizer model is available**

   The bridge uses a local Ollama model as fallback for response summarization when Haiku is unavailable.

   ```bash
   # Check if Ollama is running
   ollama list 2>/dev/null
   ```

   - If Ollama is installed, pull the summarizer model (small, ~3GB):
     ```bash
     ollama pull qwen3:4b
     ```
   - The model name can be overridden via `OLLAMA_SUMMARIZER_MODEL` in `.env`.
   - If Ollama is not installed, skip this step — the bridge will use Haiku only and fall back to truncation if Haiku fails.

6. **Verify SDK authentication**

   The SDK uses Max subscription OAuth via the Claude Desktop app. Verify authentication is configured:

   ```bash
   cd /Users/valorengels/src/ai

   # Check if Claude Desktop app is running (provides OAuth)
   if pgrep -f "Claude.app" > /dev/null; then
     echo "✅ Claude Desktop running (provides subscription auth)"
   else
     echo "⚠️  Claude Desktop not running - will use API key fallback"
   fi

   # Check auth mode configuration
   if grep -q 'USE_API_BILLING=true' .env 2>/dev/null; then
     echo "Auth mode: API key billing (forced via USE_API_BILLING=true)"
   else
     echo "Auth mode: Subscription OAuth (via Claude Desktop) with API key fallback"
   fi

   # Verify API key exists as fallback
   if grep -q 'ANTHROPIC_API_KEY=sk-ant-' .env 2>/dev/null; then
     echo "✅ API key configured (fallback)"
   else
     echo "⚠️  No API key - bridge requires either Desktop app or API key"
   fi
   ```

   **Authentication hierarchy:**
   1. Claude Desktop app (if running and `USE_API_BILLING` != true) → Subscription OAuth
   2. API key from `.env` → API billing fallback

   To force API billing, set `USE_API_BILLING=true` in `.env`

7. **Install/restart the bridge service and update cron**

   Use `install` instead of `restart` — it's idempotent and ensures both the bridge plist and the 12-hour update cron plist are present. This catches new machines or machines that were set up before the cron existed.

   ```bash
   /Users/valorengels/src/ai/scripts/valor-service.sh install
   ```

8. **Install caffeinate service (prevent sleep)**

   The bridge runs SDK queries that can take several minutes. If the machine sleeps mid-query, the subprocess dies and the response is lost. Install a persistent caffeinate service to prevent this:

   ```bash
   PLIST_PATH="$HOME/Library/LaunchAgents/com.valor.caffeinate.plist"
   if [ ! -f "$PLIST_PATH" ]; then
       cat > "$PLIST_PATH" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.valor.caffeinate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>-s</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF
       launchctl load "$PLIST_PATH"
       echo "✅ Caffeinate service installed"
   else
       echo "✅ Caffeinate service already installed"
   fi
   ```

   Verify it's running:
   ```bash
   launchctl list | grep com.valor.caffeinate && pgrep caffeinate > /dev/null && echo "✅ Caffeinate running" || echo "⚠️ Caffeinate NOT running"
   ```

9. **Verify the service is running**
   ```bash
   sleep 2 && /Users/valorengels/src/ai/scripts/valor-service.sh status
   ```

   Also verify the update cron is loaded:
   ```bash
   launchctl list | grep com.valor.update && echo "✅ Update cron installed" || echo "⚠️ Update cron NOT installed"
   ```

10. **Verify CLI tools are available**

   Run each check and report pass/fail. Group results by category.

   **System tools:**
   ```bash
   claude --version          # Claude Code CLI
   gh --version              # GitHub CLI
   git --version             # Git
   uv --version              # uv package manager
   ```

   **Python environment:**
   ```bash
   .venv/bin/python --version
   .venv/bin/python -c "import telethon; import httpx; import dotenv; import anthropic; import ollama; import google_auth_oauthlib; print('Core Python deps OK')"
   ```

   **Development tools:**
   ```bash
   .venv/bin/pytest --version
   .venv/bin/ruff --version
   .venv/bin/mypy --version
   ```

   **Valor CLI tools:**
   ```bash
   # SMS reader - reads macOS Messages for 2FA codes etc.
   .venv/bin/python -m tools.sms_reader.cli recent --limit 1

   # Calendar time tracking (check both venv and user bin locations)
   .venv/bin/valor-calendar --version 2>/dev/null || \
   "$HOME/Library/Python/3.12/bin/valor-calendar" --version 2>/dev/null || \
   echo "FAIL: valor-calendar not found"
   ```

   - If any tool is missing, attempt to install it:
     - Python packages: `uv pip install <package>`
     - System tools: `brew install <tool>` (if on macOS)
   - Report which tools passed and which failed.

11. **Generate Google Calendar config**

   Auto-generate `~/Desktop/claude_code/calendar_config.json` by matching Google Calendar names to projects.

   **Calendar mapping rules:**
   - `"dm"` → `"primary"` (DMs go to the user's main calendar)
   - `"default"` → calendar named **"Internal Projects"** (catch-all for Claude Code sessions and internal projects)
   - Project-specific: match each project's Telegram group name (minus the `"Dev: "` prefix) to a Google Calendar with the same name
   - Projects without a matching calendar automatically use `"default"` (no separate calendar needed for internal projects)

   ```bash
   .venv/bin/python -c "
   import sys, json, os
   sys.path.insert(0, '/Users/valorengels/src/ai')
   from pathlib import Path
   from dotenv import load_dotenv

   base_dir = Path.home() / 'Desktop/claude_code'
   config_path = base_dir / 'calendar_config.json'
   token_path = base_dir / 'google_token.json'

   # Check token exists
   if not token_path.exists():
       print('FAIL: No OAuth token at', token_path)
       print('Run /setup to configure Google Calendar OAuth')
       sys.exit(0)
   print('OK: OAuth token exists')

   # Connect to Calendar API
   try:
       from tools.google_workspace.auth import get_service
       service = get_service('calendar', 'v3')
       result = service.calendarList().list().execute()
       print('OK: Calendar API connected')
   except Exception as e:
       print(f'FAIL: Calendar API auth failed: {e}')
       sys.exit(0)

   # Build name->id map from all available Google Calendars
   gcal_by_name = {}
   for cal in result.get('items', []):
       gcal_by_name[cal['summary']] = cal['id']
       print(f'  Found calendar: {cal[\"summary\"]}')

   # Start building config
   calendars = {'dm': 'primary'}
   print()
   print('Mapping dm -> primary (user main calendar)')

   # Map 'default' to 'Internal Projects' calendar
   if 'Internal Projects' in gcal_by_name:
       calendars['default'] = gcal_by_name['Internal Projects']
       print(f'Mapping default -> Internal Projects')
   else:
       print('WARN: No calendar named \"Internal Projects\" found — default will fall back to primary')

   # Load projects config
   projects_path = Path('/Users/valorengels/src/ai/config/projects.json')
   if projects_path.exists():
       projects = json.loads(projects_path.read_text()).get('projects', {})
   else:
       projects = {}
       print('WARN: No projects.json found')

   # Match each project to a calendar by Telegram group name (minus 'Dev: ' prefix)
   load_dotenv(Path('/Users/valorengels/src/ai/.env'))
   active = [p.strip() for p in os.getenv('ACTIVE_PROJECTS', '').split(',') if p.strip()]

   for project_key in active:
       project = projects.get(project_key, {})
       groups = project.get('telegram', {}).get('groups', [])
       matched = False
       for group in groups:
           # Strip 'Dev: ' prefix to get the calendar name
           cal_name = group.replace('Dev: ', '')
           if cal_name in gcal_by_name:
               calendars[project_key] = gcal_by_name[cal_name]
               print(f'Mapping {project_key} -> {cal_name}')
               matched = True
               break
       if not matched and groups:
           print(f'INFO: Project \"{project_key}\" will use default calendar (no dedicated calendar found)')

   # Write config
   config = {'calendars': calendars}
   config_path.parent.mkdir(parents=True, exist_ok=True)
   config_path.write_text(json.dumps(config, indent=2) + '\n')
   print()
   print(f'Wrote {config_path} with {len(calendars)} entries')

   # Verify all mapped calendars are accessible
   print()
   for slug, cal_id in calendars.items():
       if cal_id == 'primary':
           print(f'OK: {slug} -> primary (always accessible)')
           continue
       try:
           service.calendars().get(calendarId=cal_id).execute()
           print(f'OK: {slug} -> accessible')
       except Exception:
           print(f'FAIL: {slug} -> inaccessible ({cal_id[:40]}...)')
   "
   ```

   Report status per project:
   - **Mapped**: Project has a dedicated Google Calendar
   - **Using default**: Project uses "Internal Projects" calendar (standard for internal projects)
   - **Auth failed**: OAuth token invalid or missing — run `/setup`
   - **Inaccessible**: Calendar mapped but API returns error

12. **Verify MCP servers**

   The Agent SDK inherits MCP servers from Claude Code's local/project settings via `setting_sources`. Check what's configured:

   ```bash
   claude mcp list
   ```

   - Report the list of configured MCP servers (these are shared with the Agent SDK)
   - If none are configured, note that the SDK agent will only have built-in tools (bash, file read/write, etc.)
   - MCP servers are managed via `claude mcp add/remove` — any changes take effect on next bridge restart

13. **Report results** to the user: what was pulled (summary of commits), whether dependencies were updated, whether the service restarted successfully, SDK auth mode, CLI tool health, calendar status, and MCP server status.

## Troubleshooting

### Virtual environment issues
If `.venv/` is corrupted or broken:
```bash
rm -rf .venv
uv venv
uv sync --all-extras
```

### Missing dependencies after update
If imports fail after pulling changes:
```bash
uv sync --all-extras --reinstall
```

### Calendar integration not working
1. Check OAuth token exists: `ls ~/Desktop/claude_code/google_token.json`
2. Re-run OAuth flow: `valor-calendar test` (opens browser)
3. Verify dependencies: `.venv/bin/python -c "import google_auth_oauthlib; print('OK')"`
