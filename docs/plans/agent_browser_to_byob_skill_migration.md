---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1274
last_comment_id:
---

# agent-browser → BYOB Skill Migration

## Problem

After PR #1277 ships BYOB, the agent has three browser surfaces — `agent-browser` (3rd-party
headless Playwright CLI, anonymous), `bowser` (Playwright headless, parallel-safe, anonymous),
and **BYOB MCP tools** (`byob_navigate`, `byob_click`, `byob_screenshot`, etc. — real Chrome,
the user's logged-in session). 12 skill files contain 245 references to `agent-browser`. None
yet reference BYOB. Without an explicit migration plan, every skill that should be using the
user's logged-in Chrome (LinkedIn, design audits of authenticated dashboards, internal staging
URLs) silently keeps running anonymously, producing wrong-shaped output.

**Current behavior:**

- `linkedin/SKILL.md` calls `agent-browser connect 9222` against a CDP-attached Chrome the
  operator manually launches with `--user-data-dir=/tmp/chrome-debug-profile`. This is the CDP
  hack BYOB explicitly supersedes — but the skill body still tells operators to run the launch
  command manually.
- `do-design-audit/SKILL.md`, `do-pr-review/SKILL.md`, `do-discover-paths/SKILL.md`,
  `do-design-system/SKILL.md`, `mermaid-render/SKILL.md` invoke `agent-browser` directly with
  no awareness that BYOB exists.
- `README.md` and `audit_skills.py` register `agent-browser` as the canonical browser skill
  with no mention of BYOB.
- `prepare-app/SKILL.md` and `do-test/SKILL.md` carry text references that point operators at
  `agent-browser` for browser automation context.

**Desired outcome:**

- Each of the 12 skill files has a recorded migration decision: **migrate to BYOB**, **stay on
  `agent-browser`** (with documented reason), or **doc-only update**.
- Skills that migrated to BYOB invoke `mcp__byob__*` tools (or shell out to `valor-byob` if
  one is added later — out of scope here) and set `requires_real_chrome=True` on the
  `AgentSession` field added in PR #1277, so the worker scheduler serializes them correctly.
- `.claude/skills/README.md` documents the three-surface decision rule clearly enough that a
  future skill author picks the right surface without asking.
- The `do-skills-audit` registration list recognizes BYOB as a valid browser surface alongside
  `agent-browser`.

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:22:46Z
**Disposition:** Minor drift — PR #1277 (BYOB landing) is OPEN as of plan time, not yet merged.

**File:line references re-verified:**

- `grep -rn "agent-browser" .claude/skills/ | wc -l` — confirmed 245 references across 12
  files. Per-file counts also confirmed (see Recon Summary in the issue body).
- `.claude/skills/linkedin/SKILL.md` — confirmed it uses `agent-browser connect 9222` against
  a manually-launched CDP Chrome.
- `.claude/skills/do-discover-paths/SKILL.md` — confirmed it depends heavily on
  `agent-browser eval` for CSS selector extraction.
- `.claude/skills/mermaid-render/SKILL.md` — confirmed it uses `agent-browser eval` against
  excalidraw.com (anonymous, public).
- `.claude/skills/do-skills-audit/scripts/audit_skills.py` — confirmed `agent-browser` is
  listed in the script's `MODEL_ONLY_INVOCABLE` allowlist.
- `tools/browser/__init__.py` — PR #1277 will delete the unused public wrappers and keep only
  `_downscale_if_needed`. After PR #1277 merges, this module is no longer a seam for any
  migration; skills go straight to BYOB MCP tools or stay on the `agent-browser` CLI binary.

**Cited sibling issues/PRs re-checked:**

- **#1256** — OPEN. Tracking issue for BYOB + computer-use. Implementation in PR #1277 (also
  OPEN). This plan cannot start until #1277 merges.
- **PR #1277** — OPEN as of 2026-05-04. Adds BYOB MCP registrar
  (`scripts/update/mcp_byob.py`), `AgentSession.requires_real_chrome` field, scheduler-layer
  serialization, computer-use skill, feature docs. Critically: confirms BYOB is **MCP-only**
  — there is no `valor-byob` CLI shim. Skills migrating off `agent-browser` must call BYOB
  MCP tools (`mcp__byob__*`) instead of shelling out.

**Commits on main since issue was filed (touching referenced files):** None. Issue was filed
today.

**Active plans in `docs/plans/` overlapping this area:**

- `docs/plans/byob_and_computer_use.md` — the parent plan for #1256. Lists this issue (#1274)
  in its `followups` frontmatter. The parent plan explicitly states `agent-browser` is
  untouched in #1256 and migration happens incrementally here. **Disposition: parent**, not
  overlap. This plan is the followup.

**Notes:** Plan's premise depends on PR #1277 merging first. The plan itself can be written
and critiqued now (it is documentation), but the build phase (do-build) must wait until #1277
is merged. The plan flags this with a hard prerequisite check.

## Prior Art

- **Issue #1256 / PR #1277**: BYOB infrastructure (parent work). MCP server, MV3 extension,
  `~/.claude.json` registrar, scheduler-layer serialization. This plan consumes that
  infrastructure to migrate the consumers.
