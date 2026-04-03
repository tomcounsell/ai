# OfficeCLI Integration

Automated install/update of the OfficeCLI binary via the update system, with CLAUDE.md documentation and agent skill file for .docx/.xlsx/.pptx manipulation.

## Overview

OfficeCLI is an AI-friendly CLI for creating, reading, and editing Microsoft Office documents. It is a standalone binary with no runtime dependencies, installed to `~/.local/bin/officecli`.

The integration consists of three parts:
1. **Update system module** (`scripts/update/officecli.py`) -- downloads, verifies, and installs the binary
2. **CLAUDE.md section** -- documents usage patterns for the agent
3. **Agent skill file** (`.claude/skills/officecli/SKILL.md`) -- condensed reference for Office document operations

## How It Works

### Installation

The update system includes OfficeCLI as Step 3.7 (non-fatal). On each update cycle:

1. `get_asset_name()` detects the platform (macOS ARM64/x64, Linux ARM64/x64)
2. `get_installed_version()` checks if the binary exists and reads its version
3. If the installed version matches the pinned version (`PINNED_VERSION` in `scripts/update/officecli.py`), the step is skipped
4. Otherwise, the binary is downloaded from GitHub releases, SHA256-verified against the release's `SHA256SUMS` file, and installed atomically (download to temp, verify, move to final path)

All failures are non-fatal -- a warning is logged and the update continues.

### Agent Usage

The agent discovers OfficeCLI through two paths:
- **CLAUDE.md** contains a usage section with commands, flags, and common patterns (parallel to the `gws` section)
- **`.claude/skills/officecli/SKILL.md`** provides a detailed reference with format-specific examples (Word, Excel, PowerPoint), value formats, and query selectors

### Supported Platforms

| Platform | Architecture | Asset Name |
|----------|-------------|------------|
| macOS | ARM64 (Apple Silicon) | `officecli-mac-arm64` |
| macOS | x86_64 (Intel) | `officecli-mac-x64` |
| Linux | ARM64 | `officecli-linux-arm64` |
| Linux | x86_64 | `officecli-linux-x64` |

## Key Files

| File | Purpose |
|------|---------|
| `scripts/update/officecli.py` | Install/update module with platform detection, SHA256 verification |
| `scripts/update/run.py` | Update orchestrator (wires OfficeCLI as Step 3.7) |
| `.claude/skills/officecli/SKILL.md` | Agent skill reference with usage examples |
| `CLAUDE.md` | OfficeCLI section with usage patterns |
| `tests/unit/test_officecli_install.py` | Unit tests for install module |

## Design Decisions

- **Version pinning**: The module pins to a specific version (`PINNED_VERSION`) rather than always fetching the latest. This ensures reproducible installs and avoids breaking changes from upstream.
- **SHA256 verification**: Checksums are fetched from the GitHub release's `SHA256SUMS` file. If the checksum file is unavailable, the install proceeds without verification (graceful degradation).
- **Non-fatal step**: OfficeCLI installation failure does not block the rest of the update process. The binary is a convenience tool, not a system dependency.
- **No MCP registration**: OfficeCLI is invoked as a subprocess via Bash, the same pattern as `gws` and `gh`. MCP integration is deferred to a follow-up.

## See Also

- [Remote Update](remote-update.md) -- update system architecture
- [Plan: officecli-install](../plans/done/officecli-install.md) -- original plan document
- [Issue #624](https://github.com/tomcounsell/ai/issues/624) -- tracking issue
