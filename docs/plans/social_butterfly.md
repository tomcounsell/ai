---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/455
last_comment_id:
---

# Social Butterfly — Post-Merge Tweets

## Problem

Valor ships features regularly but has zero public presence. Merged PRs disappear into git history with no external visibility. Valor has an X/Twitter account but never posts.

**Current behavior:**
PRs merge silently. No one outside the team knows what Valor is building.

**Desired outcome:**
When Valor merges a noteworthy PR, a tweet goes out in his voice — building an online presence organically from the work he's already doing.

## Prior Art

No prior issues or PRs found related to social media integration in this repo.

## Data Flow

1. **Entry point**: `/do-merge` completes successfully (PR squash-merged, branch deleted)
2. **Social Butterfly skill**: Receives PR context (title, body, plan doc if exists, diff stats). The social butterfly persona evaluates newsworthiness.
3. **Decision gate**: If not noteworthy → done. If noteworthy → draft tweet.
4. **Tweet drafting**: Same persona writes the tweet in Valor's online voice.
5. **Approval**: Draft sent to Telegram. Valor (or Tom) approves with thumbs-up or edits.
6. **Posting**: `agent-browser` navigates X/Twitter and posts the tweet from Valor's logged-in session.
7. **Confirmation**: Screenshot of posted tweet sent to Telegram.

## Architectural Impact

- **New dependencies**: None (uses existing agent-browser, Telegram bridge)
- **Interface changes**: `/do-merge` gains a post-merge hook invocation
- **Coupling**: Light — social butterfly is a standalone skill invoked after merge. Merge still works if social butterfly fails.
- **Reversibility**: Fully reversible — remove the hook from do-merge and delete the skill

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review of persona voice

**Interactions:**
- PM check-ins: 1-2 (persona voice calibration, approval flow preferences)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| opencli installed | `npx @jackwener/opencli --version` | CLI interface for X/Twitter posting |
| opencli Chrome extension | Check `chrome://extensions` for OpenCLI | Browser bridge for opencli |
| agent-browser installed | `which agent-browser` | Fallback for screenshots/visual verification |
| Chrome running | `pgrep -f Chrome` | Logged-in X/Twitter session |
| X/Twitter logged in via Chrome | Manual check — open Chrome, navigate to x.com | Valor's account session |

## Solution

### Key Elements

- **Social Butterfly Persona** (`config/SOCIAL_BUTTERFLY.md`): Defines Valor's online voice — distinct from his Telegram working voice. This persona is both the gatekeeper (is this tweet-worthy?) and the writer (what's the tweet?).
- **Social Butterfly Skill** (`.claude/skills/social-butterfly/SKILL.md`): Orchestrates the full flow: evaluate PR, draft tweet, seek approval, post via opencli.
- **Do-Merge Hook**: A few lines added to `/do-merge` that invoke the social butterfly skill after successful merge.
- **opencli (primary)**: `opencli twitter post --text "..."` — single command replaces multi-step browser automation, massive token savings.
- **agent-browser (fallback)**: Used only for screenshots and visual verification of posted tweets.

### Flow

**PR merges** → do-merge invokes social butterfly → persona evaluates newsworthiness → **if yes**: draft tweet → send draft to Telegram → **await approval** (👍 or edit) → agent-browser posts to X → screenshot confirmation → **done**

**If not noteworthy**: Social butterfly responds "Not tweet-worthy" and the flow ends silently.

### Technical Approach

- **Persona config**: A markdown file (`config/SOCIAL_BUTTERFLY.md`) containing the persona prompt, voice guidelines, and examples of good/bad tweets. This is injected as context when the skill runs.
- **Newsworthiness evaluation**: The persona receives PR title, body, plan doc (if linked), and diff stats. It makes a vibes-based judgment — no heuristics, no line-count thresholds. The prompt guides what "interesting" means: new capabilities, architectural shifts, things developers would care about. Routine fixes, doc updates, and config changes are not interesting.
- **Tweet drafting**: Same prompt invocation. If the persona decides it's noteworthy, it drafts the tweet in the same response. Max 280 chars. Focus on "why it matters" not "what changed."
- **Approval via Telegram**: The draft is sent to the designated Telegram chat. Three possible responses:
  - 👍 reaction → post as-is
  - Text reply → use the reply as the tweet (edited version)
  - 👎 reaction or "skip" → don't post
