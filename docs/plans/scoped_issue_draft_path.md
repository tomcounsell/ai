---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-05-06
tracking: https://github.com/tomcounsell/ai/issues/1305
last_comment_id:
---

# Scope the Issue Draft Path So Concurrent Agents Can't Clobber Drafts

## Problem

Two GitHub-issue-creating skills draft their issue body to a process-wide, unscoped path before invoking `gh issue create --body "$(cat …)"`. Any concurrent agent on the same machine — worker session, manual Claude Code session, scheduled routine — can rewrite that file in the window between write and read. The consumer has no anchor (hash, header, owner id) to verify "this is still the file I wrote."

This already chained into a wrong-target PR in another project: a `/do-issue` run drafted body A, a sibling agent rewrote `/tmp/issue_body.md` to body B, the next `gh issue create` published body B under what the user thought was issue A's pipeline, and a duplicate issue plus an off-target PR shipped before anyone caught it. The structural enabling condition was the unscoped scratch path.

**Current behavior:**
- `.claude/skills-global/do-issue/SKILL.md:120` — `gh issue create … --body "$(cat /tmp/issue_body.md)"`
- `.claude/skills-global/do-investigation-issue/SKILL.md:48,81` — drafts and reads `/tmp/investigation_body.md`
- Path is identical for every concurrent invocation; consumer reads whatever is at the path at read-time, with no integrity check.

**Desired outcome:**
- No issue-creating skill uses an unscoped, shared `/tmp/` path.
- The draft path is unique per-invocation (`mktemp`-generated) so concurrent agents cannot collide.
- The draft body carries a one-line owner-anchor header. Before publishing, the skill verifies the anchor matches the current process; on mismatch, it aborts loudly rather than publishing unknown content.

## Freshness Check

