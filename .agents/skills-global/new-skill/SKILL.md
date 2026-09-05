---
name: new-skill
description: "Create or update a reusable Codex skill with precise discovery metadata, progressive references, and tested helpers."
---

# New Skill

Use the installed skill-creator skill when available and follow current official Codex skill guidance. Choose a lowercase hyphenated name matching its folder. For this repository create `.agents/skills/<name>/SKILL.md`; for a personal skill use the user's configured skill location, checking current discovery conventions first.
Write YAML frontmatter with `name` and a concise `description` stating the capability and when it applies. Automatic selection is the default. Add `agents/openai.yaml` only for useful UI metadata or an explicitly requested invocation policy; do not carry over Claude-only allowed-tools, context/fork, or model fields.
Keep instructions focused on non-obvious decisions, real invariants, and a clear deliverable. Put substantial conditional detail in linked references and deterministic reusable work in scripts. Use actual Codex tools, not fictional slash-command execution. Preserve user intent and existing authorization; avoid ritual approvals, unnecessary subagents, and global policy copied into every skill.
Validate frontmatter, naming, linked resources, scripts, and realistic routing examples. When capturing a workflow from the current task, remove one-off identifiers, credentials, obsolete attempts, and generic advice. Register its source/scope in the repo's Codex inventory and validate discovery.
