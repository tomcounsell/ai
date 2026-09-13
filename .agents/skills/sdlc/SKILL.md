---
name: sdlc
description: "Route one stage of an existing Valor managed SDLC lane when a single-stage router invocation is requested."
---

# Sdlc

This is the project-specific single-stage counterpart of $do-sdlc. Read that skill and `docs/sdlc/do-sdlc.md`, resolve the issue and existing run identity, call the documented router, record a dispatch when it returns one, and perform that stage's Codex skill procedure.
Return after one stage for an explicit single-stage invocation. A normal user request for end-to-end implementation belongs to $do-sdlc and continues within the current task; do not wait for a nonexistent Claude PM to resume Codex.
Preserve stage prerequisites, lease ownership, verdict freshness, and the documented terminal/blocked/error distinctions. Use a supplied lane branch/identity where required, otherwise honor the user's worktree and Codex branch convention. Do not start a second writer on an artifact or fabricate a managed session just because the CLI exists.
