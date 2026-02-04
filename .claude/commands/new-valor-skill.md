# New Valor Skill

Checklist for building a new Valor-specific skill (tool, capability, or feature).

## When to Use This Guide

- Building a tool unique to Valor (not shared across projects)
- Creating capabilities that integrate with Valor's Telegram bridge
- Adding features that should be documented in CLAUDE.md

## Valor-Specific vs Shared

| Valor-Specific | Shared/Generic |
|----------------|----------------|
| Uses SOUL.md persona | Works in any Claude Code context |
| Integrates with Telegram bridge | Standalone utility |
| References `config/` files | No Valor dependencies |
| Uses SDK client patterns | Generic Python module |
| Documented in CLAUDE.md | Self-contained docs |

**Examples:**
- Valor-specific: `valor-image-gen`, `valor-calendar`, Telegram history tools
- Shared: `agent-browser` (npm package), generic file utilities

---

## Checklist

### Phase 1: Planning

- [ ] **Define the capability** - What does this skill do?
- [ ] **Identify integration points** - Does it need Telegram? SDK? External APIs?
- [ ] **Check for existing patterns** - Look at similar tools in `tools/` directory
- [ ] **List dependencies** - External APIs, Python packages, env vars needed
- [ ] **Choose the interface** - Python library, CLI command, or both?
- [ ] **Plan the model usage** - Which model tier? (see `config/models.py`)

### Phase 2: Implementation

- [ ] **Create directory structure**
  ```
  tools/<tool-name>/
    __init__.py       # Main implementation
    manifest.json     # Tool metadata
    README.md         # Documentation
    tests/
      __init__.py
      test_<tool>.py  # Integration tests
  ```

- [ ] **Implement `__init__.py`** with:
  - [ ] Docstring explaining the tool
  - [ ] Import from `config/models.py` for AI models
  - [ ] Main function(s) with type hints
  - [ ] Error handling returning `{"error": "message"}` pattern
  - [ ] `main()` function for CLI entry point
  - [ ] `if __name__ == "__main__": main()` block

- [ ] **Create `manifest.json`**
  ```json
  {
    "name": "tool-name",
    "version": "1.0.0",
    "description": "What it does",
    "type": "library",
    "status": "beta",
    "source": {"type": "internal"},
    "capabilities": ["capability1"],
    "requires": {
      "env": ["API_KEY_NAME"],
      "python": ">=3.11"
    }
  }
  ```

- [ ] **Register CLI command** in `pyproject.toml`:
  ```toml
  [project.scripts]
  valor-<name> = "tools.<module>:main"
  ```

- [ ] **Run `pip install -e .`** to install the CLI command

### Phase 3: Integration

- [ ] **Update CLAUDE.md** - Add to "Local Python Tools" or "Image Tools" section:
  ```markdown
  - **Tool Name** (`valor-<name>`): Brief description
    ```bash
    valor-<name> arg1 arg2    # Example usage
    ```
  ```

- [ ] **Bridge integration** (if tool output should be sent to Telegram):
  - File paths in output are auto-detected by `extract_files_from_response()`
  - Use `<<FILE:/path/to/file>>` marker for explicit file sending
  - Check `ABSOLUTE_PATH_PATTERN` in bridge covers your file types

- [ ] **SDK integration** (if tool should be callable by agent):
  - Add to agent's available tools if needed
  - Document in CLAUDE.md so agent knows it exists

- [ ] **Model configuration** (if using AI):
  - Import from `config/models.py`: `MODEL_FAST`, `MODEL_REASONING`, etc.
  - Add new model constants if needed (e.g., specialized vision models)

### Phase 4: Testing

- [ ] **Create test file** at `tools/<tool-name>/tests/test_<tool>.py`

- [ ] **Write integration tests** (real APIs, not mocks):
  ```python
  import os
  import pytest
  from tools.<tool_name> import main_function

  class TestToolName:
      @pytest.mark.skipif(
          not os.environ.get("REQUIRED_API_KEY"),
          reason="API key not set"
      )
      def test_real_api_call(self):
          result = main_function("test input")
          assert "error" not in result
          assert "expected_field" in result
  ```

- [ ] **Run tests**: `pytest tools/<tool-name>/tests/ -v`

- [ ] **Test CLI command**: `valor-<name> --help`

- [ ] **Manual verification** in Telegram DM (if bridge-integrated)

### Phase 5: Documentation

- [ ] **Tool README.md** with:
  - [ ] Overview and purpose
  - [ ] Installation (if any extra deps)
  - [ ] Usage examples (Python and CLI)
  - [ ] API reference for main functions
  - [ ] Environment variables required

- [ ] **CLAUDE.md updated** (so agent knows about it)

- [ ] **Code comments** for complex logic

### Phase 6: Deployment

- [ ] **Format code**: `black tools/<tool-name>/`
- [ ] **Lint code**: `ruff check tools/<tool-name>/`
- [ ] **Commit changes**:
  ```bash
  git add tools/<tool-name>/ pyproject.toml CLAUDE.md
  git commit -m "Add valor-<name> tool for <purpose>"
  git push
  ```
- [ ] **Reinstall package**: `pip install -e .`
- [ ] **Restart bridge** (if bridge-integrated):
  ```bash
  pkill -f telegram_bridge.py
  ./scripts/start_bridge.sh
  ```
- [ ] **Verify in production** - Test via Telegram

---

## Reference: Image Tools Implementation

The `valor-image-gen` and `valor-image-analyze` tools follow this pattern exactly:

**Files created:**
- `tools/image_gen/__init__.py` - Generation with Gemini 3 Pro
- `tools/image_analysis/__init__.py` - Analysis with Claude vision
- `config/models.py` - Model constants (`MODEL_IMAGE_GEN`, `MODEL_VISION`)

**Integration points:**
- `pyproject.toml` - CLI registration
- `CLAUDE.md` - Agent documentation
- `bridge/telegram_bridge.py` - Auto file detection patterns

**Key patterns used:**
- Try Anthropic API first, fall back to OpenRouter
- Return `{"error": ...}` on failure, structured dict on success
- `main()` function with argparse-style CLI
- Images saved to `generated_images/` for bridge auto-detection

---

## Quick Start Template

```bash
# 1. Create directory
mkdir -p tools/my_tool/tests
touch tools/my_tool/__init__.py
touch tools/my_tool/manifest.json
touch tools/my_tool/README.md
touch tools/my_tool/tests/__init__.py
touch tools/my_tool/tests/test_my_tool.py

# 2. Implement (copy pattern from tools/image_gen/__init__.py)

# 3. Register CLI in pyproject.toml

# 4. Install
pip install -e .

# 5. Test
valor-my-tool --help
pytest tools/my_tool/tests/ -v

# 6. Document in CLAUDE.md

# 7. Deploy
git add . && git commit -m "Add valor-my-tool" && git push
```
