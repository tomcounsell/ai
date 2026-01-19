# Tool Standard

This document defines the standard for tools maintained in this repository.

## Overview

Tools are capabilities that extend what Valor can do. Each tool follows a consistent structure that enables:
- **Discoverability**: What tools exist and what can they do?
- **Validation**: Does the tool work as expected?
- **Documentation**: How do I use this tool?

## Directory Structure

```
tools/<name>/
├── manifest.json         # Machine-readable specification (required)
├── README.md             # Human documentation (required)
├── tests/                # Integration tests (required)
│   ├── __init__.py
│   └── test_<name>.py
└── src/                  # Source code (if custom implementation)
    └── ...
```

## manifest.json

Every tool must have a `manifest.json` that defines its capabilities.

### Schema

```json
{
  "name": "tool-name",
  "version": "1.0.0",
  "description": "Brief description of what this tool does",
  "type": "cli | api | library",
  "status": "stable | beta | experimental",

  "source": {
    "type": "external | internal",
    "package": "npm-package-name or pip-package-name",
    "repository": "https://github.com/org/repo",
    "command": "cli-command-name"
  },

  "capabilities": [
    "capability-1",
    "capability-2"
  ],

  "requires": {
    "env": ["ENV_VAR_1", "ENV_VAR_2"],
    "binaries": ["binary-name"],
    "python": ">=3.10",
    "node": ">=18"
  },

  "commands": {
    "install": "npm install -g package-name",
    "verify": "package-name --version",
    "help": "package-name --help"
  },

  "workflows": [
    {
      "name": "workflow-name",
      "description": "What this workflow accomplishes",
      "steps": ["step1", "step2", "step3"]
    }
  ]
}
```

### Required Fields

| Field | Description |
|-------|-------------|
| `name` | Tool identifier (lowercase, hyphens) |
| `version` | Semantic version |
| `description` | One-line description |
| `type` | `cli` (command-line), `api` (HTTP/SDK), or `library` (importable) |
| `status` | `stable`, `beta`, or `experimental` |
| `capabilities` | List of what this tool can do |

### Optional Fields

| Field | Description |
|-------|-------------|
| `source` | Where the tool comes from (external package or internal) |
| `requires` | Dependencies (env vars, binaries, runtime versions) |
| `commands` | Common commands for install, verify, help |
| `workflows` | Named multi-step patterns |

## README.md

Human-readable documentation with:

1. **Overview** - What is this tool and why use it?
2. **Installation** - How to install/configure
3. **Quick Start** - Minimal example to get going
4. **Workflows** - Common patterns with examples
5. **Command Reference** - Full command/API documentation
6. **Troubleshooting** - Common issues and solutions

## Tests

### Requirements

- **Real integration tests** - No mocks for the tool itself
- **Test actual functionality** - Verify the tool works, not just that it runs
- **Cover core workflows** - Test the patterns users will actually use
- **Isolated sessions** - Tests should not interfere with each other

### Structure

```python
"""
Integration tests for <tool-name>.

Run with: pytest tools/<name>/tests/ -v
"""

import pytest

class TestInstallation:
    """Verify tool is properly installed."""

    def test_version(self):
        """Tool responds to version command."""
        pass

    def test_help(self):
        """Tool responds to help command."""
        pass

class TestCoreWorkflow:
    """Test the primary use case."""

    def test_basic_operation(self):
        """Tool performs its main function."""
        pass

class TestErrorHandling:
    """Test graceful failure modes."""

    def test_invalid_input(self):
        """Tool handles bad input gracefully."""
        pass
```

### Running Tests

```bash
# Run all tool tests
pytest tools/ -v

# Run specific tool tests
pytest tools/browser/tests/ -v

# Run with coverage
pytest tools/ --cov=tools --cov-report=html
```

## Capability Taxonomy

Use these standard capability names when applicable:

### Data Operations
- `read` - Read/fetch data
- `write` - Create/update data
- `delete` - Remove data
- `search` - Find/query data
- `list` - Enumerate items

### Web/Browser
- `navigate` - Go to URLs
- `interact` - Click, type, fill forms
- `screenshot` - Capture images
- `scrape` - Extract data from pages
- `automate` - Run automated sequences

### Communication
- `send` - Send messages/notifications
- `receive` - Receive/poll for messages
- `subscribe` - Set up listeners

### Development
- `build` - Compile/bundle code
- `test` - Run tests
- `deploy` - Push to environments
- `monitor` - Track health/metrics

### AI/ML
- `generate` - Create content
- `analyze` - Process/understand content
- `embed` - Create vector embeddings
- `classify` - Categorize content

## Adding a New Tool

### Quick Method (Recommended)

```bash
# Create from template
python tools/new_tool.py <name>

# Edit the generated files
# Then validate and commit
python tools/validate.py tools/<name>/
pytest tools/<name>/tests/ -v
git add tools/<name>/ && git commit -m "Add <name> tool" && git push
```

### Manual Method

1. **Create directory**: `mkdir -p tools/<name>/tests`

2. **Create manifest.json**:
   ```bash
   cat > tools/<name>/manifest.json << 'EOF'
   {
     "name": "<name>",
     "version": "1.0.0",
     "description": "<description>",
     "type": "cli",
     "status": "beta",
     "capabilities": [],
     "requires": {}
   }
   EOF
   ```

3. **Create README.md** with usage documentation

4. **Create tests** in `tests/test_<name>.py`

5. **Verify**:
   ```bash
   python -m pytest tools/<name>/tests/ -v
   ```

6. **Commit**:
   ```bash
   git add tools/<name>/
   git commit -m "Add <name> tool"
   git push
   ```

## Validation

Tools can be validated with:

```bash
# Check manifest schema
python tools/validate.py tools/<name>/

# Run tests
pytest tools/<name>/tests/ -v

# Check documentation
# (README.md must exist and have required sections)
```

## External vs Internal Tools

| Location | What belongs here |
|----------|-------------------|
| `tools/` | Tools we maintain, configure, and test |
| `~/clawd/skills/` | Clawdbot-native skills (external, auto-updated) |
| `.claude/skills/` | Claude Code skill definitions (prompts only) |

### Examples

- **Browser automation** → `tools/browser/` (we configure and test it)
- **GitHub API via Clawdbot** → `~/clawd/skills/github/` (Clawdbot manages it)
- **Agent-browser skill prompt** → `.claude/skills/agent-browser/` (Claude Code uses it)
