---
name: do-plan
description: "Use when creating or updating a feature plan document. Triggered by 'make a plan', 'plan this', 'flesh out the idea', or any request to scope and plan work before implementation."
allowed-tools: Read, Write, Edit, Glob, Bash, AskUserQuestion
---

# Make a Plan (Shape Up Methodology)

Creates structured feature plans in `docs/plans/` following Shape Up principles: narrow the problem, set appetite, rough out the solution, identify rabbit holes, and define boundaries.

## What this skill does

1. Takes a vague or specific request and narrows it into a concrete plan
2. Writes a structured plan document in `docs/plans/{slug}.md`
3. Creates or links a GitHub issue for tracking
4. Sends the plan for review with open questions

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `PLAN_TEMPLATE.md` | Writing the plan document (copy the template into `docs/plans/{slug}.md`) |
| `SCOPING.md` | The request is vague, a grab-bag, or needs narrowing before planning |
| `EXAMPLES.md` | Deciding how to respond to a user request (vague vs. grab-bag vs. good) |

## Cross-Repo Resolution

When invoked for a non-ai project, extract the `GITHUB:` line from the prompt context (e.g., `GITHUB: tomcounsell/popoto`). If present, use `--repo $GITHUB_REPO` with all `gh` commands below. If not present, omit `--repo` (defaults to cwd repo).

## When to Use

- Planning a new feature
- Updating an existing plan
- User says "make a plan", "plan this out", "flesh out the idea"
- Scoping unclear or large requests
- Before starting significant implementation work

## Session Progress Tracking

Extract the session ID from the conversation context. The bridge injects `SESSION_ID: {id}` into enriched messages. Look for this pattern and store it:

```bash
# Extract SESSION_ID from context
# Look for a line like "SESSION_ID: abc123" in the message you received
# Store in variable: SESSION_ID="abc123"

# Mark PLAN stage as in_progress at the start
python -m tools.session_progress --session-id "$SESSION_ID" --stage PLAN --status in_progress 2>/dev/null || true
```

After the plan is committed and tracking issue is linked:

```bash
# Mark PLAN stage complete and set links
python -m tools.session_progress --session-id "$SESSION_ID" --stage PLAN --status completed --issue-url "$ISSUE_URL" --plan-url "$PLAN_URL" 2>/dev/null || true
```

Where:
- `$ISSUE_URL` is the full GitHub issue URL (e.g., `https://github.com/owner/repo/issues/42`)
- `$PLAN_URL` is the full plan document URL (e.g., `https://github.com/owner/repo/blob/main/docs/plans/{slug}.md`)

## Quick Start Workflow

### Phase 1: Flesh Out at High Level

1. **Understand the request** - What's being asked?
2. **Narrow the problem** - Challenge vague requests (see `SCOPING.md` if needed)
3. **Blast radius analysis** - If the change involves code modifications, run the code impact finder:
   ```bash
   .venv/bin/python -m tools.code_impact_finder "PROBLEM_STATEMENT_HERE"
   ```
   Use results to inform plan sections:
   - `impact_type="modify"` -> **Solution** section
   - `impact_type="dependency"` -> **Risks** section
   - `impact_type="test"` -> **Success Criteria** section
   - `impact_type="config"` -> **Solution** section
   - `impact_type="docs"` -> **Documentation** section
   - Tangentially coupled files (< 0.5 relevance) -> **Rabbit Holes** section
   Skip if the change is purely documentation or process-related.

4. **Prior art search** - Search closed issues and merged PRs for related work. This prevents
   proposing solutions that have already been tried (and failed) or re-solving problems that
   already have working implementations.
   ```bash
   # Search closed issues for related keywords (use --repo if GITHUB: context line is present)
   gh issue list --state closed --search "KEYWORDS_HERE" --limit 10 --json number,title,closedAt,url $REPO_FLAG
   # Search merged PRs for related work
   gh pr list --state merged --search "KEYWORDS_HERE" --limit 10 --json number,title,mergedAt,url $REPO_FLAG
   ```
   Use results to fill the **Prior Art** section in the plan. If multiple prior attempts
   addressed the same problem, also fill the **Why Previous Fixes Failed** section.
   **Skip if:** Small appetite AND greenfield work (no existing code being modified).