- **Browser posting**: Primary: `opencli twitter post --text "tweet text"` — single command, uses Chrome's logged-in session via browser bridge. Fallback: `agent-browser connect 9222` for manual browser automation if opencli fails. Screenshot verification via `agent-browser screenshot` after posting.
- **Failure handling**: If browser posting fails (session expired, X UI changed), send error to Telegram. Never retry autonomously — social media errors need human awareness.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] agent-browser connection failure (Chrome not running, port not open) — must report to Telegram, not silently fail
- [ ] X/Twitter UI changes breaking selectors — must report, not loop

### Empty/Invalid Input Handling
- [ ] PR with no body/description — persona should still evaluate based on title and diff stats
- [ ] Empty tweet draft (persona returns empty) — skip posting, log warning

### Error State Rendering
- [ ] Failed post attempt sends clear error to Telegram with what went wrong
- [ ] Screenshot failure doesn't block the success report

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage. The do-merge command is a markdown skill file, not testable code.

## Rabbit Holes

- **Twitter API integration**: Tempting but unnecessary. opencli with a logged-in Chrome session is simpler and doesn't require API keys, rate limit management, or app approval. The API is a separate project if posting volume ever justifies it.
- **Building our own Chrome CLI**: Evaluated but unnecessary for v1. opencli already provides `twitter post`, `reply`, `like`, `follow`, `bookmark`, and more. Only build custom if opencli proves unreliable or if we need cross-site CLI commands it doesn't cover.
- **Multi-platform posting**: LinkedIn, Bluesky, etc. are future scope. Get one platform working first.
- **Image/media generation**: Generating screenshots of code, architecture diagrams, etc. for tweets. Cool but not v1.
- **Analytics/engagement tracking**: Measuring tweet performance. Not needed to ship the core feature.
- **Auto-posting without approval**: Tempting to skip the approval step for high-confidence tweets. Keep the human in the loop for v1 — social media mistakes are public and permanent.

## Risks

### Risk 1: X/Twitter UI changes break opencli or agent-browser selectors
**Impact:** Posting fails silently or posts wrong content
**Mitigation:** opencli's `[ui]` commands handle DOM interaction internally — breakage is upstream's problem to fix. agent-browser fallback provides a second path. Screenshot verification after posting catches silent failures. Alert via Telegram on any failure.

### Risk 2: Chrome session expires
**Impact:** agent-browser can't authenticate, posting fails
**Mitigation:** Detect auth failure (redirected to login page) and alert via Telegram. Re-login is a manual step — don't automate credential entry.

### Risk 3: Social butterfly persona drifts from Valor's voice
**Impact:** Tweets feel off-brand or inconsistent
**Mitigation:** Persona config in `config/SOCIAL_BUTTERFLY.md` is the single source of truth. Include example tweets (good and bad) so the voice stays calibrated. Tom can update the config to steer the voice over time.

## Race Conditions

No race conditions identified — this is a sequential post-merge hook with human-gated approval. Only one merge happens at a time in the SDLC pipeline.

## No-Gos (Out of Scope)

- Multi-platform posting (LinkedIn, Bluesky, etc.)
- Auto-posting without human approval
- Media/image generation for tweets
- Engagement analytics or metrics tracking
- Replying to mentions or DMs on X
- Scheduling tweets for optimal posting times
- Thread creation (multi-tweet threads)

## Update System

No update system changes required for the core skill. However:
- The `config/SOCIAL_BUTTERFLY.md` persona file must be propagated to Valor's machine
- Chrome must be running with `--remote-debugging-port=9222` on the machine that posts
- This feature only runs on Valor's primary machine (where Chrome is logged into X)

## Agent Integration

- **New skill**: `.claude/skills/social-butterfly/SKILL.md` — invoked by the agent after merge
- **Do-merge modification**: Add a post-merge step to `.claude/commands/do-merge.md` that invokes the social butterfly skill
- **No MCP server needed**: This uses existing agent-browser CLI and Telegram tools
- **No bridge changes**: The skill is invoked within the existing Claude Code session that runs do-merge
- **Integration test**: Verify that after a mock merge, the social butterfly skill is invoked and produces a draft tweet

## Documentation

- [ ] Create `docs/features/social-butterfly.md` describing the feature, persona config, and approval flow
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Document Chrome setup requirements (remote debugging port, logged-in X session)

## Success Criteria

