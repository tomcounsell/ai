# Browser Automation

Browser automation capability for web testing, form filling, screenshots, and data extraction.

## Current Implementation

**Tool**: [agent-browser](https://github.com/vercel-labs/agent-browser) (Vercel Labs)

A CLI-based browser automation tool optimized for AI agents. Uses Playwright under the hood.

## Installation

```bash
npm install -g agent-browser
agent-browser install  # Install browser binaries
```

## Core Workflow

The fundamental pattern for browser automation:

```
1. Open URL
2. Snapshot (get interactive elements)
3. Interact using refs from snapshot
4. Re-snapshot after page changes
5. Repeat 3-4 until task complete
6. Close browser
```

### Example Session

```bash
agent-browser open https://example.com/login
agent-browser snapshot -i
# Output: textbox "Email" [ref=e1], textbox "Password" [ref=e2], button "Submit" [ref=e3]

agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i  # Check result
agent-browser close
```

## Common Workflows

### 1. Form Testing

Test form submission and validation:

```bash
# Navigate to form
agent-browser open $URL
agent-browser snapshot -i

# Fill required fields
agent-browser fill @e1 "test input"
agent-browser click @submit_button

# Verify success/error state
agent-browser snapshot -i
agent-browser get text ".success-message"
```

### 2. Authentication Flow

Login and save session for reuse:

```bash
# Initial login
agent-browser open https://app.example.com/login
agent-browser snapshot -i
agent-browser fill @email "user@example.com"
agent-browser fill @password "secret"
agent-browser click @submit
agent-browser wait --url "**/dashboard"

# Save authenticated state
agent-browser state save auth.json

# Later: restore session
agent-browser state load auth.json
agent-browser open https://app.example.com/dashboard
```

### 3. Visual Regression

Capture screenshots for comparison:

```bash
agent-browser open $URL
agent-browser wait --load networkidle
agent-browser screenshot ./screenshots/page.png --full
```

### 4. Data Extraction

Scrape content from pages:

```bash
agent-browser open $URL
agent-browser snapshot -i
agent-browser get text @e1
agent-browser get html ".content"
agent-browser get attr @link href
```

### 5. Multi-Page Navigation

Navigate through a flow:

```bash
agent-browser open https://shop.example.com
agent-browser snapshot -i
agent-browser click @product_link
agent-browser wait --load networkidle
agent-browser snapshot -i
agent-browser click @add_to_cart
agent-browser click @checkout
```

### 6. Debug Session

When things go wrong:

```bash
agent-browser open $URL --headed  # Show browser window
agent-browser snapshot -i
agent-browser highlight @e1       # Highlight element
agent-browser console             # View console logs
agent-browser errors              # View page errors
```

## Command Reference

### Navigation
| Command | Description |
|---------|-------------|
| `open <url>` | Navigate to URL |
| `back` | Go back |
| `forward` | Go forward |
| `reload` | Reload page |
| `close` | Close browser |

### Inspection
| Command | Description |
|---------|-------------|
| `snapshot -i` | Interactive elements with refs (recommended) |
| `snapshot -c` | Compact output |
| `snapshot -d 3` | Limit depth |
| `get text @e1` | Get element text |
| `get html @e1` | Get innerHTML |
| `get url` | Get current URL |
| `get title` | Get page title |

### Interaction
| Command | Description |
|---------|-------------|
| `click @e1` | Click element |
| `fill @e1 "text"` | Clear and type |
| `type @e1 "text"` | Type without clearing |
| `press Enter` | Press key |
| `hover @e1` | Hover element |
| `check @e1` | Check checkbox |
| `select @e1 "value"` | Select dropdown |
| `scroll down 500` | Scroll page |

### Waiting
| Command | Description |
|---------|-------------|
| `wait @e1` | Wait for element |
| `wait 2000` | Wait milliseconds |
| `wait --text "Success"` | Wait for text |
| `wait --url "**/page"` | Wait for URL pattern |
| `wait --load networkidle` | Wait for network idle |

### Capture
| Command | Description |
|---------|-------------|
| `screenshot` | Screenshot to stdout |
| `screenshot path.png` | Save to file |
| `screenshot --full` | Full page |
| `pdf output.pdf` | Save as PDF |
| `record start demo.webm` | Start video |
| `record stop` | Stop video |

## Session Management

Use named sessions for parallel browser instances:

```bash
agent-browser --session test1 open site-a.com
agent-browser --session test2 open site-b.com
agent-browser session list
```

## Best Practices

1. **Always use `snapshot -i`** - The `-i` flag filters to interactive elements only, reducing noise
2. **Re-snapshot after navigation** - Page state changes require fresh refs
3. **Use `wait` appropriately** - Wait for elements/network before interacting
4. **Save auth state** - Don't re-login for every test
5. **Use `--headed` for debugging** - See what the browser sees
6. **Close browser when done** - Release resources

## Error Handling

Common issues and solutions:

| Issue | Solution |
|-------|----------|
| Element not found | Re-snapshot, check if page changed |
| Timeout | Increase wait time, check network |
| Stale ref | Re-snapshot after DOM changes |
| Auth expired | Re-run login flow, save new state |

## Future Considerations

This abstraction layer allows swapping the underlying tool. Potential alternatives:
- Playwright CLI directly
- Puppeteer-based tools
- Selenium WebDriver
- Custom browser automation

The workflow patterns remain consistent regardless of implementation.