4.5. **xfail test search** - For bug fixes, search the test suite for xfail markers related to the bug.
   These represent tests that document the bug but are marked as expected failures.
   ```bash
   # Search for xfail markers in tests (both decorator and runtime forms)
   grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py" | head -20
   ```
   For each xfail found that relates to the bug being fixed:
   - Add a task to the plan's **Step by Step Tasks**: "Convert TC{N} xfail to hard assertion"
   - Document the test location in the **Success Criteria** section
   - When the fix lands, the test should pass and the xfail marker must be removed
   **IMPORTANT:** Pay special attention to **runtime `pytest.xfail()` calls** inside test bodies.
   Unlike `@pytest.mark.xfail` decorators, runtime xfails short-circuit the test before reaching
   assertions — so they silently pass even after the bug is fixed. These are invisible to pytest's
   XPASS detection and MUST be explicitly listed as conversion targets in the plan.
   **Skip if:** Not a bug fix, or no xfail tests found related to this bug.

5. **Data flow trace** - For changes involving multi-component interactions, trace the data
   flow end-to-end through the system. Start from the entry point (user action, API call,
   event trigger) and follow through each component, transformation, and storage layer.
   ```bash
   # Read the entry point file
   # Follow imports and function calls through the chain
   # Document each transformation and handoff between components
   ```
   Use results to fill the **Data Flow** section in the plan. This is critical for changes
   that span multiple modules -- it prevents fixes applied at the wrong layer.
   **Skip if:** Change is isolated to a single file/function, or is purely documentation/config.

6. **Failure analysis** - If the prior art search (step 4) found previous attempts to fix the
   same problem, analyze why each attempt failed or was incomplete. Look for patterns:
   - Was the root cause correctly identified?
   - Was the fix applied at the right architectural layer?
   - Did it address a symptom instead of the underlying cause?
   - Did it introduce new problems while fixing the original?
   Use results to fill the **Why Previous Fixes Failed** section (conditional -- only include
   in the plan if prior failed fixes exist).
   **Skip if:** No prior fixes found, or this is greenfield work.

7. **Set appetite** - Small / Medium / Large (see `SCOPING.md` for sizing guidance)
8. **Rough out solution** - Key components and flow, stay abstract
9. **Race condition analysis** - If the solution involves async operations, shared mutable state,
   or cross-process data flows, identify timing hazards. For each: specify what data/state must
   be established before dependent operations read it, and how the implementation prevents races.
   Skip if the change is purely synchronous and single-threaded.

### Phase 2: Write Initial Plan

**Classification is mandatory** - every plan MUST include a `type:` field (bug, feature, or chore).

**Auto-Classification**: When a message arrives via Telegram, the bridge auto-classifies it. Check if `classification_type` is available from the session context. If available, use it as the default `type:` value. The user can always override.

Create `docs/plans/{slug}.md` using the template from `PLAN_TEMPLATE.md`.

### Phase 2.5: Link or Create Tracking Issue

After writing the plan, **push it to main first**, then link it to an existing issue OR create a new one.

```bash
# Resolve repo identity from git
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

# Push plan to main before linking
git add docs/plans/{slug}.md && git commit -m "Plan: {Feature Name}" && git push
```

**Check for existing issue first!** If the plan was created in response to an existing GitHub issue (e.g., "make a plan for issue #42"), do NOT create a new issue. Instead:

1. Add the "plan" label and prepend the plan link to the issue body
2. Update the plan frontmatter `tracking:` field with the existing issue URL

```bash
EXISTING_ISSUE=42
gh issue edit $EXISTING_ISSUE --add-label "plan" $REPO_FLAG
EXISTING_BODY=$(gh issue view $EXISTING_ISSUE --json body -q .body $REPO_FLAG)
PLAN_LINK="https://github.com/${REPO}/blob/main/docs/plans/{slug}.md"
gh issue edit $EXISTING_ISSUE $REPO_FLAG --body "**Plan:** ${PLAN_LINK}

${EXISTING_BODY}"
```

**Only create a NEW issue if** the plan was initiated from scratch (not from an existing issue).

Before creating, check `config/projects.json` for the current project:
- If `notion` key exists -> create a Notion task (use Notion MCP tools)
- If only `github` key exists -> create a GitHub issue (use `gh` CLI)
- If neither -> skip tracking, just use the plan doc

Extract `type:` from plan frontmatter before creating:

```bash
TYPE=$(grep '^type:' docs/plans/{slug}.md | sed 's/type: *//' | tr -d ' ')
if [ -z "$TYPE" ]; then
  echo "ERROR: Plan must have a 'type:' field in frontmatter (bug, feature, or chore)"
  exit 1
fi

# Use GITHUB_REPO from context if available, otherwise resolve from git
REPO="${GITHUB_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

gh issue create \
  --repo ${REPO} \
  --title "[Plan] {Feature Name}" \
  --label "plan" \
  --label "$TYPE" \
  --body "$(cat <<EOF
**Plan:** https://github.com/${REPO}/blob/main/docs/plans/{slug}.md

**Type:** {type} | **Appetite:** {appetite} | **Status:** Planning

---
This issue is for tracking and discussion. The plan document is the source of truth.
EOF
)"
```

