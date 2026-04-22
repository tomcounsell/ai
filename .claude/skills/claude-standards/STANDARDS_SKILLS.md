# Skill Standards

Rules the `/claude-standards` skill uses to audit `.claude/skills/*/SKILL.md` files.

**Asset location:** `.claude/skills/<skill-name>/SKILL.md` plus optional sub-files in the same directory.

---

## Rules

### 1. `name` frontmatter is required

Identifies the skill. Constraints:
- Lowercase letters, numbers, and hyphens only
- Max 64 characters
- Should match the containing directory name

Severity if missing: **FAIL** (skill cannot be invoked reliably).
Severity if format invalid or mismatches directory: **FAIL**.

### 2. `description` frontmatter is required

Max 1,024 characters. A good description answers two questions:
- **What does the skill do?**
- **When should Claude use it?**

The description is what Claude matches against to decide whether the skill is relevant, so wording matters. Descriptions should include the phrasings users actually use to request the task.

Severity if missing: **FAIL**.
Severity if too short to answer both questions (under ~40 chars, or obviously missing the "when to use" half): **WARN**.
Severity if over 1,024 chars: **FAIL**.

### 3. `allowed-tools` is optional but should be set for read-only or security-sensitive skills

When set, restricts which tools Claude can use while the skill is active — no permission prompt required for those tools, no access to others.

If omitted, the skill uses the normal permission model with no restriction.

No severity on its own. Flag as **WARN** if the skill's name or description clearly indicates read-only behavior (e.g., audits, reviews, queries) but `allowed-tools` includes `Write`, `Edit`, or `NotebookEdit`.

### 4. `model` is optional

Pins the skill to a specific Claude model. No severity attached — report its value if set, otherwise note "default."

### 5. SKILL.md should stay under 500 lines

Skills share context with the conversation. Long SKILL.md files consume context even when only part of the content is relevant.

Severity: **WARN** if over 500 lines. The fix is progressive disclosure (Rule 6).

### 6. Use progressive disclosure for reference material

Keep essential instructions in SKILL.md. Put detail that is only sometimes needed in supporting files that Claude loads only when instructed.

Conventional layout inside the skill directory:
- `scripts/` — executable code
- `references/` — additional documentation
- `assets/` — images, templates, data files

No severity — this is a pattern, not a requirement. Flag as **WARN** only in combination with Rule 5 (file is >500 lines and has no sub-files).

### 7. Scripts should be run, not read

When a skill directory contains scripts, SKILL.md should instruct Claude to **run** them. Running consumes only the output tokens; reading loads the whole script into context.

Severity: **WARN** if SKILL.md references a script in its own directory with language that implies reading it (e.g., "read the script in scripts/foo.py to understand X") rather than running it.

### 8. File layout: `SKILL.md` must be exactly named and live inside a named directory

The filename is case-sensitive: uppercase `SKILL`, lowercase `md`. The file must sit inside `.claude/skills/<skill-name>/SKILL.md` — not at the skills root, not as `skill.md`, not as `Skill.md`. Otherwise the skill does not load.

Severity: **FAIL** if the file is missing, misnamed, or sits at the wrong level.

### 9. Descriptions should be distinct from other skills

Claude chooses a skill by semantic match. If two skills have descriptions with heavily overlapping trigger language, the wrong one gets selected. Each skill's description should make clear what this skill does that the others do not.

Severity: **WARN** if a skill's trigger language (keywords, task verbs, domain nouns) substantially overlaps with another skill in the same repo without a distinguishing clause.

### 10. External dependencies should be surfaced in the description

If a skill depends on external packages, CLIs, or services that are not standard in this environment, the description should mention them. This lets Claude know whether the dependency is available before attempting to use the skill.

Severity: **INFO** — detecting this reliably requires reading the skill's scripts, so the audit reports it as a prompt to the author rather than a hard check.

### 11. Scripts referenced by a skill must have execute permission

Any script in the skill directory that SKILL.md tells Claude to run must be `chmod +x`. Otherwise the run fails at the first invocation.

Severity: **FAIL** if a referenced script is not executable.

### 12. Paths in SKILL.md should use forward slashes

Backslash paths are platform-specific and unreliable. Use forward slashes everywhere, including for paths that will be resolved on Windows.

Severity: **WARN** for any backslash-style path inside SKILL.md.

---

## Auto-fix eligible

Most findings require judgment and are report-only. The exceptions:

- **Rule 11 (chmod +x):** when a SKILL.md-referenced script lacks execute permission, `/claude-standards --fix` may run `chmod +x <path>` on it. This is reversible, does not change file contents, and is unambiguous.

Nothing else here is safe to auto-fix — renaming the skill directory or its `name` field breaks invocation, description edits affect matching quality, and structural changes to the directory affect progressive disclosure. Surface those in the report for human review.
