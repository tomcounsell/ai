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

8. **Verify Google Calendar integration**

   Test OAuth connectivity and calendar config for time tracking:

   ```bash
   export PATH="$HOME/Library/Python/3.12/bin:$PATH"
   python3 -c "
   import sys, json
   sys.path.insert(0, '/Users/valorengels/src/ai')
   from pathlib import Path

   base_dir = Path.home() / 'Desktop/claude_code'
   config_path = base_dir / 'calendar_config.json'
   tool_config_path = base_dir / 'calendar-tool-config.json'
   token_path = base_dir / 'google_token.json'

   # Check token exists
   if not token_path.exists():
       print('FAIL: No OAuth token at', token_path)
       sys.exit(0)
   print('OK: OAuth token exists')

   # Test API connectivity
   try:
       from tools.google_workspace.auth import get_service
       service = get_service('calendar', 'v3')
       service.calendarList().list(maxResults=1).execute()
       print('OK: Calendar API connected')
   except Exception as e:
       print(f'FAIL: Calendar API auth failed: {e}')
       sys.exit(0)

   # Check both config file locations
   configs_found = []
   for path, label in [(config_path, 'calendar_config.json'), (tool_config_path, 'calendar-tool-config.json')]:
       if path.exists():
           print(f'OK: {label} exists at {path}')
           configs_found.append(path)
       else:
           print(f'WARN: {label} not found at {path}')

   if not configs_found:
       print('FAIL: No calendar config found in either location')
       sys.exit(0)

   # Load from primary config, fall back to tool config
   config = json.loads(configs_found[0].read_text())
   calendars = config.get('calendars', {})
   print(f'OK: Calendar config has {len(calendars)} entries')

   # Check ACTIVE_PROJECTS have custom mappings
   import os
   from dotenv import load_dotenv
   load_dotenv(Path('/Users/valorengels/src/ai/.env'))
   active = [p.strip() for p in os.getenv('ACTIVE_PROJECTS', '').split(',') if p.strip()]
   for proj in active:
       cal_id = calendars.get(proj, calendars.get('default', 'primary'))
       if cal_id == 'primary':
           print(f'WARN: Project \"{proj}\" uses primary calendar (no custom mapping)')
       else:
           # Verify calendar is accessible
           try:
               service.calendars().get(calendarId=cal_id).execute()
               print(f'OK: Project \"{proj}\" -> calendar accessible')
           except Exception:
               print(f'FAIL: Project \"{proj}\" -> calendar inaccessible ({cal_id[:40]}...)')
   "
   ```

   Report status per project:
   - **Connected**: Project has custom calendar and it's accessible
   - **Auth failed**: OAuth token invalid or missing
   - **Missing config**: No calendar_config.json or project not mapped
   - **Inaccessible**: Calendar configured but API returns error

9. **Verify MCP servers**

   The Agent SDK inherits MCP servers from Claude Code's local/project settings via `setting_sources`. Check what's configured:

   ```bash
   claude mcp list
   ```

   - Report the list of configured MCP servers (these are shared with the Agent SDK)
   - If none are configured, note that the SDK agent will only have built-in tools (bash, file read/write, etc.)
   - MCP servers are managed via `claude mcp add/remove` — any changes take effect on next bridge restart

10. **Report results** to the user: what was pulled (summary of commits), whether dependencies were updated, whether the service restarted successfully, SDK auth mode, CLI tool health, calendar status, and MCP server status.
