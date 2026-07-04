---
name: do-discover-paths
description: "Discover happy paths on a target site using BYOB MCP, producing trace JSON for deterministic test generation. Use when asked to discover paths, map site flows, or record browser traces for tests."
argument-hint: "<url> [path-name]"
allowed-tools: mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_eval, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, Bash, Read, Write, Edit, Grep, Glob
---

# Discover Happy Paths

Explore one flow on a target site with BYOB MCP and record it as **structured trace JSON**: durable CSS selectors, the inputs used, and assertions about the final state. The trace is the deliverable — a file another tool can replay deterministically, without an LLM. Success means the trace conforms to the schema below, every selector is the most stable one available, the end state is asserted, and no real credential appears anywhere in it.

## Repo context probe

If `.claude/skill-context/do-discover-paths.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its trace consumers: a schema validator, a script generator and its target test runner, canonical trace/output directories, and environment specifics. When it is absent (the common case in a foreign repo), write the trace to `tests/happy-paths/traces/<path-name>.json`, then stop and report the trace path — do not invent a downstream generator.

## Browser surface

This skill drives the user's real, logged-in Chrome via BYOB MCP (`mcp__byob__browser_*`). Selector extraction and final-state assertions require `mcp__byob__browser_eval`, which is gated behind `BYOB_ALLOW_EVAL=1` in the BYOB MCP server's environment. If eval returns "browser_eval is disabled", that env var is missing — it must be set and the BYOB server restarted before this skill can produce durable selectors.

## Input

`$ARGUMENTS`: `<target-url> [path-name]`. The path name defaults to the slugified URL path. If arguments are empty, pull both from the user's original message.

## CSS selector extraction

After identifying each element you will interact with, extract a stable CSS selector for it via `mcp__byob__browser_eval` — this is what makes the trace replayable. Inject this helper (minified is fine), substituting a `document.querySelector()` locator built from the element's known attributes:

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
  return getSelector(document.querySelector('ELEMENT_SELECTOR'));
})()
```

Selector priority (most stable first): `#id` → `[data-testid="…"]` → `[name="…"]` (form elements) → `tag:nth-of-type(N)` path (last resort).

## Discovery workflow

For each page in the flow:

1. **Navigate and read.** `browser_navigate(url, waitUntil="networkidle")`, then `browser_read(url, reuseTab=true, screens=2)`. The `interactiveElements` list gives each element a `byob:idx=N` handle plus semantic attributes (`name`, `role`, `tag`, `bounds`).
2. **Per interaction:** extract and record the CSS selector first (helper above), then perform the interaction using the `byob:idx=N` handle (`browser_click` / `browser_type`), then re-read to observe the result and refresh the `byob:idx` handles — they go stale after page changes.
3. **After the flow completes,** record final-state assertions: current URL (`browser_eval("window.location.href")`), page title (`browser_eval("document.title")`), and visible text that confirms success.

## Credential handling

For login flows, use placeholders in the trace — `{{credentials.username}}` and `{{credentials.password}}` — resolved at script-generation time. Never put actual credentials in a trace file. If a flow needs credentials and the context is unclear, ask the user.

## Output format

Write the trace to `tests/happy-paths/traces/<path-name>.json` (or the repo-declared location). This schema is a contract — downstream parsers consume it exactly:

```json
{
  "name": "<path-name>",
  "url": "<starting-url>",
  "steps": [
    { "action": "navigate", "url": "<starting-url>" },
    { "action": "input", "selector": "#email", "value": "{{credentials.username}}" },
    { "action": "click", "selector": "button[type=submit]" },
    { "action": "wait", "selector": ".dashboard-header" },
    { "action": "assert", "type": "url_contains", "value": "/dashboard" },
    { "action": "screenshot", "path": "evidence/<path-name>-final.png" }
  ],
  "expected_final_url": "/dashboard",
  "expected_text": ["Welcome", "Dashboard"]
}
```

| Action | Required fields | Description |
|--------|----------------|-------------|
| `navigate` | `url` | Navigate to a URL |
| `input` | `selector`, `value` | Type text into a form field |
| `click` | `selector` | Click an element |
| `wait` | `selector` | Wait for an element to appear |
| `assert` | `type`, `value` | Assert a condition: `url_contains`, `text_visible`, `element_exists`, `title_equals` |
| `screenshot` | `path` (optional) | Capture a screenshot |
| `exists` | `selector` | Check that an element exists |

## Post-discovery

If the repo's context file declares a validator and generator, run them against the trace and report the generated script path. Otherwise report the trace path and a summary of the discovered flow — the trace itself is the complete deliverable.

## Error handling

- Navigation fails (transport error, blocked URL) → report the error; do not produce a partial trace.
- An element is missing from `interactiveElements` → skip that step and note it in the trace as a comment.
