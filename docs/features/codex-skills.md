# Codex skills

This repository maintains 57 Codex skills corresponding one-for-one to its Claude
skills: 14 project skills and 43 general skills. The Claude sources are unchanged.
Codex entrypoints are independently maintained rewrites, not symlinks to Claude
instructions and not generated search-and-replace output.

## Discovery and installation

- `.agents/skills/`: 14 project skills, discovered when Codex works in this checkout
  or its subdirectories.
- `.agents/skills-global/`: canonical sources for 43 general skills. This directory
  is deliberately outside automatic repository discovery.
- `~/.agents/skills/`: user installation of the general skills, available across repos.

From the checkout containing this change:

```sh
python3 scripts/codex_skills.py check
python3 scripts/codex_skills.py install --dry-run
python3 scripts/codex_skills.py install
```

The installer uses complete copies, so deleting or moving a worktree does not break
installed skills. Run it again after changing the canonical global skills. An alternate
`--target` supports another configured user skill root or an isolated test directory.
The installation state file is `.valor-codex-skills.json` in that target. Existing
unmanaged directories, symlinks, or edited installed skills cause a refusal before
batch writes; unrelated skills are preserved. Reconcile edits in the canonical source
before replacing an installation. Installation is atomic per skill, not a transaction
across the entire collection; an I/O failure can leave a partially updated batch,
which an ordinary rerun reconciles using the saved ownership state. Removed source
skills are not automatically deleted from user storage.

General skills are absent from the repo discovery directory to avoid loading two
copies of every skill when both user and repository locations are enabled. Project
skills are not installed globally. While this change is on a worktree branch, those
14 project skills are available in that worktree; they reach the main checkout when
the branch is integrated. All skills allow implicit selection by default, and can
also be requested by `$name`. No explicit-only policies were carried over.

