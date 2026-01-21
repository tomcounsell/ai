# Local Setup Guide

This guide sets up Valor AI with Telegram integration using the Claude Agent SDK.

## Architecture

```
Telegram App → Python Bridge (Telethon) → Claude Agent SDK → Claude API
     ↑                   ↓                        ↓
     └───────────────────┘                 config/SOUL.md
           Response
```

The system now supports two backends:
- **Claude Agent SDK** (recommended) - Official SDK with Claude Code capabilities
- **Clawdbot** (legacy) - Third-party tool, available for fallback

## Prerequisites

- **Python 3.11+**
- **Telegram API credentials** (api_id, api_hash from my.telegram.org)
- **Anthropic API key**

## Quick Start

```bash
# 1. Clone and enter directory
cd /Users/valorengels/src/ai

# 2. Install dependencies
pip install -e .

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 4. Start the bridge
./scripts/start_bridge.sh
```

## Step-by-Step Setup

### 1. Install Python Dependencies

```bash
cd /Users/valorengels/src/ai
pip install -e .
```

This installs:
- `claude-agent-sdk` - Official Claude Agent SDK
- `telethon` - Telegram client
- `python-dotenv` - Environment management
- Other dependencies from `pyproject.toml`

### 2. Configure Environment

Copy the example and edit:

```bash
cp .env.example .env
```

Required variables:

```bash
# Anthropic API (required)
ANTHROPIC_API_KEY=sk-ant-...

# Telegram User Account (from my.telegram.org)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_PHONE=+1234567890
TELEGRAM_PASSWORD=your_2fa_password  # if enabled

# Agent Backend (recommended: true)
USE_CLAUDE_SDK=true

# Bridge Configuration
TELEGRAM_SESSION_NAME=valor_bridge
ACTIVE_PROJECTS=valor
```

### 3. Get Telegram Credentials

1. Go to https://my.telegram.org
2. Log in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Copy `api_id` and `api_hash` to your `.env`

### 4. First Run (Authentication)

On first run, Telethon will prompt for verification:

```bash
python bridge/telegram_bridge.py
```

Follow the prompts:
1. Enter your phone number (if prompted)
2. Enter the verification code sent to Telegram
3. Enter 2FA password (if enabled)

The session is saved to `data/valor_bridge.session` for future runs.

### 5. Verify SDK Backend

Check the logs to confirm the SDK is active:

```bash
./scripts/start_bridge.sh
```

Look for:
```
[INFO] Agent backend: Claude Agent SDK
```

## Configuration Options

### Agent Backend Selection

| Setting | Backend | Use Case |
|---------|---------|----------|
| `USE_CLAUDE_SDK=true` | Claude Agent SDK | **Recommended** - Same capabilities as Claude Code CLI |
| `USE_CLAUDE_SDK=false` | Clawdbot | Legacy fallback, requires separate Clawdbot installation |

### Multi-Project Configuration

Edit `config/projects.json` to configure which Telegram groups each project monitors:

```json
{
  "projects": {
    "valor": {
      "name": "Valor AI",
      "telegram": {
        "groups": ["Dev: Valor"],
        "respond_to_mentions": true
      }
    }
  }
}
```

Set which projects this machine monitors:
```bash
ACTIVE_PROJECTS=valor,popoto
```

### DM Whitelist

Control who can DM Valor:
```bash
TELEGRAM_DM_WHITELIST=Tom,alice,bob
```

## Service Management

### Running the Bridge

```bash
# Direct run
python bridge/telegram_bridge.py

# Or use the script
./scripts/start_bridge.sh

# Check status
./scripts/valor-service.sh status
```

### Service Commands

| Command | Description |
|---------|-------------|
| `./scripts/valor-service.sh status` | Check if running |
| `./scripts/valor-service.sh restart` | Restart after changes |
| `./scripts/valor-service.sh logs` | View recent logs |
| `./scripts/valor-service.sh health` | Health check |

### Running as a Service (Optional)

#### macOS (launchd)

Install as auto-start service:
```bash
./scripts/valor-service.sh install
```

Or manually create `~/Library/LaunchAgents/com.valor.bridge.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.valor.bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/valorengels/src/ai/bridge/telegram_bridge.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/valorengels/src/ai</string>
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

## Troubleshooting

### "ModuleNotFoundError: No module named 'claude_agent_sdk'"

The SDK isn't installed or not in the Python path. Fix:
```bash
pip install claude-agent-sdk
```

### "Agent backend: Clawdbot (legacy)" when SDK expected

The `.env` file isn't being loaded before the flag check. Ensure `.env` exists and contains:
```bash
USE_CLAUDE_SDK=true
```

### "Please enter the code you received"

This appears on first run. Check your Telegram app for a verification code.

### "Session revoked"

Delete the session file and re-authenticate:
```bash
rm data/valor_bridge.session
python bridge/telegram_bridge.py
```

### SDK Errors

Check the logs:
```bash
tail -f logs/bridge.log
```

Test the SDK directly:
```python
python3 -c "from agent import ValorAgent; print('SDK OK')"
```

## Legacy: Clawdbot Setup

If you need the Clawdbot backend for fallback:

```bash
# Install Clawdbot
npm install -g clawdbot@latest

# Create workspace
mkdir -p ~/clawd
cp config/SOUL.md ~/clawd/SOUL.md

# Configure
mkdir -p ~/.clawdbot
# Create ~/.clawdbot/clawdbot.json (see docs/CLAWDBOT_MIGRATION_PLAN.md)

# Disable SDK
# In .env:
USE_CLAUDE_SDK=false
```

## Next Steps

1. **Test the integration** - Send a message via Telegram
2. **Configure projects** - Edit `config/projects.json` for your groups
3. **Review migration plan** - See `docs/plans/claude-agent-sdk-migration.md` for Phase 2 plans
