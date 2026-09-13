---
name: computer-use
description: "Inspect and operate native desktop apps, windows, and accessibility controls when the task requires UI interaction."
---

# Computer Use

Prefer a purpose-built connector, API, or CLI when it can perform the requested operation. For native UI work, use the available Codex computer-use tool and read its initialization documentation before interacting. Select the named app, inspect its current state, act on observed controls, and verify the result. For browser work use that tool's browser surface instead of treating a web page as a native window.
Do not assume Claude browser tools, BYOB handles, Accessibility drivers, or app permissions exist. A repository-provided native-control CLI is an alternative only when installed and documented; inspect its help and error contract first.
Refresh the UI after navigation or mutations so stale selectors cannot target the wrong control. Keep actions scoped to the user's request and preserve account identity. Report a missing driver or permission with the work that can still be completed. Never claim screenshots or UI evidence you did not obtain.
