---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/455
last_comment_id:
---

# Social Butterfly — Post-Merge Tweets

## Problem

Valor ships features regularly but has zero public presence. Merged PRs disappear into git history with no external visibility. Valor has an X/Twitter account (@valorengels) but never posts.

**Current behavior:**
PRs merge silently. No one outside the team knows what Valor is building.

**Desired outcome:**
When Valor merges a noteworthy PR, a tweet auto-posts in his voice — building an online presence organically from the work he's already doing. No approval gate; fully autonomous.

## Prior Art

No prior issues or PRs found related to social media integration in this repo.

## Data Flow

1. **Entry point**: `/do-merge` completes successfully (PR squash-merged, branch deleted)
2. **Social Butterfly skill**: Receives PR context (title, body, plan doc if exists, diff stats). The social butterfly persona evaluates newsworthiness.
3. **Decision gate**: If not noteworthy → done silently. If noteworthy → draft tweet.
4. **Tweet drafting**: Same persona writes the tweet in Valor's online voice.
5. **Posting**: `opencli twitter post` sends the tweet via Chrome's logged-in session. `agent-browser` takes a verification screenshot.
6. **Confirmation**: Screenshot logged. Errors alert via Telegram.

## Architectural Impact

- **New dependencies**: opencli (`@jackwener/opencli`) + Chrome extension for browser bridge
- **Interface changes**: `/do-merge` gains a post-merge hook invocation
- **Coupling**: Light — social butterfly is a standalone skill invoked after merge. Merge still works if social butterfly fails.
- **Reversibility**: Fully reversible — remove the hook from do-merge and delete the skill

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (only if persona voice needs calibration after seeing first tweets)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| opencli installed | `npx @jackwener/opencli --version` | CLI interface for X/Twitter posting |
| opencli Chrome extension | Check `chrome://extensions` for OpenCLI | Browser bridge for opencli |
| agent-browser installed | `which agent-browser` | Screenshots and visual verification |
| Chrome running | `pgrep -f Chrome` | Logged-in X/Twitter session |
| X/Twitter logged in via Chrome | Manual check — open Chrome, navigate to x.com | @valorengels account session |

## Solution

### Key Elements

- **Social Butterfly Persona** (`config/SOCIAL_BUTTERFLY.md`): Defines Valor's online voice — distinct from his Telegram working voice. This persona is both the gatekeeper (is this tweet-worthy?) and the writer (what's the tweet?). Voice inspired by Pieter Levels (honest ship updates), Kelsey Hightower (insight framing), swyx (learn in public), and DHH (values-driven takes).
- **Social Butterfly Skill** (`.claude/skills/social-butterfly/SKILL.md`): Orchestrates the full flow: evaluate PR, draft tweet, post via opencli. Fully autonomous — no approval step.
- **Do-Merge Hook**: A few lines added to `/do-merge` that invoke the social butterfly skill after successful merge.
- **opencli (primary)**: `opencli twitter post --text "..."` — single command replaces multi-step browser automation, massive token savings.
- **agent-browser (fallback)**: Used for screenshots, visual verification, and as fallback if opencli fails.

### Flow

**PR merges** → do-merge invokes social butterfly → persona evaluates newsworthiness → **if yes**: draft tweet → opencli posts to x.com → agent-browser screenshots for verification → **done**

**If not noteworthy**: Flow ends silently. No Telegram message, no noise.

### Technical Approach

- **Persona config**: A markdown file (`config/SOCIAL_BUTTERFLY.md`) containing the persona prompt, voice guidelines, and examples of good/bad tweets. This is injected as context when the skill runs.
- **Newsworthiness evaluation**: The persona receives PR title, body, plan doc (if linked), and diff stats. It makes a vibes-based judgment — no heuristics, no line-count thresholds. The prompt guides what "interesting" means: new capabilities, architectural shifts, things developers would care about. Routine fixes, doc updates, and config changes are not interesting.
- **Tweet drafting**: Same prompt invocation. If the persona decides it's noteworthy, it drafts the tweet in the same response. Max 280 chars. Focus on "why it matters" not "what changed."
- **No approval gate**: Tweets auto-post. Tom is the supervisor but doesn't want to be bothered unless something goes wrong. The persona's judgment is the only gate.
- **Browser posting**: Primary: `opencli twitter post --text "tweet text"` — single command, uses Chrome's logged-in session via browser bridge. Fallback: `agent-browser connect 9222` for manual browser automation if opencli fails. Screenshot verification via `agent-browser screenshot` after posting.
- **Failure handling**: If posting fails (session expired, X UI changed), send error to Telegram. Never retry autonomously — social media errors need human awareness. Successful posts don't notify Tom.

### Voice Guidelines (for persona config)

The social butterfly persona draws from four tweet archetypes:

1. **The Insight Tweet** (a la swyx/Kelsey): Share what you learned while building, not just what you built. *"Just shipped context-aware routing for the agent pipeline. Turns out the hard part isn't the AI — it's knowing when NOT to call it."*

2. **The Values Tweet** (a la DHH): Connect the technical decision to a principle. *"Built the approval flow but kept the human in the loop. AI should amplify judgment, not replace it."*

3. **The Ship Tweet** (a la Levels): Raw, honest, short. *"New feature: the system now tweets about its own merged PRs. Yes, this tweet was written by the thing it's describing."*

