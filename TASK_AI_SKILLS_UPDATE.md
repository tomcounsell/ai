# Task: Update yudame/ai-skills Repository with Creative Juices MCP

## Overview
The Creative Juices MCP server now has a revamped installation page with:
- One-click `.mcpb` bundle download
- Collapsible manual installation instructions for multiple clients
- Future-ready for API key requirements

The `yudame/ai-skills` repository should be updated to reflect these new installation methods and host relevant manifests/documentation.

## Repository Information
- **Public Repo**: https://github.com/yudame/ai-skills
- **Purpose**: Hosts documentation and manifests for publicly available MCP servers

## Files to Add/Update in yudame/ai-skills

### 1. Creative Juices Manifest
**Location**: `/creative-juices/manifest.json`

Copy from: `apps/ai/mcp/creative_juices_manifest.json` in cuttlefish repo

Update the following fields:
- `repository`: Should point to `https://github.com/yudame/cuttlefish`
- `documentation`: Should point to the ai-skills hosted documentation
- `homepage`: Update to production URL

### 2. Creative Juices README
**Location**: `/creative-juices/README.md`

Copy from: `apps/ai/mcp/CREATIVE_JUICES_README.md` in cuttlefish repo

Update the following:
- Installation instructions to reference the `.mcpb` download
- Links to point to yudame repositories
- Add link to web installation page: `https://ai.yuda.me/mcp/creative-juices`

### 3. Installation Guide
**Location**: `/creative-juices/INSTALLATION.md` (new file)

Create a comprehensive installation guide that covers:

#### Quick Install (Recommended)
```markdown
## One-Click Installation

Download the MCP Bundle for instant installation:
https://ai.yuda.me/mcp/creative-juices/download.mcpb

After downloading, double-click the `.mcpb` file. Your MCP client will handle the rest.

**Compatible with**: Claude Desktop, Cursor, Windsurf, and other MCP-compatible clients.
```

#### Manual Installation by Client

**Claude Desktop**
- Config file location (macOS, Windows, Linux)
- JSON configuration example
- Restart instructions

**Claude Code CLI**
```bash
claude mcp add creative-juices uvx run https://raw.githubusercontent.com/yudame/cuttlefish/main/apps/ai/mcp/creative_juices_server.py
```

**Cursor**
- Config file: `~/.cursor/mcp.json`
- JSON configuration example

**Windsurf**
- Config file: `~/.codeium/windsurf/mcp_config.json`
- JSON configuration example

### 4. MCP Bundle Hosting Considerations

**Option A: Host Static .mcpb File**
- Pre-generate the `.mcpb` bundle
- Host at `/creative-juices/creative-juices.mcpb`
- Update periodically when manifest changes

**Option B: Link to Dynamic Generation**
- Direct users to `https://ai.yuda.me/mcp/creative-juices/download.mcpb`
- This URL generates the bundle dynamically from Django
- Benefit: Always up-to-date
- Trade-off: Requires cuttlefish server to be running

**Recommendation**: Use Option B (link to dynamic generation) for now, consider Option A if you want a fully static fallback.

### 5. Directory Structure

Proposed structure for yudame/ai-skills:
```
yudame/ai-skills/
├── README.md (main repo overview)
├── creative-juices/
│   ├── README.md (tool documentation)
│   ├── INSTALLATION.md (detailed install guide)
│   ├── manifest.json (MCP manifest)
│   └── CHANGELOG.md (version history)
├── cto-tools/
│   └── ... (similar structure)
└── future-skills/
    └── ...
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

## Key URLs to Reference

- **Production Landing Page**: https://ai.yuda.me/mcp/creative-juices
- **Dynamic Bundle Download**: https://ai.yuda.me/mcp/creative-juices/download.mcpb
- **Manifest**: https://ai.yuda.me/mcp/creative-juices/manifest.json
- **Source Repo**: https://github.com/yudame/cuttlefish
- **Server File**: https://raw.githubusercontent.com/yudame/cuttlefish/main/apps/ai/mcp/creative_juices_server.py

## Future Considerations

### API Key Support
When API keys are required in the future:
1. Update manifest.json to include `user_config` section
2. Update INSTALLATION.md to include API key setup instructions
3. Update `.mcpb` generation to include user-specific keys (if logged in)
4. Consider hosting a public "get API key" page

### Version Management
- Use semantic versioning in manifest.json
- Update CHANGELOG.md with each release
- Consider tagging releases in both repos

### Testing Matrix
Document which MCP clients have been tested:
- [ ] Claude Desktop (macOS)
- [ ] Claude Desktop (Windows)
- [ ] Claude Code CLI
- [ ] Cursor
- [ ] Windsurf
- [ ] VS Code (if supported)

## Questions for Project Owner

1. Should yudame/ai-skills host static `.mcpb` files or link to dynamic generation?
2. What's the branching strategy for ai-skills? (main only, or develop/release branches)
3. Should manifests in ai-skills be considered "canonical" or just mirrors?
4. Do you want CI/CD to auto-sync from cuttlefish to ai-skills?
5. Should we add analytics/download tracking for `.mcpb` downloads?

## Contact

If you have questions about this task or need clarification on the cuttlefish implementation, refer to:
- Source code: `apps/ai/mcp/creative_juices_*` files
- Django view: `apps/ai/views/mcp_views.py` (CreativeJuicesBundleView)
- URL routing: `apps/ai/urls.py`
