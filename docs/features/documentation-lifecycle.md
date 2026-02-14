# Documentation Lifecycle Enforcement

Automated validation and enforcement system that ensures documentation is a first-class deliverable throughout the plan-build-ship lifecycle.

## How It Works

The documentation lifecycle has three enforcement gates plus a cascade system:

### Gate 1: Plan Creation (PostToolUse Hook)

When a plan document is written to `docs/plans/`, a PostToolUse hook validates the `## Documentation` section:

- **Trigger**: Any `Write` tool call targeting `docs/plans/*.md`
- **Validator**: `.claude/hooks/validators/validate_documentation_section.py`
- **Rules**:
  - Section must exist with content below it
  - Must contain at least one checkbox task (`- [ ]`) with a doc path, OR
  - Explicit exemption phrase ("No documentation changes needed") with 50+ char justification
  - Empty or missing sections block the write

**Example valid section:**
```markdown
## Documentation
- [ ] Create `docs/features/my-feature.md` describing the new capability
- [ ] Add entry to `docs/features/README.md` index table
```

**Example valid exemption:**
```markdown
## Documentation
No documentation changes needed — this is a one-line config fix with no user-facing or architectural impact.
```

### Gate 2: Build Completion (Post-Build Validation)

After the build agent creates a PR, a validation script checks that promised docs were actually delivered:

- **Script**: `scripts/validate_docs_changed.py`
- **Invoked by**: Build skill (Step 7.5 in `.claude/skills/build/SKILL.md`)
- **Phase 1 — Diff Check**: Parses the plan's `## Documentation` section, extracts expected doc paths, and verifies they appear in `git diff` against the base branch
- **Phase 2 — Stale Marker Scan**: Scans all changed `.md` files for stale markers (`DEPRECATED`, `LEGACY`, `OBSOLETE`, `TODO: remove`, `FIXME: update`)
- **Flags**: `--dry-run` (report only), `--base-branch` (compare target, defaults to `main`)
- **Exit codes**: 0 = pass, 1 = missing docs (hard fail), 2 = stale markers found (warning)

### Gate 3: Plan Migration

After work ships, `scripts/migrate_completed_plan.py` validates feature docs and cleans up:

- Validates that the feature doc exists under `docs/features/`
- Checks the feature doc has substantive content (200+ chars excluding frontmatter)
- Verifies the feature is indexed in `docs/features/README.md`
- Only then deletes the plan and closes the tracking GitHub issue via `gh` CLI

**Dry-Run Mode**: Use `--dry-run` to validate without making changes.

### Documentation Cascade (`/update-docs`)

A slash command (`.claude/commands/update-docs.md`) automates documentation updates after code changes:

- **Trigger**: Invoked manually or as Step 7.6 in the build skill
- **Phase 1 — Explore**: Two parallel agents examine recent code changes and inventory all existing docs
- **Phase 2 — Triage**: Cross-reference changes against docs to identify what needs updates, creation, or deletion
- **Phase 3 — Edit**: Surgical, targeted edits to affected docs only (no full rewrites)
- **Phase 4 — Verify**: Checks for broken links, verifies feature index, runs `validate_docs_changed.py` in dry-run mode, and commits all doc changes
- **Scope**: CLAUDE.md, feature docs, plans, guides, reference docs, architecture docs, README files

## Components

| Component | Path | Purpose |
|-----------|------|---------|
| Plan validator | `.claude/hooks/validators/validate_documentation_section.py` | Gate 1: Block plans without proper doc section |
| Hook wiring | `.claude/settings.json` (PostToolUse on Write) | Connects validator to file writes |
| Build validator | `scripts/validate_docs_changed.py` | Gate 2: Verify docs delivered post-build |
| Build integration | `.claude/skills/build/SKILL.md` (Steps 7.5 + 7.6) | Invokes validator then cascade in build flow |
| Migration validator | `scripts/migrate_completed_plan.py` | Gate 3: Block migration without feature doc |
| Cascade command | `.claude/commands/update-docs.md` | Automated doc updates after code changes |

## Usage

### Validate Documentation Section (Plan Creation)

Runs automatically via PostToolUse hook when writing plan files. The hook reads from stdin and validates the Write tool input.

### Validate Docs Were Changed (Post-Build)

```bash
# Validate after implementing plan
python scripts/validate_docs_changed.py docs/plans/my-feature.md

# Dry-run mode (show what would be validated)
python scripts/validate_docs_changed.py docs/plans/my-feature.md --dry-run

# Compare against different base branch
python scripts/validate_docs_changed.py docs/plans/my-feature.md --base-branch develop
```

### Migrate Completed Plan

```bash
# Validate and migrate plan
python scripts/migrate_completed_plan.py docs/plans/my-feature.md

# Dry-run (validate only)
python scripts/migrate_completed_plan.py docs/plans/my-feature.md --dry-run

# Skip issue closing
python scripts/migrate_completed_plan.py docs/plans/my-feature.md --skip-issue
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| Plan validation blocks write | Missing or empty `## Documentation` section | Add checklist items with doc paths OR explicit exemption with 50+ char justification |
| Build validation fails (exit 1) | Expected docs not created/modified | Create/modify the docs listed in plan's Documentation section |
| Build validation warns (exit 2) | Stale markers found in changed docs | Remove DEPRECATED/LEGACY/OBSOLETE markers from documentation |
| Migration fails - doc not found | Feature doc path doesn't match plan | Ensure `docs/features/` path in plan matches created doc |
| Migration fails - not indexed | Feature missing from README.md | Add entry to `docs/features/README.md` table |
| Migration fails - can't close issue | `gh` CLI issue | Verify `tracking:` URL in plan frontmatter is valid |

## Limitations

- Gate 1 validates Markdown structure, not content quality
- Gate 2 diff check relies on file paths matching between plan and actual files
- Stale marker scan uses simple string matching, not semantic analysis
- Migration validator checks existence and length, not comprehensiveness

## Related

- [Session Isolation](session-isolation.md) — Task list and worktree scoping
- [Bridge Self-Healing](bridge-self-healing.md) — Another lifecycle enforcement pattern
- Plan requirements in `CLAUDE.md` — Documents the three required plan sections
