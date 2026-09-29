---
name: computer-use
description: "Drive native macOS apps: click, type, screenshot windows without stealing focus. Use for desktop apps (Slack, Notes, Xcode), macOS workflow automation, or native window screenshots. macOS-only."
allowed-tools: Bash
user-invocable: false
---

# Computer Use (Native Desktop Control)

If `.claude/skill-context/computer-use.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

**Objective:** drive native desktop apps (Notes, Slack, Xcode, Finder...) and capture their
windows without moving the user's cursor or stealing focus. That needs an
Accessibility-API driver, which this skill does not bundle: it drives the CLI a repo
declares in its context file (commands, setup, error contract, platform limits).

- **Context file present:** use the declared CLI exactly as specified.
- **Context file absent:** tell the user native desktop control needs a repo-provided CLI
  this repo does not declare, and stop. Do not install a driver or simulate input by
  other means.

Not for web pages: route browser work to the browser tools (e.g. BYOB MCP
`mcp__byob__browser_*` or a Chrome MCP).
