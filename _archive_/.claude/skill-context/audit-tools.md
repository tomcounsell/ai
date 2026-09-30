# audit-tools context — this repo (ai)

This repo's conventions for the `audit-tools` checks. The global skill body runs a generic
baseline (a tool is cli-registered when it has any `pyproject.toml [project.scripts]` entry;
scaffold missing structure by hand); this file supplies the repo-specific names.

## CLI-naming convention (`[cli-registered]` check)

Tools in this repo are exposed as console scripts under the **`valor-*`** prefix in
`pyproject.toml [project.scripts]` (e.g. `valor-sms-reader`, `valor-tts`, `valor-email`). When
running the `[cli-registered]` check, confirm the tool has a `valor-<name>` entry-point, not just
any script name.

## Scaffolding missing structure

Use the `/new-valor-skill` (now `/new-skill`) scaffolding command to generate missing structure
(manifest, README sections, tests dir) for incomplete tools, rather than hand-creating files.

Everything else in the audit (STANDARD.md structure checks, test-coverage, CLI-help quality,
doc completeness) is generic and runs from the skill body unchanged.
