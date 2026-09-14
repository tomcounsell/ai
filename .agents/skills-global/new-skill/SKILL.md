---
name: new-skill
description: "Create or update a reusable Codex skill with precise discovery metadata, progressive references, and tested helpers."
---

# New Skill

Use the installed skill-creator skill when available and follow current official Codex skill guidance. Choose a lowercase hyphenated name matching its folder. For this repository use `.agents/skills/<name>/SKILL.md` for project scope or `.agents/skills-global/<name>/SKILL.md` for global scope; for a personal skill use the user's configured skill location, checking current discovery conventions first.
Write YAML frontmatter with `name` and a concise `description` stating the capability and when it applies. Automatic selection is the default. Add `agents/openai.yaml` only for useful UI metadata or an explicitly requested invocation policy; do not carry over Claude-only allowed-tools, context/fork, or model fields.
Keep instructions focused on non-obvious decisions, real invariants, and a clear deliverable. Put substantial conditional detail in linked references and deterministic reusable work in scripts. Use actual Codex tools, not fictional slash-command execution. Preserve user intent and existing authorization; avoid ritual approvals, unnecessary subagents, and global policy copied into every skill.
Validate frontmatter, naming, linked resources, scripts, and realistic routing examples. When capturing a workflow from the current task, remove one-off identifiers, credentials, obsolete attempts, and generic advice. In this repo, register its scope, target, and bundled resource paths in `.agents/skills-manifest.json`. For a Codex-only skill use `"source": null` and `"source_files": {}`; a converted skill keeps its actual Claude source and hashes. Global sources belong in `.agents/skills-global/`, project skills in `.agents/skills/`. Run `scripts/codex_skills.py check` with the repository Python environment (PyYAML required), then `install` for global skills. Standard YAML descriptions and optional metadata are supported.
