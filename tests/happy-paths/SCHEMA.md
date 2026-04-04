# Happy Path Trace JSON Schema

This document defines the structured trace format used as the contract between the discovery stage (agent-browser exploration) and the generation stage (Rodney script generation).

## Overview

Each trace file represents one happy path -- a complete user journey through a web application. The trace is an ordered list of steps, each describing an action to perform on the page.

## File Location

Trace files are stored in `tests/happy-paths/traces/` as JSON files named after the path they represent (e.g., `login-to-dashboard.json`).

## Schema Definition

The canonical schema is defined as Python dataclasses in `tools/happy_path_schema.py`. Use `validate_trace_file()` to validate any trace JSON.

### Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier for this happy path (used as script filename) |
| `url` | string | Yes | Starting URL for the path |
| `steps` | array | Yes | Ordered list of step objects (must not be empty) |
| `expected_final_url` | string | No | URL pattern the browser should match after all steps |
| `expected_text` | array of strings | No | Text that should be visible on the final page |

### Step Object Fields

| Field | Type | Required For | Description |
|-------|------|-------------|-------------|
| `action` | string | All steps | One of: `navigate`, `input`, `click`, `wait`, `assert`, `screenshot`, `exists` |
| `url` | string | `navigate` | Target URL to navigate to |
| `selector` | string | `input`, `click`, `wait`, `exists` | CSS selector for the target element |
| `value` | string | `input`, `assert` | Input value or assertion expected value |
| `type` | string | `assert` | Assertion type: `url_contains`, `text_visible`, `element_exists`, `title_equals` |
| `path` | string | `screenshot` (optional) | File path for the screenshot output |

### Action Types

#### `navigate`
Navigate the browser to a URL.
```json
{"action": "navigate", "url": "https://myapp.com/login"}
```

#### `input`
Type text into a form field identified by CSS selector.
```json
{"action": "input", "selector": "#email", "value": "user@example.com"}
```

#### `click`
Click an element identified by CSS selector.
```json
{"action": "click", "selector": "button[type=submit]"}
```

#### `wait`
Wait for an element to appear on the page.
```json
{"action": "wait", "selector": ".dashboard-header"}
```

#### `assert`
Assert a condition about the page state.
```json
{"action": "assert", "type": "url_contains", "value": "/dashboard"}
```

Assertion types:
- `url_contains` -- current URL contains the value string
- `text_visible` -- page body text contains the value string
- `element_exists` -- a CSS selector (in value) matches an element
- `title_equals` -- page title exactly matches the value string

#### `screenshot`
Capture a screenshot of the current page state.
```json
{"action": "screenshot", "path": "evidence/login-final.png"}
```

#### `exists`
Check that an element exists on the page (non-asserting, just verification).
```json
{"action": "exists", "selector": ".user-avatar"}
```

## Credential Placeholders

Login flows use credential placeholders instead of actual values:

```json
{"action": "input", "selector": "#email", "value": "{{credentials.username}}"}
{"action": "input", "selector": "#password", "value": "{{credentials.password}}"}
```

Placeholders are resolved at script generation time to shell environment variable references (`$HAPPY_PATH_USERNAME`, `$HAPPY_PATH_PASSWORD`). Credentials are never inlined in generated scripts.

## CSS Selector Priority

Selectors in trace files follow this stability priority (most stable first):

1. `#id` -- element ID attribute
2. `[data-testid="..."]` -- explicit test hooks
3. `input[name="..."]` -- form element name attributes
4. `tag:nth-of-type(N)` computed path -- positional fallback (least stable)

## Example Trace

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
    {"action": "assert", "type": "url_contains", "value": "/dashboard"},
    {"action": "screenshot", "path": "evidence/login-to-dashboard-final.png"}
  ],
  "expected_final_url": "/dashboard",
  "expected_text": ["Welcome", "Dashboard"]
}
```

## Validation

Validate a trace file programmatically:

```python
import json
from tools.happy_path_schema import validate_trace_file

with open("tests/happy-paths/traces/my-path.json") as f:
    data = json.load(f)

is_valid, errors = validate_trace_file(data)
if not is_valid:
    for error in errors:
        print(f"ERROR: {error}")
```

Or from the command line:

```bash
python -c "
import json
from tools.happy_path_schema import validate_trace_file
data = json.load(open('tests/happy-paths/traces/my-path.json'))
valid, errors = validate_trace_file(data)
print('Valid' if valid else f'Invalid: {errors}')
"
```
