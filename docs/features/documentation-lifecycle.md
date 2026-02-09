# Documentation Lifecycle Enforcement

Automated validation and migration system that ensures plans include proper documentation tasks, verifies documentation changes actually happen, and manages the lifecycle from plan completion to feature documentation.

## How It Works

The documentation lifecycle follows a multi-stage enforcement flow:

### 1. Plan Creation - Documentation Section Validation

When `/make-plan` creates a plan, a Stop hook (`validate_documentation_section.py`) validates that the plan includes a properly structured `## Documentation` section:

- **Section must exist** - Cannot skip documentation planning
- **Section must have substance** - No empty placeholders or "TBD"
- **Must include checklist items OR explicit exemption** - Either:
  - At least 2 checklist items (`- [ ]`) specifying docs to create/update
  - "No documentation changes needed" with explanation (50+ chars)

If validation fails, the agent is blocked from proceeding until the section is complete.

### 2. Plan Execution - Documentation Change Verification

After implementing the plan, `validate_docs_changed.py` verifies that promised documentation changes actually happened:

- **Extracts expected doc paths** from the plan's `## Documentation` section (files in backticks)
- **Checks git status** for changed `.md` files (staged, unstaged, untracked)
- **Validates at least one match** - At least one expected doc was created/modified
- **Respects explicit exemptions** - If plan states "no docs needed", validation passes

Exit codes:
- `0` - Validation passed (docs changed as planned)
- `1` - Validation failed (expected docs not changed)
- `2` - File or command error

### 3. Related Documentation Scanning

When code changes occur, `scan_related_docs.py` identifies documentation that may need updates:

**Scanning Strategy**:
- **HIGH Confidence**: Direct file path references (exact match)
- **MED-HIGH Confidence**: Function/class name references from changed files
- **MED Confidence**: Directory or module references
- **LOW Confidence**: Keyword matches (filename without extension)

For each `.md` file in `docs/`, the scanner:
1. Extracts function and class names from changed Python files
2. Searches for file paths, identifiers, directories, and keywords
3. Returns confidence-scored results sorted by relevance

### 4. GitHub Issue Creation

`create_doc_review_issue.py` creates GitHub issues for HIGH and MED-HIGH confidence matches:

- **Groups results by document** for clarity
- **Formats issue with context**:
  - Summary (changed files count, affected docs count)
  - List of changed files
  - Documents requiring review (grouped by confidence)
  - Suggested review actions
- **Uses `gh` CLI** to create issue with `docs-review` label
- **Pipes from scanner**: `scan_related_docs.py --json | create_doc_review_issue.py`

### 5. Plan Migration to Feature Documentation

After work ships, `migrate_completed_plan.py` validates feature docs and cleans up:

**Validation Checks**:
1. Feature doc exists at path specified in plan's `## Documentation` section
2. Feature doc has title (`# Heading`) and substantial content (10+ chars beyond title)
3. Feature is indexed in `docs/features/README.md` (table entry with feature name)
4. Tracking issue can be closed via `gh` CLI

**On Success**:
- Deletes the plan file from `docs/plans/`
- Closes tracking issue with "Plan completed and migrated to feature documentation" comment

**Dry-Run Mode**: Use `--dry-run` to validate without making changes.

## Components

| Path | Purpose |
|------|---------|
| `.claude/hooks/validators/validate_documentation_section.py` | Stop hook validator for plan documentation sections |
| `scripts/validate_docs_changed.py` | Post-implementation validator that docs were actually changed |
| `scripts/scan_related_docs.py` | Confidence-scored scanner for docs referencing changed files |
| `scripts/create_doc_review_issue.py` | GitHub issue creator for HIGH/MED-HIGH doc matches |
| `scripts/migrate_completed_plan.py` | Feature doc validator and plan cleanup tool |

## Usage

### Validate Documentation Section (Plan Creation)

Automatically runs via Stop hook when `/make-plan` finishes. Manual validation:

```bash
# Validate specific plan
uv run .claude/hooks/validators/validate_documentation_section.py docs/plans/my-feature.md

# Auto-detect newest plan file
uv run .claude/hooks/validators/validate_documentation_section.py
```

**Exit codes**:
- `0` - Validation passed, prints JSON `{"result": "continue", "message": "..."}`
- `2` - Validation failed, prints error to stderr, blocks agent

### Validate Docs Were Changed (Post-Implementation)

```bash
# Validate after implementing plan
python scripts/validate_docs_changed.py docs/plans/my-feature.md

# Dry-run mode (show what would be validated)
python scripts/validate_docs_changed.py docs/plans/my-feature.md --dry-run
```

**Exit codes**:
- `0` - Validation passed (docs changed or exempted)
- `1` - Validation failed (expected docs not changed)
- `2` - File or command error

### Scan Related Documentation

```bash
# Scan for docs referencing changed files
python scripts/scan_related_docs.py bridge/telegram_bridge.py tools/search.py

# JSON output (for piping to issue creator)
python scripts/scan_related_docs.py --json bridge/telegram_bridge.py
```

**Output formats**:
- Text: Human-readable grouped by confidence level
- JSON: Structured data with `changed_files`, `docs_directory`, `total_matches`, `results`

### Create Doc Review Issue

```bash
# From stdin (piped from scanner)
python scripts/scan_related_docs.py --json file.py | python scripts/create_doc_review_issue.py

# From file
python scripts/scan_related_docs.py --json file.py > scan.json
python scripts/create_doc_review_issue.py --scan-output scan.json

# Custom title and dry-run
python scripts/create_doc_review_issue.py --scan-output scan.json \
  --title "Review docs after feature X" \
  --dry-run
```

**Requirements**: GitHub CLI (`gh`) must be installed and authenticated.

### Migrate Completed Plan

```bash
# Validate and migrate plan
python scripts/migrate_completed_plan.py docs/plans/my-feature.md

# Dry-run (validate only)
python scripts/migrate_completed_plan.py docs/plans/my-feature.md --dry-run
```

**Exit codes**:
- `0` - Plan successfully migrated (or would be in dry-run)
- `1` - Validation failed, plan not migrated
- `2` - File or command error

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| Plan validation fails with "missing section" | No `## Documentation` section in plan | Add section following template in error message |
| Plan validation fails with "incomplete section" | Section is empty or has only placeholders | Add specific checklist items OR "No documentation changes needed" with explanation |
| Docs validation fails after implementation | Expected docs not created/modified | Create/modify the docs listed in plan, OR add exemption statement to plan |
| Scanner returns no results | Changed files not referenced in docs | This is normal - not all code changes require doc updates |
| Issue creator fails | `gh` CLI not installed/authenticated | Install from https://cli.github.com/ and run `gh auth login` |
| Migration fails - feature doc not found | Doc path in plan doesn't match actual location | Ensure `docs/features/` path in plan matches created doc |
| Migration fails - feature not indexed | Feature missing from README.md table | Add entry to `docs/features/README.md` following format |
| Migration fails - can't close issue | `gh` CLI can't extract issue number | Check that `tracking:` URL in plan frontmatter is valid GitHub issue URL |

## Design Principles

From the plan (`docs/plans/documentation-lifecycle-enforcement.md`):

- **Prevention over detection** - Block incomplete plans at creation time
- **Explicit over implicit** - Require clear documentation intent (tasks or exemption)
- **Confidence-based triage** - Only escalate HIGH/MED-HIGH matches to issues
- **Automated cleanup** - Migrate completed plans automatically, don't accumulate debt
- **Lightweight validation** - Simple pattern matching, no heavy parsing
- **No silent failures** - Validation blocks agent progress, requires explicit fixing
