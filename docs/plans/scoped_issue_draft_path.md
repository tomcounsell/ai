---
status: docs_complete
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
3. **Body draft (after fix):** The skill computes `DRAFT=$(mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX")` once at the top of the body-write block and writes to `"$DRAFT"`. The first line is the captured anchor literal `<!-- draft-owner: pid=<PID> ts=<epoch> -->`. The PID and ts are captured into shell variables at draft time (`OWNER_PID=$$`, `OWNER_TS=$(date +%s)`, `ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"`) so the verification step matches against the captured literal `$ANCHOR`, never re-deriving `$$` at verify time.
4. **Verification (after fix):** Immediately before `gh issue create`, the skill runs `head -1 "$DRAFT" | grep -qF "<!-- ${ANCHOR} -->"` (using the captured anchor literal). On mismatch, the skill prints a clear error to stderr (including both expected and actual anchor) and exits non-zero so the SDLC pipeline registers the failure.
5. **Publish:** `gh issue create … --body "$(cat "$DRAFT")"` runs against the verified draft. Output (issue URL) flows back to the agent.
6. **Cleanup (best-effort):** `rm -f "$DRAFT"` after publish. Failure to remove is non-fatal; `mktemp` paths are in `$TMPDIR`/`/tmp` and will get reaped by the OS.

**Single-shell invariant (load-bearing):** Steps 3 through 6 MUST execute inside a single bash tool invocation. Each Bash tool call in this codebase spawns a fresh shell with a new `$$`; if `mktemp` runs in one tool call and verification runs in another, `OWNER_PID`/`OWNER_TS`/`ANCHOR`/`DRAFT` shell variables vanish between calls and verification cannot reconstruct the captured anchor. The skill must wrap the entire mktemp → write → verify → publish → cleanup pipeline in one bash block. This is prescribed explicitly in each SKILL.md step rather than left as the agent's inference.

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

