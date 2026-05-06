---
name: do-discover-paths
description: "Discover happy paths on a target site using BYOB MCP, producing structured trace JSON for deterministic test generation."
argument-hint: "<url> [path-name]"
allowed-tools: mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_eval, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, Bash, Read, Write, Edit, Grep, Glob
---

# Discover Happy Paths

You are the **happy path discovery agent**. You systematically explore a target site using BYOB MCP, recording each interaction as structured trace JSON that can be converted into deterministic Rodney test scripts.

## Browser surface

This skill drives the user's real, logged-in Chrome via BYOB MCP
(`mcp__byob__browser_*`). It uses `mcp__byob__browser_eval` to extract
stable CSS selectors and assert final-state values — set
`BYOB_ALLOW_EVAL=1` in the agent's environment before invoking the
skill. The gate is documented in
[`docs/features/byob-browser-control.md`](../../../docs/features/byob-browser-control.md).

## Variables

DISCOVERY_ARGS: $ARGUMENTS

**If DISCOVERY_ARGS is empty or literally `$ARGUMENTS`**: Look at the user's original message. They invoked this as `/do-discover-paths <url> [path-name]`. Extract the URL and optional path name.

## Input Parsing

Parse `DISCOVERY_ARGS`:
- First argument: **target URL** (required)
- Second argument: **path name** (optional, defaults to slugified URL path)

Example:
```
/do-discover-paths https://myapp.com/login login-to-dashboard
```

## Prerequisites

Before starting:
1. Verify BYOB is connected: `cd ~/.byob && bun run doctor` (all green)
2. Verify `BYOB_ALLOW_EVAL=1` is set in the agent's environment (this skill needs eval)
3. Verify the target URL is reachable: `mcp__byob__browser_navigate(url="<url>")`

## CSS Selector Extraction Helper

After EVERY interaction with a page element, extract a stable CSS selector using `mcp__byob__browser_eval`. This is the critical step that produces durable selectors for Rodney scripts.

**JS helper function** -- inject this after each interaction to extract the selector for the element you just interacted with:

```javascript
(function() {
  function getSelector(el) {
    if (!el) return null;
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid') + '"]';
    if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    var path = [];
    while (el && el !== document.body) {
      var selector = el.tagName.toLowerCase();
      var parent = el.parentElement;
      if (parent) {
        var siblings = Array.from(parent.children).filter(function(c) { return c.tagName === el.tagName; });
        if (siblings.length > 1) selector += ':nth-of-type(' + (siblings.indexOf(el) + 1) + ')';
      }
      path.unshift(selector);
      el = parent;
    }
    return path.join(' > ');
  }
  // Replace the querySelector argument with the actual element locator
  return getSelector(document.querySelector('ELEMENT_SELECTOR'));
})()
```

**Selector priority** (most stable first):
1. `#id` -- most stable, preferred when available
2. `[data-testid="..."]` -- explicit test hooks
3. `[name="..."]` -- form elements
4. `tag:nth-of-type(N)` path -- fallback, least stable

## Discovery Workflow

For each page/flow you explore:

### Step 1: Navigate and read
```text
mcp__byob__browser_navigate(url="<url>", waitUntil="networkidle")
mcp__byob__browser_read(url="<url>", reuseTab=true, screens=2)
```

### Step 2: Identify interactive elements
Review the `interactiveElements` list returned by `browser_read`. Each element has a `byob:idx=N` identifier and a semantic description (`name`, `role`, `tag`, `bounds`).

### Step 3: For each interaction

