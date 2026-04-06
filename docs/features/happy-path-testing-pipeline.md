# Happy Path Testing Pipeline

A three-stage pipeline for creating and running deterministic browser regression tests without LLM tokens.

## Overview

The pipeline converts agent-browser explorations into repeatable Rodney shell scripts:

1. **Discovery** -- An agent uses `agent-browser` to explore a site, recording interactions as structured trace JSON
2. **Generation** -- A pure Python script converts trace JSON into standalone Rodney shell scripts
3. **Execution** -- A runner executes generated scripts in batch, collecting pass/fail results

## Architecture

```
Discovery (LLM, one-time)     Generation (no LLM)        Execution (no LLM)
/do-discover-paths URL    ->  happy_path_generator.py  ->  happy_path_runner.py
       |                            |                            |
  trace JSON                   .sh scripts                  pass/fail report
  tests/happy-paths/traces/    tests/happy-paths/scripts/   tests/happy-paths/evidence/
```

## Usage

### Discover a happy path

```
/do-discover-paths https://myapp.com/login login-to-dashboard
```

This uses agent-browser to explore the site and produces `tests/happy-paths/traces/login-to-dashboard.json`.

### Generate Rodney scripts

```bash
python tools/happy_path_generator.py tests/happy-paths/traces/login-to-dashboard.json
# or generate all:
python tools/happy_path_generator.py tests/happy-paths/traces/
```

Output: `tests/happy-paths/scripts/login-to-dashboard.sh`

### Run tests

```bash
# Via do-test skill:
/do-test happy-paths

# Or directly:
python tools/happy_path_runner.py tests/happy-paths/scripts/
```

## Trace JSON Format

See `tests/happy-paths/SCHEMA.md` for the complete schema reference.

Each trace file describes a user journey as an ordered list of steps:

```json
{
  "name": "login-to-dashboard",
  "url": "https://myapp.com/login",
  "steps": [
    {"action": "navigate", "url": "https://myapp.com/login"},
    {"action": "input", "selector": "#email", "value": "{{credentials.username}}"},
    {"action": "input", "selector": "#password", "value": "{{credentials.password}}"},
    {"action": "click", "selector": "button[type=submit]"},
    {"action": "wait", "selector": ".dashboard-header"},
    {"action": "assert", "type": "url_contains", "value": "/dashboard"}
  ],
  "expected_final_url": "/dashboard",
  "expected_text": ["Welcome", "Dashboard"]
}
```

### Actions

| Action | Purpose | Required Fields |
|--------|---------|-----------------|
| `navigate` | Go to URL | `url` |
| `input` | Type into form field | `selector`, `value` |
| `click` | Click element | `selector` |
| `wait` | Wait for element | `selector` |
| `assert` | Check condition | `type`, `value` |
| `screenshot` | Capture screenshot | `path` (optional) |
| `exists` | Verify element exists | `selector` |

## Credential Handling

Traces use placeholders like `{{credentials.username}}` that are converted to environment variables (`$HAPPY_PATH_USERNAME`) at generation time. Credentials are never inlined in generated scripts or committed to version control.

## CSS Selector Extraction

The discovery skill extracts stable CSS selectors via `agent-browser eval` with a JS helper function. Selector priority (most stable first):

1. `#id`
2. `[data-testid="..."]`
3. `[name="..."]`
4. `tag:nth-of-type(N)` computed path

## Rodney

[Rodney](https://github.com/simonw/rodney) is a headless Chrome test runner. It is installed as a prebuilt binary via the update system (`scripts/update/rodney.py`). No Go toolchain required.

### Rodney Command Mapping

| Trace Action | Rodney Command |
|-------------|----------------|
| `navigate` | `rodney open <url>` |
| `input` | `rodney input <selector> <value>` |
| `click` | `rodney click <selector>` |
| `wait` | `rodney wait <selector>` |
| `assert` | `rodney assert <js-expression>` |
| `screenshot` | `rodney screenshot <path>` |
| `exists` | `rodney exists <selector>` |

## File Structure

```
tests/happy-paths/
  SCHEMA.md           # Trace JSON schema documentation
  traces/             # Source trace JSON files (version controlled)
  scripts/            # Generated Rodney shell scripts (version controlled)
  evidence/           # Runtime screenshots (.gitignored)

tools/
  happy_path_schema.py      # Schema dataclasses and validation
  happy_path_generator.py   # Trace-to-script converter
  happy_path_runner.py      # Batch script executor

scripts/update/
  rodney.py           # Rodney binary installation

.claude/skills/do-discover-paths/
  SKILL.md            # Discovery agent instructions
```

## Integration with /do-test

The `happy-paths` target in `/do-test` runs the deterministic test runner directly via bash (no subagent, no LLM tokens):

```
/do-test happy-paths
```

When running all tests (`/do-test` with no arguments), happy path scripts are included alongside pytest and frontend test suites if any `.sh` files exist in `tests/happy-paths/scripts/`.
