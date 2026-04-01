---
name: do-xref
description: "Cross-reference the work vault knowledge base with project docs/. Audits coverage gaps and adds bidirectional links. Triggered by 'cross-reference docs', 'xref', 'sync knowledge base', or 'do-xref'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, Task
context: fork
---

# Cross-Reference Knowledge Base — Skill

Audit and maintain bidirectional cross-references between the work vault and the project's `docs/` directory. The work vault holds business context (product overviews, roadmap, stakeholder notes); `docs/` holds technical implementation details. Both benefit from linking to each other.

## When to Use

- Periodic alignment: ensure business docs and technical docs reference each other
- After adding a new feature doc or work vault page
- When someone asks "where is the business context for X?" or "where are the technical docs for Y?"
- Invoked by `/do-xref` or when user asks to cross-reference, sync knowledge base, or link docs

## Invocation

```
/do-xref [--audit-only] [--vault-path PATH] [--docs-path PATH]
```

- `--audit-only`: Report gaps without making changes
- `--vault-path`: Override vault location (default: auto-detected)
- `--docs-path`: Override docs location (default: `docs/` in current repo)

## Path Resolution

Resolve paths from the shared project config at `~/Desktop/Valor/projects.json`. This file maps every project to its `working_directory` and `knowledge_base`.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
DOCS_PATH=$REPO_ROOT/docs/
```

### Lookup from projects.json

1. Read `~/Desktop/Valor/projects.json`
2. Find the project entry whose `working_directory` (after expanding `~`) matches `$REPO_ROOT`
3. Read the `knowledge_base` field — this is `VAULT_PATH`

```python
# Pseudocode
for project in projects["projects"].values():
    if expand(project["working_directory"]) == REPO_ROOT:
        VAULT_PATH = expand(project["knowledge_base"])
        break
```

### Fallback (no config match)

If no project matches in `projects.json`, fall back to filesystem discovery:
```bash
REPO_DIR=$(basename $REPO_ROOT)
VAULT_PATH=$(find ~/work-vault -maxdepth 1 -iname "$REPO_DIR" -type d | head -1)
```

### Stop conditions

- If no vault folder is found by either method, report: "No knowledge base found for project. Check `knowledge_base` in ~/Desktop/Valor/projects.json." and stop.
- If `docs/` doesn't exist in the repo, report it and stop.
- If the project has no `knowledge_base` field in projects.json, report it and stop.

---

## Step 1: Inventory Both Locations (Parallel)

Launch two agents in parallel.

### Agent A — Vault Inventory

```
Inventory all markdown files in {VAULT_PATH}.

For each file:
1. Read the full file
2. Extract: title, key topics, any existing cross-references to docs/ or the codebase
3. Identify which apps or features it describes
4. Note any links already present (internal or external)

Produce a structured list:
  <filepath> | <title> | <topics> | <existing xrefs to docs/> | <app/feature>

Skip files that are purely personal (no relation to the codebase).
```

### Agent B — Docs Inventory

```
Inventory documentation in {DOCS_PATH}.

