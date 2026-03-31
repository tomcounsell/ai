---
name: WebMCP purpose is full agent podcast management
description: Issue 162 WebMCP integration is for AI agents to browse and manage podcasts across the entire production system, not just read-only content
type: project
---

WebMCP (issue #162) primary purpose: enable AI agents (like Claude) to browse the site AND create/manage podcasts across the entire podcast management system.

**Why:** The podcast production pipeline has 12 phases with multiple services, and agents need programmatic access to orchestrate production — not just read published content. The current workflow requires agents to scrape HTML or use backend MCP servers separately.

**How to apply:** The plan should include mutation tools (create episode, advance workflow, retry research), auth-gated page support (workflow editor), and full pipeline management — not just read-only resources on public pages. The "read-only v1" scope in the initial plan is too narrow.