1. **Extract CSS selector BEFORE interacting** using `mcp__byob__browser_eval` with the JS helper above. Use the element's known attributes (tag, text, role) to locate it via `document.querySelector()`:
   ```text
   mcp__byob__browser_eval(tabId=<tab>, expression="(function() { function getSelector(el) { if (!el) return null; if (el.id) return '#' + CSS.escape(el.id); if (el.getAttribute('data-testid')) return '[data-testid=\"' + el.getAttribute('data-testid') + '\"]'; if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name=\"' + el.getAttribute('name') + '\"]'; var path = []; while (el && el !== document.body) { var s = el.tagName.toLowerCase(); var p = el.parentElement; if (p) { var sibs = Array.from(p.children).filter(function(c) { return c.tagName === el.tagName; }); if (sibs.length > 1) s += ':nth-of-type(' + (sibs.indexOf(el) + 1) + ')'; } path.unshift(s); el = p; } return path.join(' > '); } return getSelector(document.querySelector('button[type=submit]')); })()")
   ```

2. **Record the CSS selector** in your trace data.

3. **Perform the interaction** using the `byob:idx=N` selector:
   ```text
   mcp__byob__browser_click(tabId=<tab>, selector="byob:idx=3")
   ```
   or:
   ```text
   mcp__byob__browser_type(tabId=<tab>, selector="byob:idx=2", text="test@example.com")
   ```

4. **Re-read** to observe the result and refresh `byob:idx` references:
   ```text
   mcp__byob__browser_read(url="<current_url>", reuseTab=true, screens=2)
   ```

### Step 4: Record assertions
After completing the flow, record assertions about the final state:
- Current URL (use `mcp__byob__browser_eval(expression="window.location.href")`)
- Page title (use `mcp__byob__browser_eval(expression="document.title")`)
- Visible text that confirms success

## Credential Handling

For login flows or authenticated pages, use credential placeholders in the trace:
- `{{credentials.username}}` -- resolved at script generation time
- `{{credentials.password}}` -- resolved at script generation time

**NEVER** put actual credentials in trace JSON files.

## Output Format

Write the trace JSON to `tests/happy-paths/traces/<path-name>.json`. The format must conform to the trace schema:

```json
{
  "name": "<path-name>",
  "url": "<starting-url>",
  "steps": [
    {
      "action": "navigate",
      "url": "<starting-url>"
    },
    {
      "action": "input",
      "selector": "#email",
      "value": "{{credentials.username}}"
    },
    {
      "action": "click",
      "selector": "button[type=submit]"
    },
    {
      "action": "wait",
      "selector": ".dashboard-header"
    },
    {
      "action": "assert",
      "type": "url_contains",
      "value": "/dashboard"
    },
    {
      "action": "screenshot",
      "path": "evidence/<path-name>-final.png"
    }
  ],
  "expected_final_url": "/dashboard",
  "expected_text": ["Welcome", "Dashboard"]
}
```

### Valid Actions

| Action | Required Fields | Description |
|--------|----------------|-------------|
| `navigate` | `url` | Navigate to a URL |
| `input` | `selector`, `value` | Type text into a form field |
| `click` | `selector` | Click an element |
| `wait` | `selector` | Wait for an element to appear |
| `assert` | `type`, `value` | Assert a condition (url_contains, text_visible, element_exists, title_equals) |
| `screenshot` | `path` (optional) | Capture a screenshot |
| `exists` | `selector` | Check that an element exists |

## Post-Discovery

After writing the trace JSON:

1. **Validate** the trace:
   ```bash
   python -c "
   import json
   from tools.happy_path_schema import validate_trace_file
   data = json.load(open('tests/happy-paths/traces/<path-name>.json'))
   valid, errors = validate_trace_file(data)
   print('Valid:', valid)
   if errors: print('Errors:', errors)
   "
   ```

2. **Generate** the Rodney script:
   ```bash
   python tools/happy_path_generator.py tests/happy-paths/traces/<path-name>.json
   ```

3. **Report** the generated script path and a summary of the discovered flow.

## Error Handling

- If BYOB fails to navigate to the URL (transport error, blocked URL), report the error and do not produce a partial trace
- If a page element cannot be found in `interactiveElements`, skip that step and note it in the trace as a comment
- If `browser_eval` returns "browser_eval is disabled", confirm `BYOB_ALLOW_EVAL=1` is set and the BYOB MCP server has been restarted to pick up the new env
- If credential placeholders are needed but the flow context is unclear, ask the user