Codex detects local skill changes automatically; if a skill does not appear, restart
Codex. Discovery rules and the default invocation policy are documented in the
[official skills guide](https://learn.chatgpt.com/docs/build-skills).

## What changed in the workflows

| Area | Codex adaptation |
|---|---|
| Discovery | Native name/description frontmatter with focused triggers; no Claude fork, tool allowlist, or model metadata. |
| Execution | Read the selected procedure and use available tools. Direct implementation works; Claude Task orchestration and PM resumption are not prerequisites. |
| Repo identity | AGENTS.md distinguishes Codex from the Valor application and carries non-obvious operational invariants. |
| Managed SDLC | Preserve real run ownership, dispatch recording, bounded iteration, current-head review, and finalization gates only for an active managed lane. |
| UI and connectors | Discover actual Codex capabilities; preserve authenticated CLI fallbacks. Do not assume BYOB/Claude connector schemas exist. |
| Communication | Read/draft requests do not authorize messages, posts, follows, or unrelated notifications. Explicit sending authorization remains sufficient. |
| Calendar | Infer durations honestly and match generated work logs by identity; overlapping meetings are not overwritten. |
| Design and artifacts | Prefer the installed format-specific skills and user/brand choices; retain useful Marp, Pen, Office, prose, and typography guidance. |
| Agents and scheduling | Native Codex task/automation workflows for Codex requests; preserve Anthropic products when explicitly selected. Never invent an OpenAI equivalent endpoint. |
| Operational tooling | Keep actual Claude application dependencies in setup, hook audits, and fleet updates. Converting skills does not migrate the application's runtime. |
| Helper scripts | Retain ebook cleanup/acquisition helpers; preserve paragraph breaks during page-number removal and redact archive CLI errors that could disclose signed or credential-bearing URLs. |

The house presentation theme is a default for Yudame work, not a universal override
of another client's brand. Audits verify behavior rather than counting test names or
mandating a specific documentation layout. Reviews distinguish actual independence
from a single-reviewer pass. No external services, browser extensions, credentials,
models, or APIs are installed or authorized by skill discovery itself.

## Maintenance and verification

`.agents/skills-manifest.json` maps every source skill to its native target, records
source-file hashes, and enumerates bundled resources. `check` validates one-to-one
coverage, this collection's two-field YAML subset, description uniqueness, required
resources, local Markdown links, global-link portability, Python syntax, and source
drift. It does not make network calls or execute imported helper code. Reference
links inside fenced examples are excluded. Backtick CLI paths and semantic tool
availability still require review; a passing structural check is not live capability
verification.

After a Claude skill changes, review the corresponding Codex procedure and its
resources, then update only that entry's source hashes to acknowledge the reviewed
version. For a new skill, add its matching inventory entry and resource paths.
Do not regenerate native prose from the Claude source: the differences are deliberate.
For substantial native changes, also run OpenAI's skill-creator quick_validate.py on
the changed folders and examine realistic positive and negative trigger examples.

Offline installer tests use temporary directories and no application services:

```sh
python3 -m unittest discover -s scripts/tests -p test_codex_skills.py
```

They exercise dry-run, complete copied resources, idempotent reruns, managed updates,
unmanaged/edited/symlink conflict preservation, missing resources, source drift,
broken/nonportable links, and project/global scope. A real input/output regression also checks that ebook page-number removal preserves
paragraphs. The skill helpers' syntax and archive CLI help are checked without contacting its service. Live deployment, mail,
social publication, managed-agent launches, and other external effects are not run as
part of conversion validation.

## Coverage

Each row links the reviewed source and the maintained Codex procedure. The scope
preserves the repository's original project/global distinction.

| Skill | Scope | Claude source | Codex procedure |
|---|---|---|---|
| ask-me | global | [source](../../.claude/skills-global/ask-me/SKILL.md) | [Codex](../../.agents/skills-global/ask-me/SKILL.md) |
| audit-hooks | global | [source](../../.claude/skills-global/audit-hooks/SKILL.md) | [Codex](../../.agents/skills-global/audit-hooks/SKILL.md) |
| audit-models | global | [source](../../.claude/skills-global/audit-models/SKILL.md) | [Codex](../../.agents/skills-global/audit-models/SKILL.md) |
| audit-skills | global | [source](../../.claude/skills-global/audit-skills/SKILL.md) | [Codex](../../.agents/skills-global/audit-skills/SKILL.md) |
| audit-tools | global | [source](../../.claude/skills-global/audit-tools/SKILL.md) | [Codex](../../.agents/skills-global/audit-tools/SKILL.md) |
| authenticity-pass | project | [source](../../.claude/skills/authenticity-pass/SKILL.md) | [Codex](../../.agents/skills/authenticity-pass/SKILL.md) |
| build-agent | global | [source](../../.claude/skills-global/build-agent/SKILL.md) | [Codex](../../.agents/skills-global/build-agent/SKILL.md) |
| calendar-sync | global | [source](../../.claude/skills-global/calendar-sync/SKILL.md) | [Codex](../../.agents/skills-global/calendar-sync/SKILL.md) |
| checking-system-logs | project | [source](../../.claude/skills/checking-system-logs/SKILL.md) | [Codex](../../.agents/skills/checking-system-logs/SKILL.md) |
| computer-use | global | [source](../../.claude/skills-global/computer-use/SKILL.md) | [Codex](../../.agents/skills-global/computer-use/SKILL.md) |
| cowork | global | [source](../../.claude/skills-global/cowork/SKILL.md) | [Codex](../../.agents/skills-global/cowork/SKILL.md) |
| de-slop | global | [source](../../.claude/skills-global/de-slop/SKILL.md) | [Codex](../../.agents/skills-global/de-slop/SKILL.md) |
| do-build | global | [source](../../.claude/skills-global/do-build/SKILL.md) | [Codex](../../.agents/skills-global/do-build/SKILL.md) |
| do-debrief | global | [source](../../.claude/skills-global/do-debrief/SKILL.md) | [Codex](../../.agents/skills-global/do-debrief/SKILL.md) |
| do-deploy | project | [source](../../.claude/skills/do-deploy/SKILL.md) | [Codex](../../.agents/skills/do-deploy/SKILL.md) |
| do-deploy-example | global | [source](../../.claude/skills-global/do-deploy-example/SKILL.md) | [Codex](../../.agents/skills-global/do-deploy-example/SKILL.md) |
| do-design-audit | global | [source](../../.claude/skills-global/do-design-audit/SKILL.md) | [Codex](../../.agents/skills-global/do-design-audit/SKILL.md) |
| do-design-system | global | [source](../../.claude/skills-global/do-design-system/SKILL.md) | [Codex](../../.agents/skills-global/do-design-system/SKILL.md) |
| do-discover-paths | global | [source](../../.claude/skills-global/do-discover-paths/SKILL.md) | [Codex](../../.agents/skills-global/do-discover-paths/SKILL.md) |
| do-docs | global | [source](../../.claude/skills-global/do-docs/SKILL.md) | [Codex](../../.agents/skills-global/do-docs/SKILL.md) |
| do-integration-audit | global | [source](../../.claude/skills-global/do-integration-audit/SKILL.md) | [Codex](../../.agents/skills-global/do-integration-audit/SKILL.md) |
| do-investigation-issue | global | [source](../../.claude/skills-global/do-investigation-issue/SKILL.md) | [Codex](../../.agents/skills-global/do-investigation-issue/SKILL.md) |
| do-issue | global | [source](../../.claude/skills-global/do-issue/SKILL.md) | [Codex](../../.agents/skills-global/do-issue/SKILL.md) |
| do-merge | global | [source](../../.claude/skills-global/do-merge/SKILL.md) | [Codex](../../.agents/skills-global/do-merge/SKILL.md) |
| do-patch | global | [source](../../.claude/skills-global/do-patch/SKILL.md) | [Codex](../../.agents/skills-global/do-patch/SKILL.md) |
| do-plan | global | [source](../../.claude/skills-global/do-plan/SKILL.md) | [Codex](../../.agents/skills-global/do-plan/SKILL.md) |
| do-plan-critique | global | [source](../../.claude/skills-global/do-plan-critique/SKILL.md) | [Codex](../../.agents/skills-global/do-plan-critique/SKILL.md) |
| do-pr-review | global | [source](../../.claude/skills-global/do-pr-review/SKILL.md) | [Codex](../../.agents/skills-global/do-pr-review/SKILL.md) |
| do-presentation | global | [source](../../.claude/skills-global/do-presentation/SKILL.md) | [Codex](../../.agents/skills-global/do-presentation/SKILL.md) |
| do-sdlc | global | [source](../../.claude/skills-global/do-sdlc/SKILL.md) | [Codex](../../.agents/skills-global/do-sdlc/SKILL.md) |
| do-test | global | [source](../../.claude/skills-global/do-test/SKILL.md) | [Codex](../../.agents/skills-global/do-test/SKILL.md) |
| do-voice-recording | global | [source](../../.claude/skills-global/do-voice-recording/SKILL.md) | [Codex](../../.agents/skills-global/do-voice-recording/SKILL.md) |
| ebook-ingest | project | [source](../../.claude/skills/ebook-ingest/SKILL.md) | [Codex](../../.agents/skills/ebook-ingest/SKILL.md) |
| email | global | [source](../../.claude/skills-global/email/SKILL.md) | [Codex](../../.agents/skills-global/email/SKILL.md) |
| frontend-design | global | [source](../../.claude/skills-global/frontend-design/SKILL.md) | [Codex](../../.agents/skills-global/frontend-design/SKILL.md) |
| google-workspace | global | [source](../../.claude/skills-global/google-workspace/SKILL.md) | [Codex](../../.agents/skills-global/google-workspace/SKILL.md) |
| grill-me | global | [source](../../.claude/skills-global/grill-me/SKILL.md) | [Codex](../../.agents/skills-global/grill-me/SKILL.md) |
| imagine-agent | global | [source](../../.claude/skills-global/imagine-agent/SKILL.md) | [Codex](../../.agents/skills-global/imagine-agent/SKILL.md) |
| linkedin | project | [source](../../.claude/skills/linkedin/SKILL.md) | [Codex](../../.agents/skills/linkedin/SKILL.md) |
| mermaid-render | global | [source](../../.claude/skills-global/mermaid-render/SKILL.md) | [Codex](../../.agents/skills-global/mermaid-render/SKILL.md) |
| new-audit-skill | global | [source](../../.claude/skills-global/new-audit-skill/SKILL.md) | [Codex](../../.agents/skills-global/new-audit-skill/SKILL.md) |
| new-skill | global | [source](../../.claude/skills-global/new-skill/SKILL.md) | [Codex](../../.agents/skills-global/new-skill/SKILL.md) |
| officecli | project | [source](../../.claude/skills/officecli/SKILL.md) | [Codex](../../.agents/skills/officecli/SKILL.md) |
| ontologies | global | [source](../../.claude/skills-global/ontologies/SKILL.md) | [Codex](../../.agents/skills-global/ontologies/SKILL.md) |
| pen-design | global | [source](../../.claude/skills-global/pen-design/SKILL.md) | [Codex](../../.agents/skills-global/pen-design/SKILL.md) |
| present | global | [source](../../.claude/skills-global/present/SKILL.md) | [Codex](../../.agents/skills-global/present/SKILL.md) |
| prime | project | [source](../../.claude/skills/prime/SKILL.md) | [Codex](../../.agents/skills/prime/SKILL.md) |
| reading-sms-messages | project | [source](../../.claude/skills/reading-sms-messages/SKILL.md) | [Codex](../../.agents/skills/reading-sms-messages/SKILL.md) |
| reclassify | global | [source](../../.claude/skills-global/reclassify/SKILL.md) | [Codex](../../.agents/skills-global/reclassify/SKILL.md) |
| sdlc | project | [source](../../.claude/skills/sdlc/SKILL.md) | [Codex](../../.agents/skills/sdlc/SKILL.md) |
| sentry | project | [source](../../.claude/skills/sentry/SKILL.md) | [Codex](../../.agents/skills/sentry/SKILL.md) |
| setup | project | [source](../../.claude/skills/setup/SKILL.md) | [Codex](../../.agents/skills/setup/SKILL.md) |
| telegram | project | [source](../../.claude/skills/telegram/SKILL.md) | [Codex](../../.agents/skills/telegram/SKILL.md) |
| update | project | [source](../../.claude/skills/update/SKILL.md) | [Codex](../../.agents/skills/update/SKILL.md) |
| weekly-review | global | [source](../../.claude/skills-global/weekly-review/SKILL.md) | [Codex](../../.agents/skills-global/weekly-review/SKILL.md) |
| x-com | project | [source](../../.claude/skills/x-com/SKILL.md) | [Codex](../../.agents/skills/x-com/SKILL.md) |
| zoom-out | global | [source](../../.claude/skills-global/zoom-out/SKILL.md) | [Codex](../../.agents/skills-global/zoom-out/SKILL.md) |
