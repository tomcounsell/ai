---
name: computer-use
description: "Use when driving native macOS apps -- click buttons, type text, screenshot windows -- without moving the user's cursor or stealing focus. Triggered by requests to control desktop apps (Slack, Notes, Xcode, Telegram Desktop, VS Code), automate macOS workflows, or take screenshots of native windows. macOS-only."
allowed-tools: Bash
user-invocable: false
---

# Computer Use (Native Desktop Control)

## Repo Context Probe

If `.claude/skill-context/computer-use.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares the native-desktop-control CLI this skill drives: its commands (list apps/windows, click, type, press key, screenshot, selector resolution), how it is installed and opted into, and its error contract. When the file is absent (the common case in a foreign repo), follow the generic baseline below.

## Generic baseline — desktop control requires a repo-provided CLI

Driving native desktop applications (without moving the user's cursor or stealing focus) is not a capability the bare environment provides — it needs an Accessibility-API driver. This skill does not bundle one; it drives whatever native-control CLI the repo supplies and documents in its context file.

- **Context file present** → use the declared CLI's commands exactly as specified to discover apps/windows, inspect the accessibility tree, and drive the target window.
- **Context file absent** → the desktop-control dependency is unavailable in this repo. Tell the user that native desktop control requires a repo-provided CLI which this repo does not declare, and stop gracefully. Do **not** attempt to install a driver or simulate input through other means.

## When to use

- The agent should drive a native desktop app: Notes, Slack, Telegram Desktop, VS Code, Xcode, Finder, etc.
- Capturing screenshots of native (non-browser) app windows.
- Automating multi-step desktop workflows (open app, type text, click button).
- Inspecting accessibility-tree state of a visible window.

Do **not** use for:
- Browser automation — that's BYOB MCP tools (`mcp__byob__browser_*`) or the Chrome MCP. If the agent asks "click this button on a webpage", route to the browser surface, not here.
- Keyboard/mouse simulation that should move the user's actual cursor — native-control drivers act on windows headlessly via the platform Accessibility API, deliberately leaving the user's pointer alone.

## Platform note

Native desktop control is typically platform-specific (e.g. macOS-only via the Accessibility API). The repo-provided CLI is expected to enforce its own platform constraint and exit cleanly on unsupported hosts; honor whatever the context file declares.
