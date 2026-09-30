---
name: audit-skills
description: "Audit Codex skill discovery, metadata, linked resources, runtime portability, overlapping triggers, and workflow quality."
---

# Audit Skills

Inventory `.agents/skills` and any user skill roots relevant to the request. In this repo run `python3 scripts/codex_skills.py check` for source coverage, metadata, bundled-file integrity, local links, and recorded Claude-source drift. This command is read-only.
For each skill assess whether the description routes the right requests, the body preserves scope, referenced tools exist or have honest fallbacks, and conditional detail is progressively disclosed. Check for copied Claude tool calls, hook assumptions, unsupported metadata, ambiguous writes, duplicate discovery, orphan resources, and instructions that stop authorized work unnecessarily.
For architecture review, consider keep, merge, split, script, workflow, or retire, with concrete user requests showing why. Do not assign Anthropic model tiers to Codex skills. Separate machine-detectable failures from semantic judgments; verify each finding against the current file.
Report severity, evidence, consequence, and recommended change. Apply requested fixes within scope; never silently rewrite every skill from its Claude source or sync Anthropic templates over the Codex collection.
