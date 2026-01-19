# Local Setup Guide

This guide sets up Valor AI with Telegram integration using a hybrid architecture:
- **Clawdbot** handles agent logic and SOUL.md persona
- **Python bridge** connects Telegram user account to Clawdbot

## Prerequisites

- **Node.js ≥22** (for Clawdbot)
- **Python 3.11+** (for Telegram bridge)
- **npm or pnpm** (package manager)
- **Telegram API credentials** (api_id, api_hash from my.telegram.org)

## Architecture

```
Telegram App → Python Bridge (Telethon) → clawdbot agent --local → Claude
     ↑                   ↓                         ↓
     └───────────────────┘                  ~/clawd/SOUL.md
           Response
```

The Python bridge:
- Connects as a **user account** (not a bot) for natural @mentions
- Listens for messages containing "valor", "@valor", etc.
- Forwards to Clawdbot's local agent for AI processing
- Returns responses to Telegram

## Quick Start

```bash
# 1. Install Clawdbot
npm install -g clawdbot@latest

# 2. Copy Valor persona
mkdir -p ~/clawd
cp config/SOUL.md ~/clawd/SOUL.md

# 3. Install Python dependencies
cd /Users/valorengels/src/ai
pip install telethon python-dotenv

# 4. Configure .env (see below)

# 5. Start the bridge
./scripts/start_bridge.sh
```

## Step-by-Step Setup

### 1. Install Clawdbot

```bash
npm install -g clawdbot@latest
```

Verify installation:
```bash
clawdbot --version
```

### 2. Create Clawdbot Workspace

```bash
mkdir -p ~/clawd
cp config/SOUL.md ~/clawd/SOUL.md
```

### 3. Configure Clawdbot

Create config directory:
```bash
mkdir -p ~/.clawdbot
```

Create `~/.clawdbot/clawdbot.json`:
```json
{
  "agent": {
    "model": "anthropic/claude-sonnet-4-20250514",
    "workspace": "~/clawd"
  },
  "gateway": {
    "port": 18789,
    "bind": "loopback"
  },
  "sandbox": {
    "mode": "none"
  }
}
```

### 4. Configure Environment

Ensure your `.env` file has these variables:
```bash
# Anthropic API (required)
ANTHROPIC_API_KEY=sk-ant-...

# Telegram User Account (from my.telegram.org)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_PHONE=+1234567890
TELEGRAM_PASSWORD=your_2fa_password  # if enabled

# Bridge Configuration
TELEGRAM_SESSION_NAME=valor_bridge
TELEGRAM_ALLOWED_GROUPS=MyGroup,AnotherGroup
TELEGRAM_ALLOW_DMS=true
```

### 5. Install Python Dependencies

```bash
cd /Users/valorengels/src/ai
source .venv/bin/activate  # if using venv
pip install telethon python-dotenv
```

Or using the project dependencies:
```bash
pip install -e .
```

### 6. First Run (Authentication)

On first run, Telethon will prompt for your phone number and verification code:

```bash
python bridge/telegram_bridge.py
```

Follow the prompts:
1. Enter your phone number (if prompted)
2. Enter the verification code sent to Telegram
3. Enter 2FA password (if enabled)

The session is saved to `data/valor_bridge.session` for future runs.

### 7. Running the Bridge

After authentication:
```bash
# Direct run
python bridge/telegram_bridge.py

# Or use the script
./scripts/start_bridge.sh
```

## How It Works

### Mention Detection

The bridge responds when messages contain:
- `@valor`
- `@valorengels`
- `valor`
- `hey valor`

### Group Filtering

Configure `TELEGRAM_ALLOWED_GROUPS` to limit which groups the bridge responds in:
```bash
TELEGRAM_ALLOWED_GROUPS="Valor~Yudame,Dev Team"
```

### Session Continuity

Each chat gets its own session ID (`tg_<chat_id>`), maintaining conversation context across messages.

## Commands

### Bridge Control

| Command | Description |
|---------|-------------|
| `python bridge/telegram_bridge.py` | Start bridge |
| `./scripts/start_bridge.sh` | Start with auto-setup |
| `Ctrl+C` | Stop bridge |

### Clawdbot (for debugging)

| Command | Description |
|---------|-------------|
| `clawdbot agent --local -m "test"` | Test agent locally |
| `clawdbot doctor` | Diagnose issues |
| `clawdbot --version` | Check version |

## Troubleshooting

### "Please enter the code you received"

This appears on first run. Check your Telegram app for a verification code.

### "The password you entered is invalid"

Your 2FA password in `TELEGRAM_PASSWORD` is incorrect.

### "API ID or hash invalid"

Get fresh credentials from https://my.telegram.org

### "Session revoked"

Delete `data/valor_bridge.session` and re-authenticate:
```bash
rm data/valor_bridge.session
python bridge/telegram_bridge.py
```

### Clawdbot errors

Check your Anthropic API key:
```bash
echo $ANTHROPIC_API_KEY | head -c 20
```

Test the agent directly:
```bash
clawdbot agent --local -m "Hello, test message"
```

## Running as a Service (Optional)

### macOS (launchd)

Create `~/Library/LaunchAgents/com.valor.bridge.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.valor.bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/valorengels/src/ai/.venv/bin/python</string>
        <string>/Users/valorengels/src/ai/bridge/telegram_bridge.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/valorengels/src/ai</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/valorengels/src/ai/logs/bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/valorengels/src/ai/logs/bridge.error.log</string>
</dict>
</plist>
```

Load the service:
```bash
launchctl load ~/Library/LaunchAgents/com.valor.bridge.plist
```

## Next Steps

1. **Add Skills**: See `docs/SKILLS_MIGRATION.md` for adding Clawdbot skills
2. **Customize Mentions**: Edit `MENTIONS` in `bridge/telegram_bridge.py`
3. **Set up Daydream**: Configure scheduled maintenance tasks
