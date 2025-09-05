# Telegram Client Documentation

## Important: This is NOT a Bot

This system uses a **real Telegram user account** with the Telethon library, not a bot. Key differences:

- **Real User Account**: Uses phone number authentication with 2FA support
- **Full Client Capabilities**: Can read messages, see edits, access message history
- **Natural Presence**: Appears as a regular user, not a bot
- **Session Persistence**: Maintains login session across restarts

## Authentication

```bash
# One-time setup
./scripts/telegram_login.sh
# Enter verification code when prompted
# Session is saved for future use

# Normal operation (uses saved session)
./scripts/start.sh --telegram
```

## Group Behavior Configuration

### Default Behavior

By default, the client:
- **NEVER responds to all messages** in groups
- **ONLY responds when @valor is mentioned**
- **Always responds to direct messages** (configurable)
- **Responds to replies** to its own messages

### Configuration File: `config/telegram_groups.json`

```json
{
  "default_behavior": {
    "respond_to_mentions": true,    // Respond when @valor is mentioned
    "respond_to_all": false,         // DO NOT respond to all messages
    "respond_to_replies": true,      // Respond to replies to our messages
    "typing_indicator": true,        // Show typing indicator
    "read_receipts": true           // Mark messages as read
  },
  
  "groups": {
    "Group Name": {
      "enabled": true,
      "respond_to_mentions": true,    // Only respond when mentioned
      "respond_to_all": false,         // Override for specific group
      "keywords": ["valor", "help"],  // Additional trigger words
      "ignore_users": []              // User IDs to ignore
    }
  }
}
```

### Mention Detection

The client detects mentions through:
1. **Direct mentions**: `@valor`, `@valorengels`
2. **Name mentions**: `valor`, `hey valor`, `hi valor`
3. **Custom keywords**: Per-group configurable keywords
4. **Reply chains**: Replies to messages from the client

### Per-Group Configuration

Each group can have custom behavior:

```json
"AI Developers": {
  "enabled": true,
  "respond_to_mentions": true,  // Default: only when mentioned
  "respond_to_all": false,       // Never respond to everything
  "keywords": ["ai", "help"]     // Extra triggers for this group
}
```

## Message Processing

### Decision Flow

```
Message Received
    ↓
Is it a DM? → Yes → Check whitelist/blacklist → Respond
    ↓ No
Is it a Group?
    ↓ Yes
Is @valor mentioned? → Yes → Respond
    ↓ No
Is it a reply to us? → Yes → Respond
    ↓ No
Contains keyword? → Yes → Respond (if configured)
    ↓ No
Ignore Message
```

### Logging Behavior

- Messages that trigger response: `🔔 Mention detected`
- Ignored messages: `Ignoring message - no trigger detected`
- Direct messages: `📨 New message from...`

## Security & Privacy

1. **Real User Account**: Uses your actual Telegram account
2. **Session Security**: Session files stored in `data/` directory
3. **Message History**: Only processes messages while running
4. **Group Privacy**: Only responds when explicitly triggered
5. **No Bot Badge**: Appears as regular user "Valor Engels"

## Environment Variables

```bash
# Required
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_PASSWORD=your_2fa_password  # If 2FA enabled

# Optional (deprecated, use JSON config instead)
TELEGRAM_ALLOWED_GROUPS="Group1,Group2"
TELEGRAM_ALLOW_DMS=true
```

## Best Practices

1. **Always use mention detection** in groups (default behavior)
2. **Never set `respond_to_all: true`** unless absolutely necessary
3. **Use group-specific keywords** sparingly to avoid spam
4. **Configure ignore lists** for bot accounts in groups
5. **Test in private groups** before deploying to public ones

## Testing Configuration

To test your configuration:

1. Create a test group with yourself
2. Add the configuration to `telegram_groups.json`
3. Send test messages with and without mentions
4. Verify the client only responds when mentioned

## Troubleshooting

### Client not responding to mentions
- Check `telegram_groups.json` exists and is valid JSON
- Verify group name matches exactly
- Check logs for "Mention detected" messages
- Ensure `respond_to_mentions: true` is set

### Client responding to everything
- Check `respond_to_all` is set to `false` (default)
- Verify group-specific overrides aren't set incorrectly
- Review keywords list for overly broad terms

### Authentication issues
- Re-run `./scripts/telegram_login.sh`
- Delete session file in `data/` and re-authenticate
- Ensure phone number format includes country code