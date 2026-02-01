# Google Workspace Integration Plan

## Prerequisites (REQUIRED BEFORE STARTING)

These items must be provided by Tom before implementation can begin:

- [x] **Google Cloud Project ID** - Project credentials provided ✅
- [x] **OAuth Client Credentials** - Stored in `.env` ✅
  - Application type: Desktop app
  - Scopes configured (see Authentication section below)
  - Environment variables: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- [ ] **Confirmation of authorized redirect URIs** - Verify `http://localhost` is whitelisted
- [ ] **Workspace domain verification** (if needed) - Confirm valor@yuda.me has necessary permissions

**Credentials location:** `.env` (git-ignored)

---

## Problem

The Google Workspace MCP extension (from `gemini-cli-extensions/workspace`) connects to Claude Code but its tools aren't discoverable. The extension was built for Gemini CLI and uses an incompatible tool format.

**Current state:**
- Extension installed at `~/.gemini/extensions/google-workspace/`
- MCP server connects (`claude mcp list` shows ✓ Connected)
- Tools NOT available via ToolSearch
- Valor has a Google Workspace account (valor@yuda.me) with full access

## Goal

Enable Valor's Agent SDK sessions to access Google Workspace services:
- Gmail (read, send, search emails)
- Calendar (view, create, manage events)
- Docs, Sheets, Slides (create, read, edit)
- Drive (search, manage files)
- Chat (send messages, list spaces)

## Options

### Option A: Gemini Sub-Agent (Quick Win)

Use Gemini CLI as a sub-agent when Google Workspace access is needed.

**Pros:**
- Already installed and authenticated
- Full feature set available immediately
- No additional development

**Cons:**
- Spawns separate process (latency)
- Context doesn't transfer between Claude and Gemini
- Two AI models = higher cost per operation

**Implementation:**
1. Create a wrapper function that calls `gemini -y "<prompt>"`
2. Parse Gemini's output and return to Claude
3. Document usage pattern in SOUL.md

```python
# Example wrapper
def google_workspace_via_gemini(prompt: str) -> str:
    result = subprocess.run(
        ["gemini", "-y", prompt],
        capture_output=True, text=True
    )
    return result.stdout
```

### Option B: Native MCP Server (Best Long-term)

Create a Claude-compatible MCP server that wraps Google APIs directly.

**Pros:**
- Native tool access from Claude Code
- No Gemini dependency
- Full control over tool definitions
- Lower latency

**Cons:**
- Development effort required
- Need to handle OAuth flow
- Must maintain API compatibility

**Implementation:**
1. Use `google-auth` and `google-api-python-client`
2. Create MCP server similar to `tools/mcp_server.py`
3. Implement tools for each service:
   - `gmail_search`, `gmail_get`, `gmail_send`
   - `calendar_list`, `calendar_create`
   - `docs_create`, `docs_read`, `docs_write`
   - `drive_search`, `drive_upload`
   - `people_get_me`, `time_get_timezone`
4. Handle OAuth token refresh
5. Register with `claude mcp add`

**Estimated effort:** 2-3 hours

### Option C: Fix Gemini Extension Compatibility

Investigate why the Gemini extension tools aren't discoverable and fix.

**Pros:**
- Reuses existing extension
- Benefits from upstream updates

**Cons:**
- May not be fixable (fundamental format difference)
- Dependent on third-party code

**Investigation needed:**
1. Compare tool schema format between Gemini and Claude MCP
2. Check if extension can be modified to export Claude-compatible tools
3. Test with MCP inspector

## Recommendation

**Phase 1:** Option A - Gemini sub-agent for immediate access
**Phase 2:** Option B - Native MCP server for better integration

## Authentication

The Gemini extension already handles OAuth. For Option B, we'd need:
1. Google Cloud project with OAuth credentials
2. Token stored securely (e.g., `~/.config/valor/google_tokens.json`)
3. Scopes:
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/drive`
   - `https://www.googleapis.com/auth/documents`
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/presentations`

## Files to Create/Modify

### Option A
- `tools/google_workspace.py` - Gemini wrapper functions
- `tools/mcp_server.py` - Add wrapper tools to existing MCP
- `config/SOUL.md` - Document usage

### Option B
- `tools/google_mcp_server.py` - New MCP server
- `tools/google_auth.py` - OAuth handling
- `.claude/skills/google-workspace/SKILL.md` - Update with native tools

## Success Criteria

- [ ] Can retrieve Valor's Google profile (`people.getMe()`)
- [ ] Can list today's calendar events
- [ ] Can search Gmail
- [ ] Can create a Google Doc
- [ ] Tools accessible from Agent SDK sessions
