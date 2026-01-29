# Setup - New Machine Configuration

Set up this machine to run the Valor bridge for one or more projects.

## Flow

You handle everything automated. The user only does the interactive Telegram login.

### Step 1: Dependencies

```bash
cd /Users/valorengels/src/ai
```

- Create `.venv` if it doesn't exist: `python3 -m venv .venv`
- Activate: `source .venv/bin/activate`
- Install: `pip install -e .`

### Step 2: Environment

If `.env` does not exist, copy the example:
```bash
cp .env.example .env
```

Ask the user which project(s) this machine should monitor. Then edit `.env`:
- Set `ACTIVE_PROJECTS=` to the project key(s)
- Ensure `USE_CLAUDE_SDK=true` is set
- Ensure Telegram credentials are filled in (API_ID, API_HASH, PHONE, PASSWORD)

If the user needs Telegram API credentials, direct them to https://my.telegram.org.

### Step 3: Project Config

If `config/projects.json` does not exist, copy the example:
```bash
cp config/projects.json.example config/projects.json
```

Edit `config/projects.json` for this machine. **Required rules:**
- Every project MUST have `working_directory` (absolute path)
- Include the full `defaults` section from the example
- **DO NOT set `respond_to_all: false`** — the default is `true`, which is correct for group chats
- Keep project telegram config minimal: just `"groups": ["Dev: ProjectName"]`
- Verify the `working_directory` path exists on disk

### Step 4: Telegram Login

Check if a session file exists:
```bash
ls data/*.session 2>/dev/null
```

If no session file exists:
1. Tell the user: "Please run this command and complete the Telegram login:"
   ```
   cd /Users/valorengels/src/ai && source .venv/bin/activate && python scripts/telegram_login.py
   ```
2. **STOP and WAIT** for the user to confirm they completed the login
3. Do NOT proceed until they confirm

If a session file already exists, skip to Step 5.

### Step 5: Start Bridge

After login is confirmed (or session already exists):
```bash
./scripts/start_bridge.sh
```

Verify by checking logs:
```bash
sleep 3 && tail -10 logs/bridge.log
```

Confirm to the user:
- Bridge is running
- Which project(s) and group(s) are being monitored
- Agent backend is Claude Agent SDK

### Step 6: Test

Ask the user to send a test message in the Telegram group to verify the bridge responds.

## Key Rules

- **You** install deps, create configs, and start the bridge
- **User** only does the interactive Telegram login
- Never ask the user to start the bridge — that's your job
- Never skip the login check before starting
- Never set `respond_to_all: false` in project configs
- Always include the `defaults` section in `projects.json`
- Always verify `working_directory` paths exist