Scan these locations:
- docs/features/*.md
- docs/guides/*.md
- docs/reference/*.md
- docs/specs/*.md
- docs/*.md (top-level)
- CLAUDE.md

For each file:
1. Read headings and first ~20 lines
2. Extract: title, key topics, which app/feature it covers
3. Check for any existing "Business Context" or "See also" links to the work vault
4. Note any existing cross-references to other docs

Produce a structured list:
  <filepath> | <title> | <topics> | <has vault xref: yes/no> | <app/feature>

Do NOT read docs/plans/ — plans are forward-looking and don't need vault xrefs.
```

Wait for both agents to complete.

---

## Step 2: Build the Cross-Reference Map

Using both inventories, build a mapping of related documents across the two locations.

### Matching Rules (in priority order)

1. **Explicit app match**: Vault file mentions a specific app directory and docs file covers that app
2. **Topic overlap**: Shared keywords, technologies, or feature names
3. **Feature alignment**: Vault describes a product area, docs describe its implementation

### Discovery Process

Do NOT rely on hardcoded mappings. Instead:

1. For each vault file, extract its key topics and app references
2. For each docs file, extract its key topics and app references
3. Match where topics overlap or apps align
4. Rank matches by confidence (explicit app reference > keyword overlap > general topic)

### Produce

```
## Cross-Reference Map

| Vault File | Matched Docs | Match Reason | Status |
|---|---|---|---|
| {vault_file} | {doc_file1}, {doc_file2} | {reason} | needs-link |
| {vault_file} | (none) | — | gap |
```

Status values:
- `linked` — bidirectional references already exist
- `needs-link` — related docs exist but aren't linked
- `gap` — one side has no counterpart

---

## Step 3: Report Gaps and Recommendations

Before making any edits, print a summary:

```
## Cross-Reference Audit Report

### Coverage Summary
- Vault files: N total, N linked, N need links, N have gaps
- Docs files: N total, N linked to vault, N could benefit from vault context

### Needs Linking (will add cross-references)
- Vault: {file} <-> Docs: {file1}, {file2}

### Gaps (no counterpart exists)
- Vault: {file} — no technical docs exist
  Recommendation: {suggestion}

### Already Linked (no action needed)
- (list any that already have cross-references)
```

**If `--audit-only` was passed, stop here.**

---

## Step 4: Add Cross-References (Surgical Edits)

For each `needs-link` pair, add minimal reference sections. Read each file fully before editing.

### 4a: Add to Vault Files

For each vault file that needs links, append a `## Technical Reference` section at the end:

```markdown
## Technical Reference

For implementation details, see the project documentation:
- [Doc Title](~/src/{repo}/docs/path/to/doc.md) — Brief description
```

Rules:
- Use `~/src/{repo}/docs/...` tilde paths (stable across machines)
- One bullet per linked doc with a brief description
- If a `## Technical Reference` section already exists, merge new links into it
- Do NOT duplicate existing links

### 4b: Add to Docs Files

For docs files that have a clear vault counterpart, add a short note near the top (after the title/first heading):

```markdown
> **Business context:** See [{Title}](~/work-vault/{Project}/{File}.md) in the work vault for product overview and roadmap.
```

Rules:
- Use `~/work-vault/...` tilde paths (stable across machines)
- Use a blockquote so it's visually distinct but unobtrusive
- Place after the first heading, before the first content section
- If a business context note already exists, update rather than duplicate
- Only add to docs files where the vault context is genuinely useful (feature docs, specs — NOT guides, conventions, or setup docs)
- Do NOT add vault links to: guides/, plans/, or convention docs

### Eligible doc categories for vault backlinks:
- `docs/features/*.md` — yes, always
- `docs/specs/*.md` — yes, always
- `docs/reference/*.md` — yes, if topic-specific (not general reference)
- Top-level `docs/*.md` — only if it's a feature-specific doc (not general conventions like ERROR_HANDLING, MODEL_CONVENTIONS, etc.)

---

## Step 5: Verify and Report

After all edits:

1. **Check only intended files were touched:**
   ```bash
   git diff --name-only
   ```
   Verify every changed file was in the cross-reference map.

2. **Review diffs are minimal:**
   ```bash
   git diff
   ```
   Each change should be a small addition (reference section or blockquote), not a rewrite.

3. **Print final report:**

```
## Cross-Reference Update Complete

### Links Added
- Vault: {file} → added Technical Reference section (N links)
- Docs: {file} → added business context note

### Gaps Flagged (no action taken)
- {file} — no counterpart

### Files Not Changed
- {file} — already linked
- {file} — general doc, vault link not appropriate
```

---

## Step 6: Commit Changes

Commit vault and docs changes separately since they're in different repos.

### 6a: Commit docs changes (if any)

```bash
cd {REPO_ROOT}
git add docs/
git commit -m "Add business context cross-references to technical docs"
```

### 6b: Commit vault changes (if any)

```bash
cd {VAULT_PATH}/..
git add {project_folder}/
git commit -m "Add technical reference cross-links to knowledge base"
```

If either repo has no changes, skip that commit.

---

## Principles

1. **Links, not content** — Add pointers between docs, never duplicate content from one into the other
2. **Surgical additions** — Append reference sections or insert blockquotes; never rewrite existing content
3. **Read before edit** — Always read the full file before modifying
4. **Respect boundaries** — Convention docs, guides, and plans don't need vault backlinks
5. **Tilde paths** — Use `~/src/{repo}/docs/...` and `~/work-vault/{project}/...` for stability
6. **Bidirectional** — Every link from A to B should have a corresponding link from B to A
7. **Idempotent** — Running twice should not create duplicate links
8. **Gaps are informational** — Flag missing counterparts but don't create placeholder docs
9. **No hardcoded projects** — Derive all paths from the current repo and vault discovery

## Integration

This skill pairs with:
- `/do-docs` — After code changes cascade docs updates, run `/do-xref` to verify vault alignment
- `/do-docs-audit` — After auditing docs accuracy, run `/do-xref` to refresh cross-references
- `/do-plan` — When a new plan is created, check if the vault has business context to link