- **Issue #66** (closed in #1256): Desktop control for Telegram Desktop. Closed in favor of
  the `computer-use` track of #1256. Not directly relevant to browser-skill migration but
  confirms the maintainer prefers consolidation over parallel toolchains.
- **No prior issues** propose migrating skills off `agent-browser`. This is the first
  consolidation effort.

## Research

External research is not required for this plan — the work is purely internal documentation +
skill-body rewrites against an already-shipped infrastructure surface (after #1277 merges).
The BYOB MCP tool surface, security defaults, and `requires_real_chrome` scheduler gate are
all documented in `docs/features/byob-browser-control.md` (added by PR #1277) and the parent
plan `docs/plans/byob_and_computer_use.md`.

No relevant external findings — proceeding with codebase context and the parent plan's
research already captured.

## Spike Results

No spikes required. All assumptions are answerable by reading existing skills + the parent
plan + PR #1277. Specifically:

- Whether BYOB is MCP-only or has a CLI shim → **MCP-only** (confirmed by reading PR #1277's
  `byob-browser-control.md` and `scripts/update/mcp_byob.py`).
- Whether each skill is anonymous or logged-in → **per-skill judgment call**, captured in the
  per-skill decision matrix in the Solution section. No prototyping needed.
- Whether `agent-browser eval` has a BYOB equivalent → **yes**, but gated by
  `BYOB_ALLOW_EVAL=1`. Documented in the parent plan's Research section. This shapes the
  decision for `do-discover-paths` (stays on `agent-browser` to avoid flipping the eval gate).

## Data Flow

This plan does not change the runtime data flow — it changes which surface each skill uses.

For migrated skills (e.g., `linkedin`), the new flow is:

1. **Entry**: Operator triggers the skill (e.g., `/linkedin`).
2. **Skill body**: References `mcp__byob__*` tools instead of `agent-browser <command>`.
3. **MCP runtime**: Claude Code's MCP client calls the BYOB MCP server (registered at session
   start by `scripts/update/mcp_byob.py`).
4. **byob-bridge**: Routes the call over Unix socket → Native Messaging → Chrome MV3
   extension.
5. **Chrome extension**: Operates on the active tab in the user's real, logged-in Chrome
   session. Returns DOM snapshot / screenshot / interaction result back up the chain.
6. **Worker scheduler gate**: When the migrated skill is invoked from a session with
   `requires_real_chrome=True` set on `AgentSession`, the worker pickup loop in
   `agent/session_pickup.py` defers any other real-Chrome candidate until this one finishes.
   The skill itself does not need to set the field — that is set by the operator at session
   creation (`valor-session create --needs-real-chrome ...`) or by future inference logic.

For unmigrated skills (e.g., `mermaid-render`), the data flow is unchanged: skill shells out
to `agent-browser <command>`, which spawns its own headless Playwright with a throwaway
profile.

## Architectural Impact

- **New dependencies**: None beyond what PR #1277 already added (BYOB extension, MCP server,
  registrar, scheduler field). This plan only consumes those.
- **Interface changes**: Per-skill `allowed-tools` frontmatter changes. Skills migrating to
  BYOB add `mcp__byob__*` to `allowed-tools` and remove `Bash(agent-browser:*)` (or keep both
  if the skill genuinely uses both surfaces per-flow).
- **Coupling**: Each migrated skill picks up a hard dependency on the BYOB MCP server being
  registered in `~/.claude.json`. If the registrar fails (Step 4.9 of `scripts/update/run.py`),
  the migrated skill can no longer drive any browser. Mitigation: keep `agent-browser` in
  `allowed-tools` as a fallback for the first migration target (`linkedin`) so we can
  validate the failure mode before committing to a hard cutover.
- **Data ownership**: Browser session state moves from per-skill throwaway profiles
  (`/tmp/chrome-debug-profile` for `linkedin`) to the user's actual Chrome profile. This is
  an intentional consequence of BYOB and is the whole point of the migration.
- **Reversibility**: Each per-skill migration is a single-file text edit. Reverting any one
  skill is a `git revert` of one commit.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM check-ins

**Interactions:**

- PM check-ins: 2 (per-skill decision matrix sign-off, smoke-test sign-off after
  high-leverage migration target lands)
- Review rounds: 1 (code review on the migration patches; the work is text-rewriting against
  an existing infrastructure, not novel code)

The work is mostly text editing distributed across 12 files. The bottleneck is judgment per
skill and the smoke-test signal that the migrated skill actually drives the right Chrome.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1277 merged | `gh pr view 1277 --json state -q .state \| grep -q MERGED` | BYOB MCP registrar, scheduler field, and feature docs must be on main before any migration is meaningful |
| BYOB MCP registered locally | `python -c "import json; assert 'byob' in json.load(open('$HOME/.claude.json')).get('mcpServers', {})"` | The migrated skills call `mcp__byob__*` tools — these tools must be loaded into the agent context at session start |
| BYOB extension installed in Chrome | `test -d "$HOME/.byob"` | The extension must be loaded in the operator's actual Chrome for any BYOB call to succeed (the registrar alone is not sufficient) |
| `AgentSession.requires_real_chrome` field present | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'requires_real_chrome')"` | Migrated skills running under `valor-session create --needs-real-chrome` rely on this field for scheduler serialization |

