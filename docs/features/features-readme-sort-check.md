# Features README Alphabetical Sort Check

Automated enforcement of alphabetical ordering in the `docs/features/README.md` feature index table. Prevents out-of-order entries from being committed and provides a one-command fix.

## Problem

The feature index table in `docs/features/README.md` contains the instruction "Keep entries sorted alphabetically by feature name" but entries were routinely added out of order by both humans and agents. This caused review nits (e.g., PR #311) and merge conflicts when multiple PRs touched the same file.

## Solution

A Python validator script with two modes:

- **`--check`** (default): Validates alphabetical sort order. Exits 0 if sorted, exits 2 with a helpful error message showing which entries are out of order.
- **`--fix`**: Re-sorts the table in place and exits 0.

The validator is registered as a Claude Code PostToolUse hook for both Write and Edit operations, firing automatically whenever `docs/features/README.md` is modified.

## How It Works

### Table Parsing

The script parses the markdown content between the `## Features` and `## Adding New Entries` headers. It identifies the table by looking for the header row (pipe-delimited columns) and separator row (dashes), then collects all subsequent data rows.

### Name Extraction

Feature names are extracted from the `[Feature Name](filename.md)` link syntax in each table row using regex. Rows without link syntax are skipped (they don't participate in sort validation).

### Sort Comparison

Alphabetical ordering uses case-insensitive comparison (`str.lower()`). This correctly handles mixed-case names like "Do Test" sorting before "do-patch Skill", and "SDK Modernization" sorting before "SDLC Enforcement".

### Hook Integration

The validator reads Claude Code's stdin JSON context to determine which file was modified. If the file path does not contain `docs/features/README.md`, the hook exits 0 immediately (pass-through). Uses `select.select()` with a 0.1s timeout to avoid blocking when no stdin is available (CLI invocation).

## Usage

### Check sort order
```bash
python .claude/hooks/validators/validate_features_readme_sort.py --check docs/features/README.md
```

### Auto-sort the table
```bash
python .claude/hooks/validators/validate_features_readme_sort.py --fix docs/features/README.md
```

### As a Claude Code hook
Registered automatically in `.claude/settings.json` under PostToolUse for Write and Edit matchers. No manual invocation needed -- the hook fires whenever the README is modified.

## Files

| File | Purpose |
|------|---------|
| `.claude/hooks/validators/validate_features_readme_sort.py` | Sort validator script |
| `.claude/settings.json` | Hook registration (PostToolUse for Write and Edit) |
| `tests/test_features_readme_sort.py` | 27 unit and integration tests |

## Edge Cases

- **Empty table** (0 rows): passes validation
- **Single row**: passes validation
- **Missing `## Features` header**: passes (nothing to validate)
- **Rows without link syntax**: skipped in sort validation
- **Malformed table**: warns if `## Features` exists but no table rows found

## Design Decisions

- **Single script, two modes**: Keeps the codebase simple. One file handles both validation and fixing.
- **PostToolUse hook (not pre-commit)**: Catches issues at edit time rather than commit time, providing faster feedback within Claude Code sessions.
- **Case-insensitive sort**: Matches natural reading order and avoids surprising behavior with mixed-case feature names.
- **`select()`-based stdin**: Non-blocking stdin reading allows the script to work both as a hook (stdin piped) and as a standalone CLI tool.