4. **The Human Moment** (a la Cassidy): Show the person behind the code. *"Spent 3 hours on a bug that turned out to be a missing comma. The AI didn't catch it either. Solidarity."*

**Anti-patterns to avoid:**
- Corporate announcements: *"We're excited to announce..."*
- Dry changelogs: *"Merged PR #433: Replace inference-based stage tracking"*
- Hype without substance: *"This changes everything!!!"*
- Hashtag spam: *"#AI #MachineLearning #DevOps #Coding"*

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] opencli connection failure (Chrome extension not running) — must report to Telegram, not silently fail
- [ ] agent-browser connection failure (Chrome not running, port not open) — must report to Telegram
- [ ] X/Twitter UI changes breaking opencli or selectors — must report, not loop

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
- **Building our own Chrome CLI**: Evaluated but unnecessary for v1. opencli already provides `twitter post`, `reply`, `like`, `follow`, `bookmark`, and more. Only build custom if opencli proves unreliable.
- **Multi-platform posting**: LinkedIn, Bluesky, etc. are future scope. Get one platform working first.
- **Image/media generation**: Generating screenshots of code, architecture diagrams, etc. for tweets. Cool but not v1.
- **Analytics/engagement tracking**: Measuring tweet performance. Not needed to ship the core feature.

## Risks

### Risk 1: X/Twitter UI changes break opencli or agent-browser selectors
**Impact:** Posting fails silently or posts wrong content
**Mitigation:** opencli handles DOM interaction internally — breakage is upstream's problem to fix. agent-browser fallback provides a second path. Screenshot verification after posting catches silent failures. Alert via Telegram on any failure.

### Risk 2: Chrome session expires
**Impact:** Posting fails
**Mitigation:** Detect auth failure (redirected to login page) and alert via Telegram. Re-login is a manual step — don't automate credential entry.

### Risk 3: Persona posts something tone-deaf
**Impact:** Public tweet that doesn't represent Valor well
**Mitigation:** Persona config includes strong anti-patterns and examples. The newsworthiness bar is high — most PRs won't trigger a tweet. Worst case, delete the tweet manually. Low volume = low risk.

## Race Conditions

No race conditions identified — this is a sequential post-merge hook. Only one merge happens at a time in the SDLC pipeline.

## No-Gos (Out of Scope)

- Multi-platform posting (LinkedIn, Bluesky, etc.)
- Human approval gate for tweets (auto-post by design)
- Media/image generation for tweets
- Engagement analytics or metrics tracking
- Replying to mentions or DMs on X
- Scheduling tweets for optimal posting times
- Thread creation (multi-tweet threads)

## Update System

The update script needs to install opencli on machines that will post tweets:
- `npm install -g @jackwener/opencli` added to dependency sync
- opencli Chrome extension must be installed manually (one-time setup per machine)
- The `config/SOCIAL_BUTTERFLY.md` persona file propagates via git automatically
- This feature only runs on machines where Chrome is logged into @valorengels on X

## Agent Integration

- **New skill**: `.claude/skills/social-butterfly/SKILL.md` — invoked by the agent after merge
- **Do-merge modification**: Add a post-merge step to `.claude/commands/do-merge.md` that invokes the social butterfly skill
- **No MCP server needed**: This uses opencli CLI, existing agent-browser CLI, and Telegram tools (for error alerts only)
- **No bridge changes**: The skill is invoked within the existing Claude Code session that runs do-merge
- **Integration test**: Verify that after a mock merge, the social butterfly skill is invoked and produces a draft tweet

## Documentation

- [ ] Create `docs/features/social-butterfly.md` describing the feature, persona config, and posting flow
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Document Chrome setup requirements (opencli extension, logged-in X session)

## Success Criteria

- [ ] Social butterfly persona config exists at `config/SOCIAL_BUTTERFLY.md` with voice guidelines and examples
- [ ] Social butterfly skill exists at `.claude/skills/social-butterfly/SKILL.md`
- [ ] `/do-merge` invokes social butterfly after successful merge
- [ ] Persona correctly identifies noteworthy vs. routine PRs (manual validation with 5+ example PRs)
- [ ] Noteworthy PRs result in auto-posted tweets to @valorengels
- [ ] Posted tweet is confirmed with a screenshot
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
  - Role: Implement the opencli posting flow with agent-browser fallback
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Validate the full flow from merge → tweet draft → post
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
  - Persona identity: Valor's public voice on social media
  - Voice guidelines inspired by Levels (ship updates), Kelsey (insights), swyx (learn in public), DHH (values)
  - Newsworthiness criteria (what makes a PR tweet-worthy)
  - 5+ example good tweets across the four archetypes
  - 5+ example bad tweets (anti-patterns)
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
  - Step 4: Post via opencli (fallback: agent-browser)
  - Step 5: Screenshot and confirm (errors → Telegram alert)

### 3. Implement Posting Flow
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
- On failure: send error to Telegram via `valor-telegram send`

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
- Verify opencli and agent-browser commands are correct for X/Twitter
- Dry-run with a recently merged PR to verify draft generation

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-e2e
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/social-butterfly.md`
- Add entry to `docs/features/README.md`
- Document Chrome + opencli setup requirements

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
