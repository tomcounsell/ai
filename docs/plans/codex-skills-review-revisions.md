---
status: Ready
type: bug
appetite: Small
tracking: null
---

# Codex skills review revisions

## Problem

Review of commit 858b7ca4d found that native skill creation cannot satisfy the
installer's mandatory Claude-source mapping, ordinary YAML descriptions are rejected,
and the Mermaid conversion omitted Excalidraw inputs and hand-drawn export.
This task uses the user's request and review findings as its acceptance contract;
no synthetic tracking issue or managed SDLC lane is needed.

## Solution and scope

- Permit explicit `source: null` with empty source hashes for Codex-only skills,
  while preserving complete coverage and drift checks for converted Claude skills.
- Parse frontmatter with PyYAML (already a repository dependency), accepting standard
  YAML strings and optional metadata while validating required name/description values.
- Document native registration and interpreter requirements in the authoring skill
  and feature guide.
- Restore `.excalidraw` input, hand-drawn Mermaid conversion, batch isolation, and
  visual verification through real available CLI/browser capabilities.

## Tasks and verification

1. Update scripts/codex_skills.py and add regression coverage for native-only global
   installation, source drift, standard YAML forms, and invalid required fields.
2. Update new-skill and mermaid-render procedures and the feature documentation.
3. Run the offline filesystem tests, complete collection check, OpenAI skill validator,
   and scoped Ruff checks. Inspect CLI availability; do not claim a rendered output
   without actually rendering it.
4. Commit, refresh the managed installed copies, and request an independent review
   against the original request and these findings. Leave merge to the user.

## Risks and rollback

PyYAML requires the repository environment (or another interpreter with PyYAML).
Native-only entries must not hide newly added or removed Claude sources. Revert the
revision commit and reinstall to roll back; preserve locally edited installed skills.

## Success criteria

Native-only additions install without a fake Claude source; ordinary valid YAML
passes; bad required metadata and source drift still fail; both diagram input types
have usable procedures; the fresh review has no unresolved actionable findings.

## Validation evidence

- 14 offline tests pass, including native-only installation and YAML variants.
- All 57 skills pass collection validation; scoped Ruff checks pass.
- The installed excalidraw-export CLI produced an 800×440 PNG from a real scene;
  visual inspection confirmed the hand-drawn rectangle and readable label.
- Browser-based Mermaid import has not been exercised in this revision.
- Independent review requested after implementation; findings will be addressed
  before handoff to the user.