- **Per-invocation `mktemp` draft path** — Each `/do-issue` and `/do-investigation-issue` invocation gets a unique scratch file like `/tmp/issue_body.AbC123`. Two concurrent invocations are guaranteed-disjoint by `mktemp` semantics. The template uses the positional form `mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX"` which works identically on macOS (BSD) and Linux (GNU). The `-t TEMPLATE` form is BSD/GNU-incompatible: BSD `mktemp -t prefix` treats the argument as a literal prefix and appends its own random suffix, so `mktemp -t issue_body.XXXXXX.md` on macOS yields a file literally named `issue_body.XXXXXX.md.<rand>` — defeating the unguessable-name property. The `.md` suffix is dropped from the filename (the draft is a transient artifact `cat`-piped into `gh`; no consumer reads it by extension).
- **Owner anchor header** — Each draft's first line is `<!-- draft-owner: pid=<PID> ts=<EPOCH> -->`. The PID and timestamp are captured into shell variables (`OWNER_PID=$$`, `OWNER_TS=$(date +%s)`, `ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"`) at draft time so the verification step compares against the captured `$ANCHOR` literal, never re-deriving `$$` at verify time.
- **Single-shell invariant** — The whole mktemp → write → verify → publish → cleanup pipeline executes inside one bash tool invocation. Splitting it across tool calls breaks `$$` capture (each call is a fresh shell) and loses the `$DRAFT`/`$ANCHOR` variables. Each SKILL.md prescribes this explicitly as a single bash block.
- **Pre-publish verification** — Immediately before `gh issue create`, the skill `head -1`s the draft and `grep -qF`s for the captured anchor literal. Mismatch aborts with a clear stderr message naming both expected and actual anchor; success proceeds to publish.
- **Write-site contract closure** — Both SKILL.md files prescribe both the *write* and the *read* paths via the same `$DRAFT` shell variable in one bash block. The current `do-issue/SKILL.md` only mentions the read path (line 120's `cat /tmp/issue_body.md`), leaving the write site to the agent's improvisation. The fix replaces that implicit contract with an explicit Step that allocates `$DRAFT`, writes the body to it, verifies the anchor, and publishes — all in one bash invocation. This is decided, not deferred (see Open Question #2 in the original draft; closed here).
- **Best-effort cleanup** — `rm -f "$DRAFT"` after publish. Non-fatal on failure.

### Flow

`/do-issue` invocation → body block runs → `mktemp` allocates unique draft path → heredoc writes body with anchor header → verification grep confirms anchor matches captured PID+ts → `gh issue create` publishes verified body → cleanup removes draft.

### Technical Approach

**Closing the implicit contract for `do-issue/SKILL.md`:** The current SKILL.md only prescribes the *read* path (line 120's `cat /tmp/issue_body.md`); the *write* site is improvised by the agent at runtime, with the agent presumably deducing the path from the read site. The fix MUST add an explicit write step that allocates `DRAFT=$(mktemp …)` and prescribes "write your body to `$DRAFT`" so the same variable flows top-to-bottom through write, verify, and publish. Without this, the agent could still write to the legacy hardcoded path by convention and the read site's `mktemp` would be a no-op.

For `do-investigation-issue/SKILL.md`, the contract is already explicit: line 48 has the literal `cat > /tmp/investigation_body.md` heredoc. The fix replaces the literal path with `$DRAFT` at both write (line 48) and read (line 81) sites.

For both `do-issue/SKILL.md` and `do-investigation-issue/SKILL.md`, the SKILL.md must prescribe a **single bash tool invocation** that runs the whole pipeline (mktemp → write → verify → publish → cleanup). Each Bash tool call in this codebase is a fresh shell with its own `$$`; splitting these steps across calls breaks anchor capture and the `$DRAFT` variable. The instruction text in each SKILL.md states this explicitly so the agent doesn't accidentally split it.

Inside that single bash block:

1. **At the top**, allocate the draft path and capture owner identity (cross-platform `mktemp` template form):
   ```bash
   DRAFT=$(mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX") || { echo "ERROR: mktemp failed" >&2; exit 1; }
   OWNER_PID=$$
   OWNER_TS=$(date +%s)
   ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"
   ```
   Note: positional template form (no `-t`) — works on macOS BSD and Linux GNU identically. BSD's `-t prefix` mode treats the argument as a literal prefix (it appends its own random suffix), so `-t issue_body.XXXXXX.md` produces a file literally named `issue_body.XXXXXX.md.<rand>` on macOS. The positional template is the only portable shape. `${TMPDIR:-/tmp}` honors macOS's per-user `/var/folders/...` `$TMPDIR` and falls back to `/tmp` on Linux.
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
**Impact:** A machine that has not run `/update` since this PR merges keeps the old inode at `~/.claude/skills/<skill>/SKILL.md`. That machine's `/do-issue` invocations would still write to `/tmp/issue_body.md`. **Mitigation:** This is the standard "skills change requires `/update`" risk class, not unique to this fix. Already documented in `scripts/update/hardlinks.py` design. Operators are expected to run `/update` after pulling skill changes; the existing `/update` machinery includes a hooks-merge step that runs on every invocation. **Verification step:** the post-merge verification (see Verification table) re-runs `stat -f "%i"` on the main checkout after `/update` to confirm both file paths share an inode on the test machine.

### Risk 4: Inode check is meaningless inside a git worktree
**Impact:** This work happens in `.worktrees/scoped_issue_draft_path/`. Worktree checkouts share the `.git` dir with main but each working tree has its own independent files on disk — they are NOT hardlinks to anything under `~/.claude/skills/`. `scripts/update/hardlinks.py::_sync_skills` only runs on `/update` against the *main* checkout's `.claude/skills-global/`, so during build the worktree-side `.claude/skills-global/<skill>/SKILL.md` has its own inode unrelated to `~/.claude/skills/<skill>/SKILL.md`. Running `stat -f "%i"` between the worktree file and `~/.claude/skills/` during build will (correctly) show two different inodes — that is *not* a regression. **Mitigation:** Build-stage validation does NOT check inode equality. Inode equality is verified ONLY after merge to main and after `/update` has re-run hardlink propagation. The plan's Step-1/Step-2 validation criteria are updated to drop the in-worktree `stat` check; it moves to a post-merge step in the Verification table.

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

No documentation changes needed beyond inline SKILL.md comments. Confirmed during build: `docs/features/sdlc-pipeline.md` does not document `/do-issue`'s scratch-draft mechanism (`grep -n "issue_body\|/tmp/issue" docs/features/sdlc-pipeline.md` returns empty), so the conditional update from the original plan does not apply. The SKILL.md files are the authoritative source for skill behavior, and inline rationale comments explaining the `mktemp` + anchor check have been added to both edited blocks so a future reader does not "simplify" the safety check away.

## Success Criteria

- [ ] `/do-issue` no longer references `/tmp/issue_body.md` literally; the draft path is `mktemp`-generated per invocation.
- [ ] `/do-investigation-issue` no longer references `/tmp/investigation_body.md` literally; same scoping applied.
- [ ] Both skills prepend a `<!-- draft-owner: pid=<PID> ts=<EPOCH> -->` anchor to the draft and verify it via `head -1 | grep -qF` before `gh issue create`. Anchor mismatch causes `exit 1` with both expected and actual anchor on stderr.
- [ ] Both file paths (`~/.claude/skills/<skill>/SKILL.md` and `.claude/skills-global/<skill>/SKILL.md`) share an inode **after merge to main and `/update` re-run** (`stat -f "%i"` confirms hardlink intact). The "two mirror locations updated identically" acceptance criterion is satisfied automatically by the hardlink machinery on main; the edit in the worktree is to one inode that becomes the shared inode after `/update`. **Build-stage inode check in the worktree is intentionally skipped** — worktree files are independent inodes by git's design (see Risk 4).
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
- **Validates**: grep confirms `mktemp`, `draft-owner`, and `$DRAFT` references all present in `.claude/skills-global/do-issue/SKILL.md`; no literal `/tmp/issue_body.md` remains. (Inode equality with `~/.claude/skills/` is NOT checked here — worktree files are independent inodes; see Risk 4. That check moves to post-merge Verification.)
- **Informed By**: Hardlink Discovery section + Technical Approach steps 1–5
- **Assigned To**: skill-draft-scoper
- **Agent Type**: builder
- **Parallel**: false (sequential to ease reviewer comprehension)
- Edit `.claude/skills-global/do-issue/SKILL.md` block at line 114–121 (`### Step 6: Create the Issue`)
- Replace the `gh issue create … --body "$(cat /tmp/issue_body.md)"` block with the full mktemp + anchor + verify + publish + cleanup sequence (see Technical Approach)
- **Add an explicit body-write step BEFORE Step 6** and prescribe the single-shell invariant. The current SKILL.md only mentions the read path; the agent improvises the write at runtime. The fix must add `DRAFT=$(mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX")` and `OWNER_PID=$$; OWNER_TS=$(date +%s); ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"` near the top of Step 6 (or as a dedicated Step 5.5), inside the SAME bash block that later runs `gh issue create` and `rm -f "$DRAFT"`. The SKILL.md instruction text must explicitly state: "Run mktemp, write, verify, publish, and cleanup in one bash tool invocation. Splitting across calls breaks `$$` capture." Body write uses `printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"; cat >> "$DRAFT" << 'BODY' …agent-substituted body… BODY`. This closes the implicit contract — `$DRAFT` flows from write to verify to publish in one shell scope.

### 2. Edit `do-investigation-issue/SKILL.md`
- **Task ID**: build-do-investigation-edit
- **Depends On**: build-do-issue-edit
- **Validates**: grep confirms `mktemp`, `draft-owner`, and `$DRAFT` references present; no literal `/tmp/investigation_body.md` remains. (Inode equality is NOT checked in the worktree — see Risk 4. Post-merge only.)
- **Informed By**: Risk 1 (heredoc quoting), Technical Approach step 2 (two-step `printf` + quoted heredoc pattern)
- **Assigned To**: skill-draft-scoper
- **Agent Type**: builder
- **Parallel**: false
- Edit `.claude/skills-global/do-investigation-issue/SKILL.md` Step 2 (line 47–74, the body heredoc) and Step 3 (line 78–83, the publish). Consolidate both steps into a single bash block (or prescribe in the surrounding instruction text that the agent must run them in one bash invocation) so `$DRAFT`/`$ANCHOR`/`$OWNER_PID` persist between write and publish.
- Use `mktemp "${TMPDIR:-/tmp}/investigation_body.XXXXXX"` (positional template form — see Risk 5 / Solution Key Elements).
- Apply the two-step `printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"` then `cat >> "$DRAFT" << 'BODY' … BODY` pattern to preserve the literal-placeholder heredoc semantics
- Add the anchor verification block before `gh issue create`
- Replace `--body "$(cat /tmp/investigation_body.md)"` with `--body "$(cat "$DRAFT")"` and add `rm -f "$DRAFT"` cleanup after success
- Commit on the build branch

### 3. Validate concurrency smoke test (no real GitHub issues created)
- **Task ID**: validate-concurrency-smoke
- **Depends On**: build-do-issue-edit, build-do-investigation-edit
- **Assigned To**: concurrency-smoke-tester
- **Agent Type**: validator
- **Parallel**: false
- **No `gh issue create` against the real GitHub API in this smoke.** The structural property under test is "concurrent invocations produce non-colliding, anchor-verified drafts." That is fully observable from the draft files themselves; involving the real GitHub API risks orphan test issues if cleanup fails mid-run, which the critique flagged. Substitute `gh issue create … --body "$(cat "$DRAFT")"` with `cp "$DRAFT" "/tmp/smoke-$MARKER.captured"` so the published body is captured locally instead of posted.
- Extract the body-draft + verify pipeline from each SKILL.md into a standalone bash script (`/tmp/smoke_do_issue.sh`) for the smoke. Two background invocations of that script (`bash /tmp/smoke_do_issue.sh SMOKE-A & bash /tmp/smoke_do_issue.sh SMOKE-B & wait`) simulate concurrent agents.
- Assert: `/tmp/smoke-SMOKE-A.captured` contains marker `SMOKE-A` and not `SMOKE-B`; `/tmp/smoke-SMOKE-B.captured` contains `SMOKE-B` and not `SMOKE-A`. Each captured file's first line matches its own captured anchor (different PIDs, different `$ANCHOR` strings). Cross-contamination = test failure.
- Deliberate-collision test: spawn one invocation, pause it after mktemp+write but before verify (insert `read -t 5` in a test copy of the script). From a second shell, write `<!-- draft-owner: pid=99999 ts=0 --> hostile body` to the known `$DRAFT` path. Resume the paused invocation; confirm verification prints `ERROR: draft anchor mismatch` to stderr and exits 1, no captured file is produced.
- Cleanup: `rm -f /tmp/smoke-*.captured /tmp/smoke_do_issue.sh` and any `$TMPDIR/issue_body.*` left from interrupted runs. This cleanup runs unconditionally via `trap` in the smoke driver script.
- Report pass/fail. **No GitHub API calls made; no issues to close.**

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
- Re-grep `.claude/skills-global/{do-issue,do-investigation-issue}/SKILL.md` for `mktemp` and `draft-owner` — must return matches in both
- Skip inode equality check in the worktree (worktree files have independent inodes — see Risk 4). Inode check is in the post-merge Verification table.
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
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |

### Post-merge verification (runs on main checkout after `/update`, NOT in the worktree)

These checks require the hardlink propagation that `scripts/update/hardlinks.py::_sync_skills` performs on `/update` against the main checkout. They are meaningless inside a git worktree (see Risk 4) and are deliberately deferred to after PR merge + `/update`.

| Check | Command | Expected |
|-------|---------|----------|
| `do-issue` hardlink intact (post-merge, main) | `stat -f "%i" ~/src/ai/.claude/skills-global/do-issue/SKILL.md ~/.claude/skills/do-issue/SKILL.md \| awk '{print $1}' \| sort -u \| wc -l \| tr -d ' '` | output `1` |
| `do-investigation-issue` hardlink intact (post-merge, main) | `stat -f "%i" ~/src/ai/.claude/skills-global/do-investigation-issue/SKILL.md ~/.claude/skills/do-investigation-issue/SKILL.md \| awk '{print $1}' \| sort -u \| wc -l \| tr -d ' '` | output `1` |

The two-shell concurrency smoke (Step 3 of tasks) is **automated by a smoke driver script**, runs without touching the real GitHub API (publish replaced with local `cp` capture), and produces a written pass/fail in the validator's report. It is the load-bearing acceptance test for this fix.

## Critique Results

Revision round 1 addressed 2 blockers + 3 concerns + 1 nit:

- **B1 (mktemp template)** — RESOLVED. Switched from `mktemp -t issue_body.XXXXXX.md` (BSD-incompatible — macOS treats argument as a literal prefix and appends its own random suffix) to the positional template form `mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX"` which is identical-behavior on BSD and GNU. Verified in-shell that the new form generates `/var/folders/.../issue_body.<6 random>` on macOS. Solution Key Elements + Technical Approach step 1 document the rationale so a future reviewer can't "simplify" the form back to `-t`.
- **B2 (cross-tool-call `$$` fragility)** — RESOLVED. Added an explicit **single-shell invariant**: the entire mktemp → write → verify → publish → cleanup pipeline runs inside one bash tool invocation. Each Bash tool call spawns a fresh shell with a new `$$`; splitting the pipeline across calls loses `OWNER_PID`/`OWNER_TS`/`ANCHOR`/`DRAFT` and breaks verification. The SKILL.md instruction text must state this requirement so the agent doesn't accidentally split it. Documented in Data Flow, Solution Key Elements, Technical Approach, and Step-by-Step Tasks.
- **C1 (worktree hardlink check broken)** — RESOLVED. Added Risk 4 explaining that worktree files have independent inodes by git's design — `scripts/update/hardlinks.py` only runs on `/update` against main. Removed inode-equality checks from build-stage Steps 1/2/5. Moved the `stat -f "%i"` check to a new **Post-merge verification** subsection of the Verification table; it runs on main after `/update`. Success Criteria updated accordingly.
- **C2 (real test issues without cleanup)** — RESOLVED. Smoke test no longer creates real GitHub issues. The `gh issue create` call is replaced with `cp "$DRAFT" "/tmp/smoke-$MARKER.captured"` so the captured body is observable locally. Concurrency is exercised by two background bash invocations of an extracted smoke driver script. A `trap` in the driver guarantees `rm -f /tmp/smoke-*.captured` runs unconditionally. **No GitHub API calls; no orphan-issue risk.** Step 3 of tasks rewritten.
- **C3 (unresolved write-site contract)** — RESOLVED. Original Open Question #2 (whether to close the implicit write-site contract in `do-issue/SKILL.md`) is decided: yes, the SKILL.md MUST prescribe both write and read paths via a single `$DRAFT` variable inside one bash block. Moved from Open Questions into Solution Key Elements as a committed decision. Step-1 task text now states this requirement explicitly.
- **N1 (nit)** — Open Questions list trimmed: #2 is now a committed decision (moved into Solution); #1 and #3 remain as legitimate confirmation-asks.

### Remaining open questions (non-blocking)

1. **Should the anchor format encode anything beyond `pid=<PID> ts=<EPOCH>`?** The plan's stance is no — PID+epoch is sufficient for the local-machine, sub-second collision window. Adding a session id would be nicer but `${CLAUDE_SESSION_ID}` is not reliably exported across all invocation surfaces. Confirm this is acceptable.
2. **Should I also add inline comments to `~/.claude/skills/<skill>/SKILL.md` reading "DO NOT EDIT THIS FILE — hardlinked from .claude/skills-global/<skill>/SKILL.md, edit there"?** Currently no such warning exists, and the hardlink discovery during freshness check showed how easy it would be for a future agent to "fix" the wrong inode believing it is independent. This is one extra inline comment per file. Out of strict scope but high signal-to-noise.
