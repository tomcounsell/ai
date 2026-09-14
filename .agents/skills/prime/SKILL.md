---
name: prime
description: "Orient to the Valor AI repository, its bridge/worker architecture, tools, and operational invariants."
---

# Prime

Read AGENTS.md, the relevant feature index, and current source rather than treating an old architecture sketch as exhaustive. Valor is the system being developed; Codex is its collaborator, not the Valor persona.
Trace Telegram → Telethon bridge → Redis AgentSession queue → standalone worker → headless session runner. The bridge handles I/O and nudges; the worker executes sessions. The reflection scheduler is a separate supervised process. Confirm details in `bridge/`, `worker/`, `agent/`, and `reflections/`.
Use `docs/tools-reference.md` and each CLI's help to find callable capabilities. New functions must be wired into an actual entrypoint before an agent can use them. Codex procedures live in `.agents/skills`; `.claude/skills*`, hooks, and Claude subprocesses remain infrastructure of the target application.
Explain the relevant flow and likely change points, including pinned Python, isolated tests, ORM-only production data access, secrets in the external vault, and service ownership. A request for orientation does not authorize restarting services or pushing changes.
