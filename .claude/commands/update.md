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

4. **Restart the bridge service**
   ```bash
   /Users/valorengels/src/ai/scripts/valor-service.sh restart
   ```

5. **Verify the service is running**
   ```bash
   sleep 2 && /Users/valorengels/src/ai/scripts/valor-service.sh status
   ```

6. **Verify CLI tools are available**

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

7. **Verify MCP servers**

   The Agent SDK inherits MCP servers from Claude Code's local/project settings via `setting_sources`. Check what's configured:

   ```bash
   claude mcp list
   ```

   - Report the list of configured MCP servers (these are shared with the Agent SDK)
   - If none are configured, note that the SDK agent will only have built-in tools (bash, file read/write, etc.)
   - MCP servers are managed via `claude mcp add/remove` — any changes take effect on next bridge restart

8. **Report results** to the user: what was pulled (summary of commits), whether dependencies were updated, whether the service restarted successfully, CLI tool health, and MCP server status.