For Notion tasks, use MCP tools to create a page with Title, Status, Type, and link to the plan document.

After linking or creating: update the plan's `tracking:` field and commit.

### Phase 2.7: Sync Issue Comments into Plan

Before finalizing, check the tracking issue for comments that contain feedback, scope changes, or new context that should be incorporated into the plan.

```bash
# Extract issue number from plan frontmatter tracking URL
ISSUE_NUM=$(grep '^tracking:' docs/plans/{slug}.md | grep -oP '/issues/\K\d+')

if [ -n "$ISSUE_NUM" ]; then
  # Get all comments with IDs, sorted chronologically
  gh api repos/{owner}/{repo}/issues/${ISSUE_NUM}/comments \
    --jq '.[] | {id: .id, author: .user.login, created: .created_at, body: .body}' 2>/dev/null

  # Get the latest comment ID
  LATEST_COMMENT_ID=$(gh api repos/{owner}/{repo}/issues/${ISSUE_NUM}/comments \
    --jq '.[-1].id // empty' 2>/dev/null)

  # Get the plan's recorded last_comment_id
  PLAN_COMMENT_ID=$(grep '^last_comment_id:' docs/plans/{slug}.md | sed 's/last_comment_id: *//')
fi
```

**If new comments exist** (LATEST_COMMENT_ID != PLAN_COMMENT_ID):
1. Read each comment since the plan's `last_comment_id`
2. Incorporate relevant feedback into the plan (scope changes, new requirements, corrections)
3. Update `last_comment_id:` in the plan frontmatter to `LATEST_COMMENT_ID`
4. Commit the updated plan

**If no tracking issue or no comments**: Skip this step.

### Phase 3: Critique and Enumerate Questions

1. **Review assumptions** - What did I assume that might be wrong?
2. **Identify gaps** - What's unclear or risky?
3. **Enumerate questions** - List all questions needing supervisor input
4. **Add questions to plan** - Append to "Open Questions" section
5. **Pre-send checklist**:
   - [ ] Plan committed AND pushed to `main`
   - [ ] GitHub issue has `**Plan:** https://github.com/${REPO}/blob/main/docs/plans/{slug}.md`
   - [ ] Plan frontmatter has `tracking:` set to the issue URL
6. **Send reply**:

```
Plan draft created: docs/plans/{slug}.md

Tracking: {GitHub issue URL or Notion page URL}

I've made the following key assumptions:
- [Assumption 1]
- [Assumption 2]

Please review the Open Questions section at the end of the plan and provide answers so I can finalize it.
```

### Phase 4: Finalize Plan

After receiving answers:

1. **Update plan** - Incorporate feedback, remove Open Questions section
2. **Mark as finalized** - Update frontmatter: `status: Ready`
3. **Suggest implementation prompt**:

```
Plan finalized: docs/plans/{slug}.md

When you're ready to implement, use this prompt:

---
Implement the plan in docs/plans/{slug}.md

Follow the solution approach, stay within the appetite, and avoid the identified rabbit holes. Check off success criteria as you complete them.
---
```

## Output Location

All plans go to: `docs/plans/{slug}.md`

Use snake_case for slugs: `async_meeting_reschedule.md`, `dark_mode_toggle.md`, `api_response_caching.md`

## Branch Workflow

**Plans are written directly on the main branch.** The plan document itself is just a document -- no feature branch needed.

When the plan is *executed* (via `/do-build`), the build skill creates a feature branch, does the work there, and opens a PR.

## Status Tracking

Status and classification are tracked in the plan document's YAML frontmatter.

**Required Frontmatter Fields:**
- `status:` - Current state of the plan
- `type:` - Classification (bug, feature, or chore) - **MANDATORY**

**Status Values:**
- `Planning` - Initial draft being created
- `Ready` - Finalized and ready for implementation
- `In Progress` - Being implemented
- `Complete` - Shipped to production
- `Cancelled` - Not pursuing this

Update status as work progresses. Keep all tracking in the plan document itself.

**Tracking issue lifecycle:**
- When plan status changes to `Ready` or `In Progress`, update the GitHub issue / Notion task status accordingly
- When plan status changes to `Complete`, close the GitHub issue (`gh issue close`) or mark the Notion task as done
- When plan status changes to `Cancelled`, close the issue with a comment explaining why
