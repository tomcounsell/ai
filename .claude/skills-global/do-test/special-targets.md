# Special Targets: Frontend and Happy Paths

Loaded when `TEST_ARGS` routes to the `frontend` or `happy-paths` target.
Neither target runs the project's unit-test runner.

## Frontend Testing (`frontend` target)

When `TEST_ARGS` starts with `frontend`, route to the `frontend-tester` subagent. Do **not** run the unit-test runner.

**Input format:**
```
/do-test frontend https://myapp.com "Login form submits and shows dashboard"
/do-test frontend https://myapp.com "Checkout flow completes successfully" -- steps: click add-to-cart, click checkout, fill address, submit
```

**Dispatch a single `frontend-tester` subagent:**

```
Task({
  description: "Frontend test: <scenario>",
  subagent_type: "frontend-tester",
  prompt: "
URL: <url>
Scenario: <scenario>
Steps:
  <extracted steps if provided, otherwise infer from scenario>
Expected: <inferred from scenario>
  "
})
```

The `frontend-tester` agent owns all browser interaction via BYOB MCP (`mcp__byob__browser_*`) — the skill never drives the browser directly.

**When running all tests** (no target) and a `tests/frontend/` directory exists with `.json` or `.yaml` scenario files, dispatch one `frontend-tester` subagent per scenario file in parallel alongside the unit-test agents.

**Scenario file format** (for `tests/frontend/`):
```json
{
  "url": "https://myapp.com/login",
  "scenario": "Login with valid credentials shows dashboard",
  "steps": [
    "Fill email field with test@example.com",
    "Fill password field with password123",
    "Click Login button"
  ],
  "expected": "Dashboard page loads with user name visible"
}
```

**Result aggregation:** Include frontend results in the summary table alongside the other suites:

```
| Suite           | Status | Passed | Failed | Screenshot |
|-----------------|--------|--------|--------|------------|
| frontend/login  | PASS   | 1      | 0      | /tmp/...   |
| frontend/checkout | FAIL | 0      | 1      | /tmp/...   |
```

## Happy Path Testing (`happy-paths` target)

When `TEST_ARGS` starts with `happy-paths`, run the repo's deterministic happy-path runner directly. No subagent needed. This target only applies when the context file declares such a runner (command and scenario directory); if none is declared, report "no happy-path runner configured in this repo" and skip.

### Execution:
Run the runner command the context file specifies, against its declared scenario directory.

### Result format:
The runner typically outputs a markdown summary table to stdout with pass/fail/error counts per script, followed by a JSON summary in an HTML comment block. Include results in the summary table alongside the other suites.

### When running all tests:
If the context file declares a happy-path scenario directory and it contains scripts, include happy-paths execution alongside the other targets. Run via bash, not subagent.