**Baseline commit:** `0fa22cff78340bd90beb30a30f5b98dc9801bc64` (HEAD as of plan time)
**Issue filed at:** 2026-05-06T08:45:47Z (same day as plan; no commits to affected files since then)
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills-global/do-issue/SKILL.md:120` — claim: contains literal `/tmp/issue_body.md` in `gh issue create --body "$(cat /tmp/issue_body.md)"`. **Confirmed verbatim.**
- `.claude/skills-global/do-investigation-issue/SKILL.md:48` — claim: contains `cat > /tmp/investigation_body.md << 'BODY'`. **Confirmed verbatim.**
- `.claude/skills-global/do-investigation-issue/SKILL.md:81` — claim: contains `gh issue create … --body "$(cat /tmp/investigation_body.md)" \`. **Confirmed verbatim.**

**Cited sibling issues/PRs re-checked:** The issue body's narrative cites #417/#418/#419/#420 for the incident chain. In *this* repo (`tomcounsell/ai`) those numbers are unrelated SDLC pipeline integrity work. The numbers refer to a different project's issue tracker (the project where the multilingual_evaluation_content incident occurred). This does not change the plan — the structural defect described in the body exists regardless of which repo the example came from.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since=2026-05-06T08:45:47Z` against `.claude/skills-global/do-issue/SKILL.md`, `.claude/skills-global/do-investigation-issue/SKILL.md`, and `scripts/update/hardlinks.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/skills-audit-issue-filing.md` (#1299) operates on the audit→issue-filing surface in `reflections/auditing.py` — a different code path, no overlap with the scratch-draft mechanism the SKILL.md files use. No active plans touching `do-issue` or `do-investigation-issue` themselves.

**Notes:** A material discovery during freshness check changes the framing of one acceptance criterion. See **Hardlink Discovery** below.

## Hardlink Discovery (changes one acceptance criterion's framing)

The issue body says: "Both mirror copies (`~/.claude/skills/<skill>/SKILL.md` and `.claude/skills-global/<skill>/SKILL.md`) are updated identically." During freshness verification I ran `stat -f "%i %N"` on all four file paths:

```
255439705 /Users/tomcounsell/.claude/skills/do-issue/SKILL.md
255439705 /Users/tomcounsell/src/ai/.claude/skills-global/do-issue/SKILL.md
255439699 /Users/tomcounsell/.claude/skills/do-investigation-issue/SKILL.md
255439699 /Users/tomcounsell/src/ai/.claude/skills-global/do-investigation-issue/SKILL.md
```

The two "mirrors" share an inode per skill — they are **hardlinks**, not independent copies. This is enforced by `scripts/update/hardlinks.py::_sync_skills`, which `os.link()`s every file under `.claude/skills-global/` into `~/.claude/skills/` on each `/update` run. Editing the repo-side file edits the user-side file because they are literally the same bytes on disk.

**Consequences for this plan:**
- Editing only the `.claude/skills-global/` copy is sufficient on a properly-updated machine. The "two locations to edit identically" framing in the issue is, on the local invariant, a single edit that propagates by inode identity.
- On a *stale* machine where `/update` has not yet run after this PR merges, `~/.claude/skills/<skill>/SKILL.md` could still be the old inode. This is the existing risk class for *any* skill change, not unique to this fix. The deployment story is "merge → `/update` on each machine → next invocation uses the new bytes," and that already works because hardlinks are re-established on `/update`.
- The acceptance criterion "Both mirror copies are updated identically" is therefore satisfied automatically by editing `skills-global/`. We will write a one-paragraph note in the plan's verification section explaining this for the reviewer, and assert with `stat -f "%i"` post-edit that the inodes still match.

**No additional code change** to `scripts/update/hardlinks.py` is required.

## Prior Art

Searched closed issues and merged PRs in `tomcounsell/ai` for `scratch tmp draft skill` and `tmp issue_body draft scoped` (gh issue list / gh pr list, both `--state closed/merged`). **No prior issues or PRs found** addressing this defect. This is a first-time fix.

Other unscoped `/tmp/` usage exists across skills (`weekly-review`, `x-com`, `linkedin`, `do-design-audit`, `do-build`, `mermaid-render`, `computer-use`, `do-test`). The issue body explicitly drops these out of scope: most are read-once or single-tenant in practice, and a broader scratch-file audit is a separate issue if warranted. **This plan respects that scope.** No drive-by fixes.

## Research

No relevant external findings — this is a markdown-skill edit using only `bash` builtins (`mktemp`, `grep`, `cat`, `$$`) and the already-required `gh` CLI. No new libraries or external APIs.

## Data Flow

Trace the issue-creation path the fix touches end-to-end:

1. **Entry point:** Operator (or another skill) invokes `/do-issue` (or `/do-investigation-issue`). The skill's SKILL.md runs as a sequence of bash blocks in the agent's shell.
2. **Body draft (today, broken):** A `cat > /tmp/issue_body.md << 'BODY' … BODY` heredoc writes the issue body to a fixed path. Concurrent agent B can `cat > /tmp/issue_body.md` between this write and step 4's read, and there is no detector.
3. **Body draft (after fix):** The skill computes `DRAFT=$(mktemp -t issue_body.XXXXXX.md)` once at the top of the body-write block and writes to `"$DRAFT"`. The first line is `<!-- draft-owner: pid=$$ ts=<epoch> -->`. The PID and ts are captured into shell variables at draft time (`OWNER_PID=$$`, `OWNER_TS=<epoch>`) so the verification step can match against those exact values, not against `$$` at read time (a subshell would have a different `$$`).
4. **Verification (after fix):** Immediately before `gh issue create`, the skill runs `head -1 "$DRAFT" | grep -q "draft-owner: pid=$OWNER_PID ts=$OWNER_TS"` (using the captured values). On mismatch, the skill prints a clear error to stderr (including both expected and actual anchor) and exits non-zero so the SDLC pipeline registers the failure.
5. **Publish:** `gh issue create … --body "$(cat "$DRAFT")"` runs against the verified draft. Output (issue URL) flows back to the agent.
6. **Cleanup (best-effort):** `rm -f "$DRAFT"` after publish. Failure to remove is non-fatal; `mktemp` paths are in `/tmp` and will get reaped by the OS.

The fix lives entirely in steps 3–4. Steps 1, 2 (today's behavior), 5, and 6 either disappear or remain unchanged.

## Architectural Impact

- **New dependencies:** None. `mktemp`, `grep`, `head`, `$$`, `date +%s` are all bash/coreutils builtins available on every machine that runs `gh`.
- **Interface changes:** None to the agent-facing skill contract. `/do-issue` and `/do-investigation-issue` accept the same arguments, produce the same `Issue created: #N — title` output. The only observable difference: on integrity failure, the skill aborts with a clear stderr message instead of silently publishing.
- **Coupling:** Decreases coupling between concurrent skill invocations. Today they implicitly share a global file; after the fix they have no shared state.
- **Data ownership:** No change. The draft file remains a transient artifact owned by the invocation that creates it; the only difference is that ownership is now provable via the anchor.
- **Reversibility:** Trivially reversible — the change is two SKILL.md files. Reverting is a single `git revert`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully pinned by the issue body and this plan)
- Review rounds: 1 (standard PR review)

This is a markdown edit to two skill files plus a manual concurrency smoke test. No Python, no tests in the suite, no migrations, no new files. Estimated effort under one hour including the smoke test write-up.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Required to run the smoke test (creates real test issues; close them afterward) |
| `mktemp` available | `command -v mktemp` | Used by the new draft-path scheme; standard on macOS and Linux |
| Working directory clean before edit | `git status --porcelain` returns empty | Standard hygiene; ensures the SKILL.md edits are atomic |

Run all checks: `python scripts/check_prerequisites.py docs/plans/scoped_issue_draft_path.md`

## Solution

### Key Elements

- **Per-invocation `mktemp` draft path** — Each `/do-issue` and `/do-investigation-issue` invocation gets a unique scratch file like `/tmp/issue_body.AbC123.md`. Two concurrent invocations are guaranteed-disjoint by `mktemp` semantics.
- **Owner anchor header** — Each draft's first line is `<!-- draft-owner: pid=<PID> ts=<EPOCH> -->`. The PID and timestamp are captured into shell variables at draft time so the verification step compares against the values that were written, not against `$$` at verify time.
- **Pre-publish verification** — Immediately before `gh issue create`, the skill `head -1`s the draft and `grep`s for the captured anchor. Mismatch aborts with a clear stderr message naming both expected and actual anchor; success proceeds to publish.
- **Best-effort cleanup** — `rm -f "$DRAFT"` after publish. Non-fatal on failure.

### Flow

`/do-issue` invocation → body block runs → `mktemp` allocates unique draft path → heredoc writes body with anchor header → verification grep confirms anchor matches captured PID+ts → `gh issue create` publishes verified body → cleanup removes draft.

### Technical Approach

**Closing the implicit contract for `do-issue/SKILL.md`:** The current SKILL.md only prescribes the *read* path (line 120's `cat /tmp/issue_body.md`); the *write* site is improvised by the agent at runtime, with the agent presumably deducing the path from the read site. The fix MUST add an explicit write step that allocates `DRAFT=$(mktemp …)` and prescribes "write your body to `$DRAFT`" so the same variable flows top-to-bottom through write, verify, and publish. Without this, the agent could still write to the legacy hardcoded path by convention and the read site's `mktemp` would be a no-op.

For `do-investigation-issue/SKILL.md`, the contract is already explicit: line 48 has the literal `cat > /tmp/investigation_body.md` heredoc. The fix replaces the literal path with `$DRAFT` at both write (line 48) and read (line 81) sites.

For both `do-issue/SKILL.md` and `do-investigation-issue/SKILL.md`:

1. **At the top of the body-creation bash block**, allocate the draft path and capture owner identity:
   ```bash
   DRAFT=$(mktemp -t issue_body.XXXXXX.md) || { echo "ERROR: mktemp failed" >&2; exit 1; }
   OWNER_PID=$$
   OWNER_TS=$(date +%s)
   ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"
   ```
2. **In the heredoc that writes the body**, prepend the anchor as the first line:
   ```bash
   cat > "$DRAFT" << BODY
   <!-- ${ANCHOR} -->
   …existing body content…
   BODY
   ```
   Note: heredoc delimiter changes from `'BODY'` (no expansion) to `BODY` (expansion enabled) so `${ANCHOR}` interpolates. Variable expansion in the *rest* of the body is already on by intent in `do-issue/SKILL.md` (it doesn't quote the heredoc); for `do-investigation-issue/SKILL.md` the existing template uses `'BODY'` (quoted) and contains literal placeholders like `{component}` which are intended for the agent to substitute, not the shell. **Plan to preserve that:** anchor injection happens via `printf` of just the anchor line, then the existing quoted heredoc appends the rest. Concretely:
   ```bash
   printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"
   cat >> "$DRAFT" << 'BODY'
   …existing literal-placeholder body…
   BODY
   ```
   This keeps `'BODY'` semantics intact for the agent-substituted placeholders.
3. **Right before `gh issue create`**, verify the anchor:
   ```bash
   if ! head -1 "$DRAFT" | grep -qF "<!-- ${ANCHOR} -->"; then
     echo "ERROR: draft anchor mismatch — refusing to publish unknown content" >&2
     echo "  expected first line: <!-- ${ANCHOR} -->" >&2
     echo "  actual first line:   $(head -1 "$DRAFT")" >&2
     exit 1
   fi
   ```
4. **Replace** `--body "$(cat /tmp/issue_body.md)"` with `--body "$(cat "$DRAFT")"`. Same for the investigation skill's path.
5. **After `gh issue create` succeeds**, clean up:
   ```bash
   rm -f "$DRAFT"
   ```

**Apply identically to both skills.** The hardlink machinery in `scripts/update/hardlinks.py` propagates the change to `~/.claude/skills/` on the next `/update`. **No edit to `~/.claude/skills/` is needed** — that is the same inode.

**Anchor format choice:** `pid=<PID> ts=<EPOCH>` is the minimum that uniquely identifies an invocation on a single machine in a single second. PID alone is insufficient because PIDs recycle. Adding a session ID would be nicer but `${CLAUDE_SESSION_ID}` is not reliably exported across all invocation paths (bridge worker vs manual Claude Code vs scheduled routine), so we keep the anchor simple and machine-local.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No Python `except Exception: pass` blocks in the touched files — the touched files are SKILL.md (markdown). The bash error path is explicit `exit 1` with stderr output, not an exception swallow. Documented in this plan.

### Empty/Invalid Input Handling
- [ ] Document what happens if `mktemp` fails: skill exits 1 with `ERROR: mktemp failed` (line in Technical Approach step 1). The pipeline's outer SDLC machinery treats non-zero exit as stage failure.
- [ ] Document what happens if `head -1 "$DRAFT"` returns empty (file truncated to 0 bytes by another agent): the `grep -qF` fails, the verification block prints "ERROR: draft anchor mismatch" with empty actual line, exit 1. **No silent loop.**
- [ ] Document what happens if the entire draft is replaced with foreign content (the original incident scenario): `head -1` returns the foreign first line, `grep -qF` against the captured anchor fails, abort with both expected + actual lines on stderr.

### Error State Rendering
- [ ] The skill's user-visible failure path is the stderr block (`ERROR: draft anchor mismatch …`). Manual smoke (see Verification) confirms it renders to the operator and is not swallowed by `gh issue create`'s output channel.
- [ ] On success, output is unchanged (`Issue created: #{number} — {title}` line).

## Test Impact

No existing tests affected — the change is markdown-only inside two SKILL.md files. The repo's pytest suite does not exercise the SKILL.md bodies (they are interpreted by the Claude Code skill harness, not by pytest). No `tests/unit/test_do_issue.py` or `tests/integration/test_do_investigation_issue.py` exist; verification is by the manual concurrency smoke described in the issue's acceptance criteria and in this plan's Verification section.

## Rabbit Holes

- **Migrating ALL `/tmp/` usage across skills.** The issue explicitly drops this and so does this plan. `weekly-review`, `x-com`, `linkedin`, `do-design-audit`, `do-build`, `mermaid-render`, `computer-use`, `do-test` all have unscoped `/tmp/` paths. Most are read-once-by-the-same-process; the multi-tenant draft-then-publish-by-shell pattern is unique to issue creation. A broader audit is a separate issue if warranted.
- **Adding session-id to the anchor.** Tempting because it would survive PID recycling across very long-running shells, but `${CLAUDE_SESSION_ID}` is not reliably exported across all invocation surfaces (bridge worker, manual Claude Code, scheduled routine). PID+epoch is sufficient for the single-machine, sub-second collision window we actually care about. Defer.
- **Generalizing the anchor pattern into a shared bash helper sourced by both skills.** Tempting DRY, but skills are intended to be self-contained markdown — there is no `lib/anchor.sh` convention. Adding one would be its own design exercise. The five-line anchor block is small enough to duplicate.
- **Editing `scripts/update/hardlinks.py`.** Already does the right thing (hardlinks SKILL.md across `skills-global/` and `~/.claude/skills/`). No code change needed. **Verified during freshness check.**
- **Refactoring the SKILL.md files beyond the path change and anchor.** The issue's "Constraints for the planner" explicitly says surgical fix only.

## Risks

### Risk 1: Heredoc quoting interaction with anchor injection
**Impact:** If we change `do-investigation-issue/SKILL.md`'s heredoc from `'BODY'` (literal, no expansion) to unquoted `BODY` (expanding), the agent-substituted `{component}`, `{date}`, `{symptoms}`, etc. placeholders will get interpreted as shell variable references and silently expand to empty strings, corrupting the issue body. **Mitigation:** Use the two-step `printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"; cat >> "$DRAFT" << 'BODY' …` pattern documented in Technical Approach step 2. Anchor is injected via `printf` (which expands `${ANCHOR}`) and the existing literal heredoc is preserved verbatim with `>>` append. **Verification:** After the edit, `cat ~/.claude/skills/do-investigation-issue/SKILL.md` and confirm the literal `{component}` placeholders are still present in the body block.

### Risk 2: Anchor verification false-negative if the draft path is reused mid-script
**Impact:** If a future edit accidentally re-derives `DRAFT` mid-block or the heredoc body itself contains the anchor literal somewhere other than the first line, `head -1 | grep` could pass (or fail) for the wrong reason. **Mitigation:** Anchor is checked with `head -1 | grep -qF` (fixed-string, exact-match against the literal anchor line `<!-- draft-owner: pid=… ts=… -->`). The body content of an issue does not naturally contain `<!-- draft-owner:` — it would have to be deliberately included. The risk surface is "operator pastes a literal `draft-owner: pid=...` line into the body," which is contrived enough to ignore. Documented as known limitation.

### Risk 3: Hardlinks not propagated on a stale machine
**Impact:** A machine that has not run `/update` since this PR merges keeps the old inode at `~/.claude/skills/<skill>/SKILL.md`. That machine's `/do-issue` invocations would still write to `/tmp/issue_body.md`. **Mitigation:** This is the standard "skills change requires `/update`" risk class, not unique to this fix. Already documented in `scripts/update/hardlinks.py` design. Operators are expected to run `/update` after pulling skill changes; the existing `/update` machinery includes a hooks-merge step that runs on every invocation. **Verification step:** the smoke test in Verification re-runs `stat -f "%i"` post-merge to confirm both file paths share an inode on the test machine.

## Race Conditions

**The fix is itself a race-condition fix**, but the implementation introduces no new races:

### Race 1 (the one this plan FIXES): foreign agent rewrites `/tmp/issue_body.md` between write and read
**Location:** `do-issue/SKILL.md:120` and `do-investigation-issue/SKILL.md:48,81` (today's code)
**Trigger:** Two concurrent invocations of any draft-then-publish skill, or a stale prior-session agent still holding a write to the path.
**Data prerequisite:** The current invocation has just written its body to `/tmp/issue_body.md` and is about to `cat` it back into `gh issue create`.
**State prerequisite:** No collision detection exists. The consumer reads whatever is at the path at read-time.
**Mitigation:** Per-invocation `mktemp` path makes collision impossible (different file). Anchor verification catches the *theoretically still possible* case where a foreign agent guesses the `mktemp` name (cryptographically unlikely with 6 random characters) AND wins the race. Defense in depth.

### Race 2 (introduced by the fix? — analyzed and rejected): TOCTOU between anchor verify and `cat` for `gh issue create`
**Location:** New verification block + `gh issue create --body "$(cat "$DRAFT")"`
**Trigger:** Foreign agent rewrites `$DRAFT` between the `head -1 | grep` check and the `cat` for `gh`'s body argument.
**Analysis:** The `mktemp` filename is unguessable (6 random chars), so a foreign agent cannot collide on it accidentally. A targeted attack would require reading `/proc/<pid>/cmdline` or `lsof` to discover the filename, which is outside the local-machine-trust model this codebase already assumes. The consumer that benefits from the fix is *the friendly concurrent agent*, not a hostile one. **Mitigation:** None needed beyond the anchor — TOCTOU is not in this issue's threat model.

## No-Gos (Out of Scope)

- **All other unscoped `/tmp/` usage across skills** (`weekly-review`, `x-com`, `linkedin`, `do-design-audit`, `do-build`, `mermaid-render`, `computer-use`, `do-test`). Drive-by fixes are explicitly forbidden; a broader scratch-file audit is a separate issue if warranted.
- **SDLC stage-routing rework** — the misread of "ready for do-plan" against a mutated file is a separate failure mode tracked elsewhere.
- **System-reminder behavior changes** ("don't tell the user about file changes" guidance) — upstream of this repo.
- **Editing `scripts/update/hardlinks.py`** — already hardlinks the SKILL.md files correctly. No change needed.
- **Adding a `lib/anchor.sh` shared helper** to deduplicate the five-line anchor block. Skills are markdown; introducing a shared bash library is its own design exercise.
- **Adding session-id to the anchor format** beyond `pid=<PID> ts=<EPOCH>`. PID+epoch is sufficient for the threat model.
- **Pytest coverage of SKILL.md** — there is no existing harness that interprets SKILL.md from pytest. Verification is by manual concurrency smoke test only, as the issue's acceptance criteria requires.

## Update System

**No update system changes required.** `scripts/update/hardlinks.py::_sync_skills` already hardlinks every file under `.claude/skills-global/` to `~/.claude/skills/` on every `/update` run, propagating SKILL.md edits by inode identity. Operators on every machine pick up the fix by running `/update` (the standard cadence after any merge). No new files to propagate, no new dependencies, no migration steps.

The smoke test in Verification asserts post-edit that both file paths still share an inode (`stat -f "%i"` on `.claude/skills-global/<skill>/SKILL.md` and `~/.claude/skills/<skill>/SKILL.md`) — defending against accidental hardlink breakage during the edit.

## Agent Integration

**No agent integration required.** `/do-issue` and `/do-investigation-issue` are already wired into the agent (they're invoked by `/sdlc` Step 1 and on demand). The CLI surface unchanged, the bridge integration unchanged, the MCP servers unchanged. The fix is internal to the skills' bash bodies.

## Documentation

- [ ] Update `docs/features/sdlc-pipeline.md` IF it documents `/do-issue`'s draft mechanism. (If it does not, no doc update for this feature; the SKILL.md is the authoritative source for skill behavior.)
- [ ] No new `docs/features/` entry — this is a bug fix to existing skills, not a new feature.
- [ ] Inline rationale comment in each SKILL.md block explaining why `mktemp` and the anchor check exist (one short comment so a future reader does not "simplify" the safety check away).

If `docs/features/` contains nothing about `/do-issue`'s scratch mechanism, this section reduces to the SKILL.md inline comments only.

## Success Criteria

- [ ] `/do-issue` no longer references `/tmp/issue_body.md` literally; the draft path is `mktemp`-generated per invocation.
- [ ] `/do-investigation-issue` no longer references `/tmp/investigation_body.md` literally; same scoping applied.
- [ ] Both skills prepend a `<!-- draft-owner: pid=<PID> ts=<EPOCH> -->` anchor to the draft and verify it via `head -1 | grep -qF` before `gh issue create`. Anchor mismatch causes `exit 1` with both expected and actual anchor on stderr.
- [ ] Both file paths (`~/.claude/skills/<skill>/SKILL.md` and `.claude/skills-global/<skill>/SKILL.md`) share an inode after the edit (`stat -f "%i"` confirms hardlink intact). The "two mirror locations updated identically" acceptance criterion is satisfied automatically by the hardlink machinery; the edit is to one inode.
- [ ] Manual concurrency smoke test (documented in Verification) confirms two concurrent draft+publish runs from two shells produce two distinct issues with correct, non-cross-contaminated bodies.
- [ ] No new dependencies added. `bash` builtins (`mktemp`, `printf`, `grep`, `head`, `cat`, `rm`, `$$`, `date +%s`) and existing `gh` only.
- [ ] Tests pass (`/do-test`) — the existing pytest suite is not affected; this verifies no collateral breakage.
- [ ] Documentation updated (`/do-docs`) per the Documentation section.

## Team Orchestration

Single component, single skill mechanism — minimal team.

### Team Members

- **Builder (skill-edit)**
  - Name: `skill-draft-scoper`
  - Role: Apply the scratch-path + anchor edits to both SKILL.md files identically; verify hardlinks preserved post-edit
  - Agent Type: builder
  - Resume: true

- **Validator (smoke-test)**
  - Name: `concurrency-smoke-tester`
  - Role: Run the manual two-shell concurrency smoke and confirm two distinct issues with correct bodies; verify anchor-mismatch error path renders to stderr
  - Agent Type: validator
  - Resume: true

### Available Agent Types

`builder` and `validator` (Tier 1). No specialists needed — this is a small bash-edit + smoke verify.

## Step by Step Tasks

### 1. Edit `do-issue/SKILL.md`
- **Task ID**: build-do-issue-edit
- **Depends On**: none
- **Validates**: stat-inode equality between `.claude/skills-global/do-issue/SKILL.md` and `~/.claude/skills/do-issue/SKILL.md` post-edit
- **Informed By**: Hardlink Discovery section + Technical Approach steps 1–5
- **Assigned To**: skill-draft-scoper
- **Agent Type**: builder
- **Parallel**: false (sequential to ease reviewer comprehension)
- Edit `.claude/skills-global/do-issue/SKILL.md` block at line 114–121 (`### Step 6: Create the Issue`)
- Replace the `gh issue create … --body "$(cat /tmp/issue_body.md)"` block with the full mktemp + anchor + verify + publish + cleanup sequence (see Technical Approach)
- **Add an explicit body-write step BEFORE Step 6.** The current SKILL.md only prescribes the read path; the agent improvises the write at runtime. The fix must add `DRAFT=$(mktemp -t issue_body.XXXXXX.md)` and `OWNER_PID=$$; OWNER_TS=$(date +%s); ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"` near the top of Step 6 (or as a dedicated Step 5.5), then explicitly instruct the agent to write the body via `printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"; cat >> "$DRAFT" << 'BODY' …agent-substituted body… BODY`. This closes the implicit contract — `$DRAFT` flows from write to verify to publish in one shell scope, not via the agent's deduced convention
- Run `stat -f "%i" .claude/skills-global/do-issue/SKILL.md ~/.claude/skills/do-issue/SKILL.md` post-edit; confirm both inodes match
- Commit on the build branch

### 2. Edit `do-investigation-issue/SKILL.md`
- **Task ID**: build-do-investigation-edit
- **Depends On**: build-do-issue-edit
- **Validates**: stat-inode equality between `.claude/skills-global/do-investigation-issue/SKILL.md` and `~/.claude/skills/do-investigation-issue/SKILL.md` post-edit
- **Informed By**: Risk 1 (heredoc quoting), Technical Approach step 2 (two-step `printf` + quoted heredoc pattern)
- **Assigned To**: skill-draft-scoper
- **Agent Type**: builder
- **Parallel**: false
- Edit `.claude/skills-global/do-investigation-issue/SKILL.md` Step 2 (line 47–74, the body heredoc) and Step 3 (line 78–83, the publish)
- Apply the two-step `printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"` then `cat >> "$DRAFT" << 'BODY' … BODY` pattern to preserve the literal-placeholder heredoc semantics
- Add the anchor verification block before `gh issue create`
- Replace `--body "$(cat /tmp/investigation_body.md)"` with `--body "$(cat "$DRAFT")"` and add `rm -f "$DRAFT"` cleanup after success
- Run `stat -f "%i" .claude/skills-global/do-investigation-issue/SKILL.md ~/.claude/skills/do-investigation-issue/SKILL.md` post-edit; confirm both inodes match
- Commit on the build branch

### 3. Validate concurrency smoke test
- **Task ID**: validate-concurrency-smoke
- **Depends On**: build-do-issue-edit, build-do-investigation-edit
- **Assigned To**: concurrency-smoke-tester
- **Agent Type**: validator
- **Parallel**: false
- Open two shells. In shell A, run the body-draft + verify + publish sequence with a body containing the marker `SMOKE-A`. In shell B, run the same sequence simultaneously with body marker `SMOKE-B`. Use a real `gh issue create --label test` against this repo (close the resulting test issues afterward).
- Confirm: two distinct issues created, one body contains `SMOKE-A` and not `SMOKE-B`, the other contains `SMOKE-B` and not `SMOKE-A`. Cross-contamination = test failure.
- Clean up: `gh issue close <N>` on both test issues with comment "smoke test cleanup"
- Run a deliberate-collision test: in shell C, write `<!-- draft-owner: pid=99999 ts=0 --> hostile body` to the `mktemp` path of an in-progress shell D draft. Confirm shell D's verification block prints "ERROR: draft anchor mismatch" to stderr and exits 1, no issue published.
- Report pass/fail

### 4. Documentation pass
- **Task ID**: document-feature
- **Depends On**: validate-concurrency-smoke
- **Assigned To**: skill-draft-scoper (small change, no separate documentarian)
- **Agent Type**: builder
- **Parallel**: false
- Add inline comments to each SKILL.md block explaining `mktemp` + anchor rationale (one short comment per skill, so a future reader does not "simplify" the safety check away)
- If `docs/features/sdlc-pipeline.md` documents `/do-issue`'s draft mechanism, update the relevant paragraph; otherwise no docs/features/ change
- Commit

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-do-issue-edit, build-do-investigation-edit, validate-concurrency-smoke, document-feature
- **Assigned To**: concurrency-smoke-tester
- **Agent Type**: validator
- **Parallel**: false
- Re-grep `.claude/skills-global/` for `/tmp/issue_body.md` and `/tmp/investigation_body.md` — must return zero matches
- Re-stat both file pairs — both inodes still match
- Run `python -m ruff check .` and `python -m ruff format --check .` (no Python touched, but proves no collateral)
- Run `pytest tests/unit/` (parallel) — must pass; this fix does not change any Python so the suite is a regression-only check
- Final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No literal `/tmp/issue_body.md` | `grep -rn "/tmp/issue_body.md" .claude/skills-global/` | exit code 1 (no matches) |
| No literal `/tmp/investigation_body.md` | `grep -rn "/tmp/investigation_body.md" .claude/skills-global/` | exit code 1 (no matches) |
| `do-issue` SKILL.md uses `mktemp` | `grep -n "mktemp" .claude/skills-global/do-issue/SKILL.md` | output contains `mktemp` |
| `do-issue` SKILL.md has anchor check | `grep -n "draft-owner" .claude/skills-global/do-issue/SKILL.md` | output contains `draft-owner` |
| `do-investigation-issue` SKILL.md uses `mktemp` | `grep -n "mktemp" .claude/skills-global/do-investigation-issue/SKILL.md` | output contains `mktemp` |
| `do-investigation-issue` SKILL.md has anchor check | `grep -n "draft-owner" .claude/skills-global/do-investigation-issue/SKILL.md` | output contains `draft-owner` |
| `do-issue` hardlink intact | `stat -f "%i" .claude/skills-global/do-issue/SKILL.md ~/.claude/skills/do-issue/SKILL.md \| sort -u \| wc -l` | output `1` (single unique inode) |
| `do-investigation-issue` hardlink intact | `stat -f "%i" .claude/skills-global/do-investigation-issue/SKILL.md ~/.claude/skills/do-investigation-issue/SKILL.md \| sort -u \| wc -l` | output `1` |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |

The two-shell concurrency smoke (Step 3 of tasks) is **manual**, documented in this plan, and produces a written pass/fail in the validator's report. It is the load-bearing acceptance test for this fix.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Should the anchor format encode anything beyond `pid=<PID> ts=<EPOCH>`?** The plan's stance is no — PID+epoch is sufficient for the local-machine, sub-second collision window. Adding a session id would be nicer but `${CLAUDE_SESSION_ID}` is not reliably exported across all invocation surfaces. Confirm this is acceptable.
2. **The `do-issue` SKILL.md only specifies the *read* path, not the write path.** A `grep -rn "issue_body\|/tmp/issue" .claude/skills-global/do-issue/` returns exactly one match: line 120's `cat /tmp/issue_body.md`. The body must be created somewhere by the agent improvising at runtime — but `ISSUE_TEMPLATE.md`, `RECON.md`, and `CHECKLIST.md` contain no write instruction either. The current contract is "the consumer reads from this hardcoded path; you figure out how to get the body there." The fix needs to **close that contract** by making the SKILL.md prescribe both write and read paths through a single `DRAFT=$(mktemp …)` variable that flows top-to-bottom. This is the surgical fix the issue asks for; not a scope expansion. **Confirm the planner agrees with this framing** (vs. trying to thread `mktemp` only at the read site, which would leave the write-site path still hardcoded by convention).
3. **Should I also add inline comments to `~/.claude/skills/<skill>/SKILL.md` reading "DO NOT EDIT THIS FILE — hardlinked from .claude/skills-global/<skill>/SKILL.md, edit there"?** Currently no such warning exists, and the hardlink discovery during freshness check showed how easy it would be for a future agent to "fix" the wrong inode believing it is independent. This is one extra inline comment per file. Out of strict scope but high signal-to-noise.
