# Update & Restart

Pull the latest changes from the remote repository and restart the bridge service.

## Steps

1. **Pull latest changes**
   ```bash
   cd /Users/valorengels/src/ai && git pull --ff-only
   ```
   - If the pull fails due to local changes, stash them first with `git stash`, pull, then `git stash pop`.
   - If the pull fails due to diverged branches, stop and inform the user. Do NOT force-pull.

2. **Install any new dependencies**
   ```bash
   cd /Users/valorengels/src/ai && .venv/bin/python -m pip install -e . --quiet
   ```
   - Only run this if `pyproject.toml` was modified in the pulled changes (check `git diff HEAD@{1} --name-only` for `pyproject.toml`).
   - Also install dev dependencies if needed: `.venv/bin/python -m pip install -e ".[dev]" --quiet`
   - If `python -m pip` fails with "No module named pip", bootstrap it first: `.venv/bin/python -m ensurepip`

3. **Ensure Ollama summarizer model is available**

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

4. **Verify SDK subscription auth**

   The SDK uses the Max subscription via OAuth instead of API credits. Verify it's set up:

   ```bash
   cd /Users/valorengels/src/ai

   # Check claude login status
   claude auth status 2>&1 || claude --version

   # Ensure USE_API_BILLING is not set to true (subscription is preferred)
   if grep -q 'USE_API_BILLING=true' .env 2>/dev/null; then
     echo "WARNING: USE_API_BILLING=true is set in .env — SDK will use API credits instead of Max subscription"
     echo "Remove or set to false unless subscription auth is broken"
   else
     echo "OK: SDK will use Max subscription auth (no API credits burned)"
   fi

   # Ensure claude login has been run on this machine
   if [ ! -f ~/.claude/credentials.json ] && [ ! -f ~/.claude/.credentials.json ]; then
     echo "FAIL: No claude credentials found. Run 'claude login' to authenticate with Max subscription"
   else
     echo "OK: Claude credentials found"
   fi
   ```

   - If `claude login` hasn't been run on this machine, run it now (requires browser auth)
   - If `USE_API_BILLING=true` is set, ask the user if they want to switch to subscription auth

5. **Restart the bridge service**
   ```bash
   /Users/valorengels/src/ai/scripts/valor-service.sh restart
   ```

6. **Verify the service is running**
   ```bash
   sleep 2 && /Users/valorengels/src/ai/scripts/valor-service.sh status
   ```

7. **Verify CLI tools are available**

   Run each check and report pass/fail. Group results by category.

   **System tools:**
   ```bash
   claude --version          # Claude Code CLI
   gh --version              # GitHub CLI
   git --version             # Git
   ```

   **Python environment:**
   ```bash
   .venv/bin/python --version
   .venv/bin/pytest --version
   .venv/bin/ruff --version
   .venv/bin/python -c "import telethon; import httpx; import dotenv; import anthropic; import ollama; print('Core Python deps OK')"
   ```

   **Valor CLI tools:**
   ```bash
   # SMS reader - reads macOS Messages for 2FA codes etc.
   .venv/bin/python -m tools.sms_reader.cli recent --limit 1

   # Browser automation - headless browser for web interaction
   agent-browser --version

   # Calendar time tracking
   valor-calendar 2>&1 || true
   ```

   - If any tool is missing, attempt to install it (pip for Python packages, brew/npm for system tools).
   - Ensure `~/Library/Python/3.12/bin` is on PATH (where pip installs script entry points):
     ```bash
     if ! grep -q 'Library/Python/3.12/bin' ~/.zshrc 2>/dev/null; then
       echo 'export PATH="$HOME/Library/Python/3.12/bin:$PATH"' >> ~/.zshrc
       echo "Added Python 3.12 bin to PATH in ~/.zshrc"
     fi
     export PATH="$HOME/Library/Python/3.12/bin:$PATH"
     ```
   - Report which tools passed and which failed.

8. **Generate Google Calendar config**

   Auto-generate `~/Desktop/claude_code/calendar_config.json` by matching Google Calendar names to projects.

   **Calendar mapping rules:**
   - `"dm"` → `"primary"` (DMs go to the user's main calendar)
   - `"default"` → calendar named **"Internal Projects"** (catch-all for Claude Code sessions and unmatched slugs)
   - Project-specific: match each project's Telegram group name (minus the `"Dev: "` prefix) to a Google Calendar with the same name

   ```bash
   export PATH="$HOME/Library/Python/3.12/bin:$PATH"
   python3 -c "
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
       if not matched:
           print(f'WARN: No calendar match for project \"{project_key}\" (groups: {groups})')

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
   - **Mapped**: Project matched to a Google Calendar by name
   - **Auth failed**: OAuth token invalid or missing — run `/setup`
   - **No match**: No Google Calendar matches the Telegram group name (will use default)
   - **Inaccessible**: Calendar mapped but API returns error

9. **Verify MCP servers**

   The Agent SDK inherits MCP servers from Claude Code's local/project settings via `setting_sources`. Check what's configured:

   ```bash
   claude mcp list
   ```

   - Report the list of configured MCP servers (these are shared with the Agent SDK)
   - If none are configured, note that the SDK agent will only have built-in tools (bash, file read/write, etc.)
   - MCP servers are managed via `claude mcp add/remove` — any changes take effect on next bridge restart

10. **Report results** to the user: what was pulled (summary of commits), whether dependencies were updated, whether the service restarted successfully, SDK auth mode, CLI tool health, calendar status, and MCP server status.