Run all checks: `python scripts/check_prerequisites.py docs/plans/agent_browser_to_byob_skill_migration.md`

## Solution

### Key Elements

- **Per-skill decision matrix** — A single table in this plan that records the migration
  decision for each of the 12 files. Decisions: `migrate-to-byob`, `stay-on-agent-browser`
  (with reason), `dual-surface` (per-flow choice), or `doc-only-update`. This matrix is the
  build's task list.
- **Decision-rule documentation** — A new "When to use which browser surface?" section in
  `.claude/skills/README.md` so future skill authors don't have to re-derive the choice.
- **Audit list update** — Add `byob` (or the BYOB skill name once it exists) to the
  `MODEL_ONLY_INVOCABLE` list in `do-skills-audit/scripts/audit_skills.py`. This makes
  `do-skills-audit` recognize BYOB as a valid surface alongside `agent-browser`.
- **Smoke-test per migration** — For each skill that migrated to BYOB, add a short
  invocation-shape test (not a full E2E test — those need real Chrome) that verifies the
  skill body references the right tool surface and the `allowed-tools` frontmatter is
  consistent.

### Per-Skill Decision Matrix

The matrix below is the canonical decision record. Each row is one task in the build phase.

| Skill file | Decision | Reason |
|------------|----------|--------|
| `agent-browser/SKILL.md` | **stay** | The skill *is* the wrapper for the 3rd-party CLI. Renaming or deleting it would orphan callers that explicitly want the anonymous surface. Keep it; add a "When to use BYOB instead?" pointer. |
| `bowser/SKILL.md` | **stay** | Already a separate surface for parallel anonymous browsing. No `agent-browser` references in the file (it's its own skill). Confirmed not in the migration list — listed here for clarity only. |
| `linkedin/SKILL.md` | **migrate-to-byob** | LinkedIn requires login. The current CDP-attach hack (`agent-browser connect 9222` + manually launched Chrome with `--user-data-dir=/tmp/chrome-debug-profile`) is exactly what BYOB was built to replace. High-leverage first migration target. |
| `do-design-audit/SKILL.md` | **dual-surface** | Audits both public marketing pages (anonymous → `agent-browser`) and authenticated app surfaces (logged-in → BYOB). The skill body must explain the choice per-URL. |
| `do-pr-review/SKILL.md` + `sub-skills/screenshot.md` | **dual-surface** | PR previews can be either public preview-deploy URLs (anonymous) or private staging URLs that require login (BYOB). Skill body documents the per-PR choice; the operator routes manually based on the URL. |
| `do-discover-paths/SKILL.md` | **stay** | Skill depends on `agent-browser eval` for CSS-selector extraction. BYOB blocks `browser_eval` by default (gated by `BYOB_ALLOW_EVAL=1`). Migrating would either require flipping the eval gate (security regression) or rewriting the entire selector-extraction layer. Out of scope. Document why it stays. |
| `do-design-system/SKILL.md` | **doc-only-update** | The 7 references are an architectural note about why `agent-browser` is the workaround for Cosmos.so's JS-rendered SPA, not invocations. Add a row to the table mentioning BYOB as an alternative for logged-in sources. |
| `mermaid-render/SKILL.md` | **stay** | Excalidraw is anonymous and public. BYOB has no advantage here, and the skill uses `agent-browser eval` which BYOB blocks by default. No change. |
| `prepare-app/SKILL.md` | **doc-only-update** | 2 references are documentation pointers ("before browser automation with `agent-browser`"). Update to mention both surfaces. |
| `do-test/SKILL.md` | **doc-only-update** | The single reference says "the `frontend-tester` agent owns all `agent-browser` interaction — the skill never calls `agent-browser` directly." If `frontend-tester` migrates to BYOB later, this sentence updates with it; for now, leave it referencing `agent-browser` and note that BYOB is a future option. |
| `README.md` | **doc-only-update** | Update the table row for `agent-browser` to clarify it's the anonymous surface, and add a row for BYOB pointing at the parent feature doc. |
| `do-skills-audit/scripts/audit_skills.py` | **doc-only-update** | Add `byob` (or whatever the BYOB skill is called) to the `MODEL_ONLY_INVOCABLE` allowlist. One-line change. |

**Out of the 12 files: 1 real migration (linkedin), 2 dual-surface (do-design-audit,
do-pr-review), 4 doc-only updates (do-design-system, prepare-app, do-test, README,
audit_skills.py — counted as 5 files but 4 logical changes), 4 stays with documented
reasoning (agent-browser, bowser, do-discover-paths, mermaid-render).**

### Flow

Build phase per skill:

**Migration entry** (a row in the decision matrix) → **Read skill body** → **Edit skill body**
to reference the new surface (or document why it stays) → **Update `allowed-tools` frontmatter**
if the toolset changed → **Run smoke test** verifying the skill body is internally consistent
→ **Commit one skill at a time** so each commit is independently revertable.

For dual-surface skills (`do-design-audit`, `do-pr-review`):

Same flow, plus the skill body adds an explicit "Decision: which surface?" section near the
top documenting how the operator (or downstream agent) chooses between BYOB and
`agent-browser` per-invocation.

For doc-only updates:

One-line text edits committed in a single batch (one commit per file is overkill; one commit
covering all four doc-only files is appropriate).

### Technical Approach

- **The `linkedin` migration is the canonical example.** Land it first, smoke-test it
  end-to-end (operator verifies `mcp__byob__navigate('linkedin.com')` returns their logged-in
  feed), then use the resulting skill body as the template for the dual-surface and
  doc-only-update skills.
- **Each skill's `allowed-tools` frontmatter is the source of truth** for what tools the
  skill is permitted to call. Migrated skills get `mcp__byob__*` added; staying skills keep
  their existing `Bash(agent-browser:*)`. Dual-surface skills get both. The smoke test asserts
  this.
- **No `agent-browser` removal in this plan.** Per the parent plan's spike-2 finding, the
  `agent-browser` Mach-O binary is a 3rd-party dependency. Uninstalling it is out of scope
  here; that bookkeeping happens in a separate future issue once every skill has migrated.
- **No `valor-byob` CLI is added in this plan.** PR #1277 confirms BYOB is MCP-only. If a CLI
  shim is desired later (so non-MCP-aware code can call BYOB), that is a separate issue.
- **Operator must run `valor-session create --needs-real-chrome ...`** when invoking a
  migrated skill that needs the user's logged-in Chrome. This plan does not add inference
  logic to set the flag automatically; the explicit-flag path is sufficient. Document this in
  each migrated skill's Prerequisites section.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] No new exception handlers are added by this plan — the work is text editing of skill
  bodies, not Python code. State "No exception handlers in scope."

### Empty/Invalid Input Handling

- [ ] No new input handling code. State "No empty/invalid input handling needed — work is
  text-only."

### Error State Rendering

- [ ] If a migrated skill is invoked when the BYOB MCP server is not registered (operator
  hasn't run `/setup` Step 8.5 yet), Claude Code will surface a missing-tool error when the
  skill tries to call `mcp__byob__*`. Verify by spot-checking that the skill body documents
  this fallback behavior in its Prerequisites section. No silent failure — Claude Code's MCP
  runtime is the error surface.
- [ ] If a migrated skill is invoked from a session that did not set
  `requires_real_chrome=True`, two real-Chrome sessions can race. The scheduler gate from PR
  #1277 prevents this only if the flag is set. Document the requirement in each migrated
  skill's Prerequisites section: "**Always launch with `valor-session create
  --needs-real-chrome ...`** when invoking this skill."

## Test Impact

- [ ] `tests/skills/test_skill_invocations.py` (if it exists) — UPDATE: any test that asserts
  the `linkedin` skill calls `agent-browser` should be updated to assert `mcp__byob__*` calls
  instead. **Verify existence**: `ls tests/skills/ 2>/dev/null` — this directory may not
  exist. If it doesn't, no test changes here.
- [ ] `tests/unit/test_skills_audit.py` (if exists) — UPDATE: if the test asserts the
  `MODEL_ONLY_INVOCABLE` list contains specific entries, update it to include `byob`.
- [ ] `.claude/skills/do-skills-audit/scripts/audit_skills.py` — UPDATE not delete: add `byob`
  to the allowlist. Smoke-test the script still runs cleanly: `python
  .claude/skills/do-skills-audit/scripts/audit_skills.py --dry-run` (or whatever the script's
  no-op invocation is — verify in the file).

If neither of the above test files exists in the current tree, state explicitly: **No
existing tests affected by this work.** The migration is text-only editing of skill bodies
and one allowlist update; the `do-skills-audit` script is the closest thing to a test of skill
metadata and is itself updated (not asserted-against by a test).

## Rabbit Holes

- **Don't try to inline-test that BYOB actually drives Chrome.** That's an end-to-end test
  requiring a real BYOB extension install and a logged-in Chrome — out of scope for the build
  phase. The smoke test verifies the skill body's *shape* (which tool it references), not the
  runtime behavior. Operator does the runtime smoke-test manually after the build.
- **Don't add a `valor-byob` CLI shim.** PR #1277 explicitly chose MCP-only. Adding a CLI
  here would re-litigate that decision. If a shim is needed, separate issue.
- **Don't attempt to migrate `do-discover-paths` to BYOB.** The `agent-browser eval` blocker
  (BYOB blocks `browser_eval` by default) makes this a multi-day rewrite of the
  selector-extraction layer for marginal value. Document why it stays and move on.
- **Don't bump the per-skill `allowed-tools` frontmatter to include `mcp__byob__*` on every
  skill** "just in case." This pollutes the skill's tool surface and is the wrong default.
  Only skills with documented BYOB usage should declare BYOB tools.
- **Don't rewrite `mermaid-render` to use BYOB.** Excalidraw is anonymous and public. BYOB
  adds no value and blocks `eval`. Stay on `agent-browser`.
- **Don't try to uninstall the `agent-browser` npm package as part of this plan.** That's a
  separate cleanup task that depends on every skill having migrated. Premature here.

## Risks

### Risk 1: `linkedin` migration breaks the existing CDP-attach workflow

**Impact:** Operators using the existing `pkill chrome && launch with --remote-debugging-port`
recipe lose their LinkedIn skill until they install BYOB.

**Mitigation:** The `linkedin` skill body keeps the old CDP-attach prerequisites as a
"fallback path" until BYOB is verified working on every machine. Operators who haven't run
`/setup` Step 8.5 yet keep using the old path; operators who have run BYOB setup get the new
path. Both paths coexist for one release cycle, then the old path is removed in a followup
issue. Document this in the skill body.

### Risk 2: BYOB extension fails to load in Chrome silently and migrated skills produce wrong-shaped output

**Impact:** Operator runs `/linkedin`; BYOB MCP server starts but the Chrome extension isn't
actually loaded. `mcp__byob__navigate` returns an error or a blank result. Operator doesn't
realize their session isn't logged in.

**Mitigation:** Each migrated skill body's Prerequisites section includes a one-command
sanity check (`mcp__byob__list_tabs` or equivalent) that surfaces "is the extension actually
talking to Chrome?" before any real work runs. Per the parent plan's `byob-browser-control.md`
documentation, the extension load step is operator-manual and cannot be auto-verified
post-install — so the skill body must teach the operator to verify it themselves on first run.

### Risk 3: PR #1277 lands but `requires_real_chrome` field is not actually populated in practice

**Impact:** Two migrated skills run concurrently in different sessions, both drive real Chrome,
collide on the active tab, produce corrupted results or partially-overwritten DOM state.

**Mitigation:** Each migrated skill body's Prerequisites section says "**Always launch with
`valor-session create --needs-real-chrome ...`** when invoking this skill." The build phase
adds a smoke-test that grep-asserts every migrated skill's body contains that string, so a
skill author can't quietly drop the warning on a future edit.

### Risk 4: Skills that "stay on agent-browser" silently drift behind BYOB feature parity

**Impact:** BYOB gains a feature (e.g., a snapshot mode that's strictly better than
agent-browser's), but the staying skills don't adopt it because nobody re-evaluates the
decision.

**Mitigation:** The decision matrix in the Solution section is committed to this plan
document, which lives at `docs/plans/agent_browser_to_byob_skill_migration.md`. When the plan
is archived after the build, the matrix lives on in the parent feature doc
(`docs/features/byob-browser-control.md` from PR #1277) updated as part of the build phase to
reflect the post-migration state. Future re-evaluations have an obvious place to land.

## Race Conditions

This plan does not introduce new concurrency. The only race surface is the one created by PR
#1277 (real-Chrome session collision), and that's mitigated by the
`AgentSession.requires_real_chrome` scheduler gate already in place.

**No new race conditions identified.** Skills migrating to BYOB inherit the scheduler-gate
mitigation from PR #1277. Skills staying on `agent-browser` are unaffected (each
`agent-browser` invocation gets a throwaway profile, no shared state).

## No-Gos (Out of Scope)

- **Uninstalling `agent-browser`** — separate future issue, requires every skill to be
  migrated first.
- **Adding a `valor-byob` CLI shim** — PR #1277 chose MCP-only deliberately.
- **Migrating `do-discover-paths`** — blocked by BYOB's `eval` security default. Documented
  as "stay" decision.
- **Adding inference logic to auto-set `requires_real_chrome=True`** — operator-explicit
  flag is sufficient for the post-migration skills. Inference belongs in a separate issue.
- **Changing `tools/browser/__init__.py`** — already cleaned up by PR #1277.
- **Migrating `frontend-tester` agent** — that's an agent (`.claude/agents/`), not a skill,
  and the issue body lists only skills. If the agent should migrate, file a separate issue.
- **Adding new BYOB MCP tools** — out of scope. We only consume the tools PR #1277 ships.

## Update System

- **No `/update` skill changes required.** The BYOB registrar (`scripts/update/mcp_byob.py`)
  is added by PR #1277 and runs at every `/update --full`. This plan only edits skill bodies
  and documentation; nothing operator-machine-side changes.
- **No new dependencies.** Skills migrate to MCP tools that are already loaded by the BYOB
  registrar PR #1277 wires in.

## Agent Integration

- **Skills migrating to BYOB get `mcp__byob__*` added to their `allowed-tools` frontmatter.**
  Per the existing skill conventions, the `allowed-tools` line is the agent's contract for
  what each skill is permitted to call.
- **No new MCP server registration.** PR #1277's `scripts/update/mcp_byob.py` registers BYOB
  in `~/.claude.json` once per machine; this plan consumes that registration.
- **No bridge changes.** `bridge/telegram_bridge.py` does not need to know that any skill
  migrated; the bridge enqueues sessions, the worker picks them up, the worker scheduler gate
  serializes real-Chrome sessions. None of those layers care which surface a particular skill
  uses.
- **Per-skill smoke test:** add a unit-test-shape check (or a script-level assertion) that
  parses each migrated skill's frontmatter and asserts `mcp__byob__*` is present in
  `allowed-tools`. This is a structural check, not a runtime test.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/byob-browser-control.md` (added by PR #1277): replace the
  "**Followups:** [#1274](https://github.com/tomcounsell/ai/issues/1274)" line with a
  "**Migration status**" subsection listing each skill's post-build decision (linkedin →
  migrated; do-design-audit → dual-surface; etc.). The decision matrix from this plan's
  Solution section becomes the source for that subsection.
- [ ] No new top-level feature doc — this plan is a migration of consumers, not a new
  feature. The BYOB feature doc already exists.

### External Documentation Site

- [ ] No external docs site for this repo. Skip.

### Inline Documentation

- [ ] Each migrated skill's body includes a brief "**Migrated from `agent-browser` in
  #1274**" pointer near the top so future readers know where the change came from.
- [ ] `.claude/skills/README.md` gains a "When to use which browser surface?" subsection
  documenting the three-surface decision rule (the same matrix from this plan).

## Success Criteria

- [ ] Every skill in the decision matrix has a post-build state matching its decision
  (migrate-to-byob → BYOB-only references; dual-surface → both surfaces with documented
  per-flow choice; doc-only-update → updated text; stay → unchanged invocations + a
  documented reason).
- [ ] `linkedin` skill smoke-test passes: operator runs `valor-session create
  --needs-real-chrome --message "list my linkedin DMs"` against the migrated skill, and the
  result reflects the operator's actual logged-in LinkedIn session (not a CDP-attach hack and
  not anonymous).
- [ ] `.claude/skills/README.md` has a "When to use which browser surface?" section with
  prose-level instructions for skill authors.
- [ ] `do-skills-audit/scripts/audit_skills.py` recognizes BYOB as a valid model-only
  invocable surface (post-edit, the script still runs without errors).
- [ ] `docs/features/byob-browser-control.md` has a Migration Status subsection reflecting
  the post-build state.
- [ ] `agent-browser` is still installed and still works for the staying skills (`mermaid-
  render`, `do-discover-paths`, `agent-browser` itself). No skill that decided to stay is
  broken by this plan.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms each migrated skill's body references `mcp__byob__*` and not
  `Bash(agent-browser:*)` (unless dual-surface).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (linkedin-migration)**
  - Name: `linkedin-builder`
  - Role: Migrate `.claude/skills/linkedin/SKILL.md` to BYOB MCP tools (canonical first
    target). Document fallback to CDP-attach for one release cycle.
  - Agent Type: builder
  - Resume: true

- **Validator (linkedin-migration)**
  - Name: `linkedin-validator`
  - Role: Verify the migrated linkedin skill body references `mcp__byob__*`, frontmatter
    `allowed-tools` is updated, and the operator-facing prerequisites docs the
    `--needs-real-chrome` flag.
  - Agent Type: validator
  - Resume: true

- **Builder (dual-surface)**
  - Name: `dual-surface-builder`
  - Role: Update `do-design-audit/SKILL.md` and `do-pr-review/SKILL.md` (+
    `sub-skills/screenshot.md`) to document both surfaces with per-flow choice rules.
  - Agent Type: builder
  - Resume: true

- **Builder (doc-only)**
  - Name: `doc-only-builder`
  - Role: One-batch text update of `do-design-system/SKILL.md`, `prepare-app/SKILL.md`,
    `do-test/SKILL.md`, `.claude/skills/README.md`, and
    `do-skills-audit/scripts/audit_skills.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (decision-rule docs)**
  - Name: `decision-rule-builder`
  - Role: Add the "When to use which browser surface?" section to
    `.claude/skills/README.md`. Update `docs/features/byob-browser-control.md` Migration
    Status subsection.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Run all verification commands. grep-confirm migrated skills reference BYOB tools,
    staying skills still reference `agent-browser`, doc-only skills updated text. Confirm
    `audit_skills.py` runs cleanly after the allowlist update.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Migrate `linkedin` skill

- **Task ID**: build-linkedin
- **Depends On**: none (PR #1277 must be merged — gated by Prerequisites check)
- **Validates**: smoke check `grep mcp__byob__ .claude/skills/linkedin/SKILL.md`,
  `grep --needs-real-chrome .claude/skills/linkedin/SKILL.md`
- **Informed By**: parent plan's Decision 1 (BYOB is MCP-only); parent plan's Decision 2
  (real-Chrome serialization via `requires_real_chrome` field)
- **Assigned To**: linkedin-builder
- **Agent Type**: builder
- **Parallel**: false (canonical first target — must land before dual-surface work)
- Replace `agent-browser connect 9222` block in skill body with a BYOB-aware Prerequisites
  section: install BYOB extension, verify with `mcp__byob__list_tabs`, set
  `--needs-real-chrome` on session create.
- Replace each `agent-browser <command>` invocation with the equivalent BYOB MCP tool call
  (use `docs/features/byob-browser-control.md` as the tool catalog reference).
- Update `allowed-tools` frontmatter: add `mcp__byob__*`. Keep `Bash(agent-browser:*)` for
  one release cycle as a documented fallback (per Risk 1 mitigation).
- Add inline note: "**Migrated from `agent-browser` in #1274. Fallback CDP path documented
  below until BYOB rollout is complete on all machines.**"

### 2. Validate `linkedin` migration

- **Task ID**: validate-linkedin
- **Depends On**: build-linkedin
- **Assigned To**: linkedin-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify skill body references `mcp__byob__*` tools.
- Verify `allowed-tools` frontmatter includes `mcp__byob__*`.
- Verify Prerequisites section documents `valor-session create --needs-real-chrome`.
- Verify the migration pointer (`Migrated from agent-browser in #1274`) is present.
- Report pass/fail; fail blocks downstream tasks.

### 3. Update dual-surface skills (do-design-audit, do-pr-review)

- **Task ID**: build-dual-surface
- **Depends On**: validate-linkedin (canonical pattern must be confirmed first)
- **Validates**: each skill body has a "Decision: which surface?" section with per-flow
  routing rules
- **Assigned To**: dual-surface-builder
- **Agent Type**: builder
- **Parallel**: true
- For `.claude/skills/do-design-audit/SKILL.md`: add a "Decision: which surface?" section
  near the top documenting that public marketing pages → `agent-browser`, authenticated app
  surfaces → BYOB. Update each `agent-browser` example with a sibling BYOB example.
- For `.claude/skills/do-pr-review/SKILL.md` and
  `.claude/skills/do-pr-review/sub-skills/screenshot.md`: add the same decision section.
  Document that public preview-deploy URLs use `agent-browser`; private staging URLs that
  require login use BYOB.
- Update `allowed-tools` frontmatter on both skills to include both surfaces.

### 4. Doc-only updates (one batch)

- **Task ID**: build-doc-only
- **Depends On**: validate-linkedin (so the canonical migration pattern is referenceable)
- **Validates**: grep confirms each file's text changes match the decision matrix
- **Assigned To**: doc-only-builder
- **Agent Type**: builder
- **Parallel**: true
- `.claude/skills/do-design-system/SKILL.md`: add a row to the Cosmos.so workaround table
  noting that BYOB is an alternative for logged-in moodboard sources.
- `.claude/skills/prepare-app/SKILL.md`: update the "before browser automation with
  `agent-browser`" reference to "before browser automation with `agent-browser` (anonymous)
  or BYOB MCP tools (logged-in)".
- `.claude/skills/do-test/SKILL.md`: append a sentence after "the `frontend-tester` agent
  owns all `agent-browser` interaction" noting that `frontend-tester` may migrate to BYOB
  separately and this skill text will follow.
- `.claude/skills/README.md`: update the `agent-browser` table row description to clarify
  it's anonymous; add a row for BYOB pointing at `docs/features/byob-browser-control.md`.
- `.claude/skills/do-skills-audit/scripts/audit_skills.py`: add `byob` (or whatever the BYOB
  skill name is — verify by reading the registrar's output) to the `MODEL_ONLY_INVOCABLE`
  allowlist.

### 5. Document staying skills (one batch)

- **Task ID**: build-staying
- **Depends On**: validate-linkedin
- **Validates**: grep confirms each staying skill has a documented "Why this skill stays on
  `agent-browser`" sentence
- **Assigned To**: doc-only-builder
- **Agent Type**: builder
- **Parallel**: true
- `.claude/skills/agent-browser/SKILL.md`: add a "When to use BYOB instead?" section near
  the top pointing readers at BYOB for logged-in workflows.
- `.claude/skills/do-discover-paths/SKILL.md`: add a "Why this stays on `agent-browser`"
  note explaining the BYOB `eval` block.
- `.claude/skills/mermaid-render/SKILL.md`: add a "Why this stays on `agent-browser`" note
  explaining the anonymous-public nature of Excalidraw.
- (`bowser/SKILL.md` is already its own skill; no change.)

### 6. Decision-rule documentation

- **Task ID**: document-decision-rule
- **Depends On**: build-dual-surface, build-doc-only, build-staying
- **Validates**: `.claude/skills/README.md` has the decision section; `byob-browser-
  control.md` Migration Status updated
- **Assigned To**: decision-rule-builder
- **Agent Type**: documentarian
- **Parallel**: false
- In `.claude/skills/README.md`, add a "When to use which browser surface?" subsection with
  the three-surface decision matrix (anonymous public → `agent-browser`; anonymous parallel
  → `bowser`; logged-in real Chrome → BYOB).
- In `docs/features/byob-browser-control.md`, replace the "**Followups: #1274**" line with a
  "Migration Status" subsection summarizing each skill's post-build state.

### 7. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-decision-rule
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run: `grep -rln "mcp__byob__" .claude/skills/` — expect at least 3 hits (linkedin,
  do-design-audit, do-pr-review including screenshot subskill).
- Run: `grep -rln "agent-browser" .claude/skills/` — expect 8+ hits (staying skills + dual-
  surface skills + doc references; no orphaned references in fully-migrated skills).
- Run: `python .claude/skills/do-skills-audit/scripts/audit_skills.py` — expect exit 0.
- Run: `grep "Migration Status" docs/features/byob-browser-control.md` — expect 1 hit.
- Run: `grep "When to use which browser surface" .claude/skills/README.md` — expect 1 hit.
- Verify each migrated skill's `allowed-tools` frontmatter using a small parser script.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| BYOB MCP tools referenced in migrated skills | `grep -rln "mcp__byob__" .claude/skills/` | output > 0 |
| Migrated linkedin skill no longer prescribes manual CDP launch | `grep -c "remote-debugging-port=9222" .claude/skills/linkedin/SKILL.md` | exit code 1 (0 matches) — OR 1 match if kept as documented fallback |
| BYOB feature doc has migration status | `grep "Migration Status" docs/features/byob-browser-control.md` | exit code 0 |
| Decision rule documented | `grep "When to use which browser surface" .claude/skills/README.md` | exit code 0 |
| Skills audit script accepts BYOB | `python .claude/skills/do-skills-audit/scripts/audit_skills.py` | exit code 0 |
| `requires_real_chrome` flag documented in migrated skills | `grep -l "needs-real-chrome" .claude/skills/linkedin/SKILL.md` | output contains `linkedin/SKILL.md` |
| Staying skills documented their reason | `grep -l "stays on .agent-browser." .claude/skills/do-discover-paths/SKILL.md .claude/skills/mermaid-render/SKILL.md` | output contains both files |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict (2026-05-04, /do-plan-critique):** NEEDS REVISION — 1 blocker.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator | `requires_real_chrome` flag never set on bridge-spawned sessions — Telegram-initiated invocations bypass the scheduler gate from PR #1277 | Pending revision | Add bridge-side inference in `bridge/telegram_bridge.py` enqueue path: scan initial message for skill triggers (`linkedin`, etc.) and set `requires_real_chrome=True` before `agent_session_queue._push_agent_session()`. Migration of linkedin must land WITH this wiring, not before. |
| CONCERN | Skeptic | Smoke test is structural-only (grep checks); no behavioral validation that BYOB actually drives the user's Chrome | Pending revision | Operator runs `valor-session create --role dev --needs-real-chrome --message "list my linkedin DMs"` after build-linkedin, captures output to `tests/manual/linkedin_byob_smoke.txt`, links from PR description. Without this artifact, success criterion #2 is not executable per memory `feedback_acceptance_criteria_must_be_executable`. |
| CONCERN | Skeptic, Consistency Auditor | Verification check for CDP fallback is non-deterministic — "0 matches OR 1 match" cannot decide pass/fail | Pending revision | Keep CDP block, prefix with deprecation callout. Verification becomes `grep -q "Fallback path — deprecated" .claude/skills/linkedin/SKILL.md` (exit 0). File followup issue for CDP removal as part of build phase. |
| CONCERN | Operator | No rollback path documented other than `git revert` | Pending revision | Confirm `git revert <build-linkedin commit SHA>` is acceptable rollback for a text-only chore migration; add explicit one-liner to Risk 1's mitigation block. Skip env-var feature flag — runtime branching not justified for revertable work. |
| CONCERN | Adversary | TOCTOU on dual-surface routing — public URL can redirect to login page, wrong-surface choice produces wrong-shaped output silently | Pending revision | Each dual-surface skill body adds a "Surface decision" allowlist of known-public domains (e.g., `github.com`, `vercel.app preview-*` for do-pr-review; public marketing domains for do-design-audit). Default-route to BYOB for everything else. 5-line addition per skill. |
| CONCERN | Consistency Auditor | Issue Acceptance Criterion #5 (uninstall agent-browser if all skills migrate) cannot trigger — 4 skills explicitly stay | Pending revision | Add an "Issue Acceptance Criteria — Disposition" subsection after No-Gos walking through each AC: satisfied / deferred / partial. AC #5 row: "deferred — 4 skills stay per Decision Matrix; future cleanup." |
| NIT | Archaeologist | Prior Art does not cite the originating PR for the CDP-attach pattern in linkedin/SKILL.md | Pending revision | Run `git log -p --follow .claude/skills/linkedin/SKILL.md \| grep -B2 "remote-debugging-port"` and add the originating commit to Prior Art. |
| NIT | Adversary | No fail-fast check that BYOB MCP is registered before migrated skills are usable | Pending revision | Verify PR #1277 already adds a `/setup` or `/doctor` check; reference it in Prerequisites instead of duplicating. |
| NIT | Simplifier | Team Orchestration over-specifies (6 members for a text-editing job) | Pending revision | Consolidate to 3 members: linkedin-builder, migration-builder (covers dual-surface + doc-only + decision-rule), final-validator. |
| NIT | Simplifier | Decision Matrix duplicated between this plan and `byob-browser-control.md` | Pending revision | Plan's matrix stays in plan; feature doc gets a short summary + link back. After archive, matrix moves to feature doc (one-time). |
| NIT | User | Problem section understates operator pain (manual `pkill chrome && launch with debug-port` recipe) | Pending revision | Add one sentence to Problem → Current behavior: "Operators using `/linkedin` today must manually relaunch their Chrome with debug flags before each session — BYOB removes that step entirely." |

---

## Open Questions

1. **PR #1277 merge timing** — Should the build phase wait for PR #1277 to merge, or can
   the migration commits land on a feature branch that targets the same merge train? My
   recommendation: hard-block on #1277 merging first. Confirming.
2. **`linkedin` fallback retention duration** — Risk 1's mitigation keeps the old CDP-attach
   path documented for "one release cycle." Is one release the right horizon, or should we
   commit to a specific date (e.g., one month after `/setup` Step 8.5 ships)?
3. **Should `frontend-tester` agent migrate too?** The issue body mentions skills only, but
   `frontend-tester` is named in `do-test/SKILL.md` as the actual `agent-browser` user. This
   plan keeps the agent as-is and adds a forward-pointer in `do-test/SKILL.md`. If
   `frontend-tester` should migrate, it belongs in a separate issue (the agents directory is
   out of scope for this plan).
4. **Should the `agent-browser` skill itself rename** (e.g., to `agent-browser-anonymous` or
   `headless-browser`) to make the three-surface distinction obvious in the skill name? My
   recommendation: no — renaming creates churn for existing skill references. Keep the name;
   document the role in the decision rule. Confirming.