- [ ] Social butterfly persona config exists at `config/SOCIAL_BUTTERFLY.md` with voice guidelines and examples
- [ ] Social butterfly skill exists at `.claude/skills/social-butterfly/SKILL.md`
- [ ] `/do-merge` invokes social butterfly after successful merge
- [ ] Persona correctly identifies noteworthy vs. routine PRs (manual validation with 5+ example PRs)
- [ ] Draft tweets are sent to Telegram for approval
- [ ] 👍 reaction triggers agent-browser to post to X/Twitter
- [ ] Posted tweet is confirmed with a screenshot sent to Telegram
- [ ] Failed posts alert via Telegram instead of failing silently
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (persona)**
  - Name: persona-builder
  - Role: Create the social butterfly persona config with voice guidelines and example tweets
  - Agent Type: builder
  - Resume: true

- **Builder (skill)**
  - Name: skill-builder
  - Role: Implement the social butterfly skill and do-merge hook
  - Agent Type: builder
  - Resume: true

- **Builder (browser-flow)**
  - Name: browser-builder
  - Role: Implement the agent-browser posting flow with approval gate
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Validate the full flow from merge → tweet draft → approval → post
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create Social Butterfly Persona
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: Manual review of voice/tone
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/SOCIAL_BUTTERFLY.md` with:
  - Persona identity and purpose
  - Voice guidelines (distinct from Telegram voice — more public, more punchy, developer-audience-aware)
  - Newsworthiness criteria (what makes a PR tweet-worthy)
  - 5+ example good tweets and 5+ example bad tweets
  - Tweet structure guidelines (max 280 chars, "why it matters" framing)

### 2. Implement Social Butterfly Skill
- **Task ID**: build-skill
- **Depends On**: build-persona
- **Validates**: Skill file structure follows existing patterns
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/social-butterfly/SKILL.md` with:
  - Input: PR number, title, body, plan doc path (optional), diff stats
  - Step 1: Load persona from `config/SOCIAL_BUTTERFLY.md`
  - Step 2: Evaluate newsworthiness (return early if not noteworthy)
  - Step 3: Draft tweet
  - Step 4: Send draft to Telegram for approval
  - Step 5: On approval, invoke agent-browser to post
  - Step 6: Screenshot and confirm

### 3. Implement Browser Posting Flow
- **Task ID**: build-browser
- **Depends On**: build-skill
- **Validates**: `tests/integration/test_social_butterfly.py` (create)
- **Assigned To**: browser-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement the posting flow using opencli as primary mechanism:
  - Primary: `opencli twitter post --text "tweet text"` (single command, ~50 tokens vs ~2000-5000 with agent-browser)
  - Verify post success from opencli output (JSON format: `opencli twitter post --text "..." -f json`)
  - Screenshot verification: `agent-browser connect 9222` → navigate to Valor's profile → screenshot latest tweet
  - Fallback: If opencli fails, fall back to agent-browser multi-step flow (open x.com, snapshot, fill compose, click post)
- Handle failure cases: opencli bridge not running, session expired, post failed, fallback also failed

### 4. Add Do-Merge Hook
- **Task ID**: build-hook
- **Depends On**: build-skill
- **Validates**: do-merge.md includes post-merge social butterfly invocation
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Add post-merge section to `.claude/commands/do-merge.md`:
  - After successful merge and cleanup
  - Gather PR context (title, body, diff stats, linked plan doc)
  - Invoke social butterfly skill with this context
  - Non-blocking: merge is already done, social butterfly failure doesn't affect merge status

### 5. End-to-End Validation
- **Task ID**: validate-e2e
- **Depends On**: build-hook, build-browser
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify persona config exists and has required sections
- Verify skill file follows existing skill patterns
- Verify do-merge includes social butterfly invocation
- Verify agent-browser commands are correct for X/Twitter
- Dry-run with a recently merged PR to verify draft generation

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-e2e
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/social-butterfly.md`
- Add entry to `docs/features/README.md`
- Document Chrome setup requirements

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Persona config exists | `test -f config/SOCIAL_BUTTERFLY.md` | exit code 0 |
| Skill file exists | `test -f .claude/skills/social-butterfly/SKILL.md` | exit code 0 |
| Do-merge references social butterfly | `grep -l "social.butterfly" .claude/commands/do-merge.md` | exit code 0 |
| Feature docs exist | `test -f docs/features/social-butterfly.md` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

---

## Open Questions

1. **Valor's X/Twitter handle**: What's the account username? Needed for the agent-browser navigation and persona config.
2. **Telegram approval chat**: Which Telegram chat should receive tweet drafts for approval? The main "Dev: Valor" chat, or a dedicated channel?
3. **Persona voice calibration**: The persona config will have example tweets — do you want to provide some examples of the tone you're going for, or should the first draft propose a voice and you'll iterate?
4. **Chrome session on Valor's machine**: Is Chrome already running with `--remote-debugging-port=9222` on Valor's machine, or does that need to be set up?
