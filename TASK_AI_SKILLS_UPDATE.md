# Task: Update yudame/ai-skills Repository with Creative Juices MCP

## Overview
The Creative Juices MCP server now has a revamped installation page with:
- One-click `.mcpb` bundle download
- Collapsible manual installation instructions for multiple clients
- Future-ready for API key requirements

The `yudame/ai-skills` repository should be updated to reflect these new installation methods and host relevant manifests/documentation.

## Repository Information
- **Public Repo**: https://github.com/yudame/ai-skills
- **Purpose**: Hosts user-facing documentation for publicly available MCP servers hosted at app.bwforce.ai

## Files to Add/Update in yudame/ai-skills

### 1. Creative Juices README
**Location**: `/creative-juices/README.md`

Create comprehensive user guide including:
- What the tool does (3 tools description)
- Usage examples
- Tool reference
- Design philosophy
- **Do NOT include**: Hosting instructions, deployment docs, private repo references

### 2. Installation Guide
**Location**: `/creative-juices/INSTALLATION.md`

Create installation guide that covers:

#### Quick Install (Recommended)
```markdown
## One-Click Installation

Download: https://app.bwforce.ai/mcp/creative-juices/download.mcpb
Double-click to install.
```

#### Manual Installation
Show JSON config for each client pointing to:
```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://app.bwforce.ai/mcp/creative-juices/serve"
    }
  }
}
```

Clients to cover:
- Claude Desktop (macOS/Windows/Linux config file locations)
- Claude Code CLI
- Cursor
- Windsurf

### 3. Manifest & Changelog
**Location**: `/creative-juices/manifest.json` and `/creative-juices/CHANGELOG.md`

- Copy manifest from cuttlefish, update repo links to ai-skills
- Create changelog with v1.0.0 release info

### 4. Directory Structure
```
yudame/ai-skills/
├── README.md (main repo overview)
└── creative-juices/
    ├── README.md (user guide)
    ├── INSTALLATION.md (install guide)
    ├── manifest.json (MCP manifest)
    └── CHANGELOG.md (version history)
```

## Implementation Checklist

- [ ] Fork/clone yudame/ai-skills repository
- [ ] Create `/creative-juices/` directory
- [ ] Copy and adapt `manifest.json`
- [ ] Copy and adapt `README.md`
- [ ] Create new `INSTALLATION.md` with multi-client instructions
- [ ] Update repository URLs from tomcounsell to yudame
- [ ] Add screenshots of the installation page (optional but helpful)
- [ ] Create `CHANGELOG.md` with version 1.0.0 initial release
- [ ] Update main repo `README.md` to list Creative Juices
- [ ] Test all installation methods
- [ ] Create PR or push to main branch

## Key URLs (all hosted at app.bwforce.ai)

- **Landing Page**: https://app.bwforce.ai/mcp/creative-juices
- **HTTP Endpoint**: https://app.bwforce.ai/mcp/creative-juices/serve
- **Bundle Download**: https://app.bwforce.ai/mcp/creative-juices/download.mcpb
- **Manifest**: https://app.bwforce.ai/mcp/creative-juices/manifest.json

## Future Considerations

### API Key Support
When API keys are required:
1. Update manifest.json user_config section
2. Update INSTALLATION.md with API key setup
3. Update installation page with API key instructions

### Version Management
- Use semantic versioning
- Update CHANGELOG.md with each release
