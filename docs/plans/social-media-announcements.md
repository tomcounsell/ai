---
status: Planning
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-08
tracking: https://github.com/tomcounsell/ai/issues/65
---

# Social Media Announcement Skills

## Problem

We need general-purpose tools for posting announcements to social media platforms. Currently there's no programmatic way to:
- Post to X (Twitter)
- Post product updates to ProductHunt
- Coordinate multi-platform announcements

**Current behavior:**
- No programmatic access to social platforms
- Manual copy-paste for announcements
- No consistent format or workflow

**Desired outcome:**
- `tools/x_client.py` — Thin Tweepy wrapper for posting tweets/threads
- `tools/producthunt_client.py` — Thin client for posting PH updates
- Skills that use these tools for announcement workflows
- Human approval via existing plan process (plan = permission)

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| X API credentials | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('X_API_KEY')"` | X posting access |
| Tweepy installed | `python -c "import tweepy; print('OK')"` | X API client |
| ProductHunt credentials | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('PRODUCTHUNT_ACCESS_TOKEN')"` | ProductHunt access |

Run all checks: `python scripts/check_prerequisites.py docs/plans/social-media-announcements.md`

## Solution

### Key Elements

- **X Client (Tweepy)**: Use existing Tweepy library for Twitter/X API v2
- **ProductHunt Client**: Browser automation via `agent-browser` (API is read-only for posts)
- **Announcement Skills**: Three skills for different use cases
- **Content Generation**: Claude generates platform-appropriate copy

### Flow

**Single Platform:**
User: "Post this to X: [content]" → Agent uses `/announce-x` → Tweet posted → URL returned

**Multi-Platform Release:**
User: "Announce [product] release" → Agent drafts copy for each platform → Plan created with exact content → User approves → `/build` executes → All URLs returned

### Technical Approach

#### X (Twitter) via Tweepy

```python
# tools/x_client.py
import tweepy
from utils.api_keys import get_env_var

def get_client() -> tweepy.Client:
    """Get authenticated Tweepy client."""
    return tweepy.Client(
        consumer_key=get_env_var("X_API_KEY"),
        consumer_secret=get_env_var("X_API_SECRET"),
        access_token=get_env_var("X_ACCESS_TOKEN"),
        access_token_secret=get_env_var("X_ACCESS_TOKEN_SECRET"),
    )

def post_tweet(text: str, reply_to: str | None = None) -> dict:
    """Post a tweet. Returns {"id": "...", "url": "..."}."""
    client = get_client()
    response = client.create_tweet(text=text, in_reply_to_tweet_id=reply_to)
    tweet_id = response.data["id"]
    return {
        "id": tweet_id,
        "url": f"https://x.com/i/web/status/{tweet_id}",
    }

def post_thread(tweets: list[str]) -> list[dict]:
    """Post a thread. Returns list of tweet data."""
    results = []
    reply_to = None
    for text in tweets:
        result = post_tweet(text, reply_to=reply_to)
        results.append(result)
        reply_to = result["id"]
    return results
```

#### ProductHunt via Browser Automation

Research shows the ProductHunt GraphQL API has no mutations for creating posts or updates - it's read-only. We'll use `agent-browser` for browser automation:

```python
# tools/producthunt_client.py
"""ProductHunt posting via browser automation.

The PH API is read-only for posts. We use agent-browser to:
1. Navigate to product page
2. Click "Post Update"
3. Fill in update content
4. Submit

Requires: PH session cookies or login flow.
"""

import subprocess
import json

def post_update(product_slug: str, title: str, body: str) -> dict:
    """Post an update to a ProductHunt product page.

    Uses agent-browser for automation since API is read-only.
    Returns {"url": "..."} on success.
    """
    # Navigate to product updates page
    subprocess.run(["agent-browser", "open",
        f"https://www.producthunt.com/products/{product_slug}/updates/new"])

    # Fill form and submit via agent-browser commands
    # (Implementation details in skill execution)
    ...
```

#### Alternative: Manual ProductHunt Flow

If browser automation proves unreliable, fall back to:
1. Generate update content
2. Open ProductHunt in browser
3. User manually posts (with copy-paste content)
4. User confirms URL

## Rabbit Holes

- **OAuth flow complexity** — Use Developer Token for X, avoid full OAuth dance
- **ProductHunt API mutations** — Don't waste time; API is confirmed read-only for posts
- **Media attachments** — V1 only, defer to later iteration
- **Scheduled posting** — Out of scope, post immediately
- **Analytics tracking** — UTM params nice-to-have, not required

## Risks

### Risk 1: X API rate limits or account restrictions
**Impact:** Cannot post announcements
**Mitigation:** Use Developer Token tier (limited but sufficient for announcements). Test with low-stakes tweets first.

### Risk 2: ProductHunt browser automation breaks
**Impact:** Cannot post PH updates programmatically
**Mitigation:** Design graceful fallback to manual posting with generated content.

### Risk 3: Tweepy API changes
**Impact:** Client breaks on X API updates
**Mitigation:** Pin Tweepy version, monitor for breaking changes.

## No-Gos (Out of Scope)

- Media/image attachments (text-only for V1)
- Scheduled/delayed posting
- Analytics dashboards
- Other platforms (LinkedIn, Mastodon, etc.)
- Engagement monitoring (likes, retweets)
- DM automation

## Update System

Add to `pyproject.toml`:
```toml
dependencies = [
    ...
    "tweepy>=4.14.0",
]
```

Add to `.env.example`:
```
# X (Twitter) API credentials
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=

# ProductHunt (browser session or token)
PRODUCTHUNT_ACCESS_TOKEN=
```

No update script changes needed — standard `uv sync` handles new dependency.

## Agent Integration

**Skills to create:**
- `.claude/skills/announce-x/SKILL.md` — Post tweets/threads
- `.claude/skills/announce-producthunt/SKILL.md` — Post PH updates
- `.claude/skills/release-announce/SKILL.md` — Orchestrate multi-platform

**Tool exposure:**
- `tools/x_client.py` — Called directly by skill, no MCP needed
- `tools/producthunt_client.py` — Uses `agent-browser` internally

**Bridge integration:** None needed — skills invoke tools directly.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/social-announcements.md`
- [ ] Add entry to `docs/features/README.md` index

### Inline Documentation
- [ ] Docstrings in `tools/x_client.py`
- [ ] Docstrings in `tools/producthunt_client.py`
- [ ] Usage examples in skill SKILL.md files

## Success Criteria

- [ ] `tools/x_client.py` can post tweets and threads via Tweepy
- [ ] `tools/producthunt_client.py` can post updates (browser automation or manual fallback)
- [ ] `/announce-x` skill works end-to-end
- [ ] `/announce-producthunt` skill works end-to-end
- [ ] `/release-announce` orchestrates both platforms
- [ ] X API credentials documented in `.env.example`
- [ ] Tweepy added to dependencies
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (x-client)**
  - Name: x-client-builder
  - Role: Implement Tweepy-based X client
  - Agent Type: builder
  - Resume: true

- **Builder (ph-client)**
  - Name: ph-client-builder
  - Role: Implement ProductHunt browser automation client
  - Agent Type: builder
  - Resume: true

- **Builder (skills)**
  - Name: skills-builder
  - Role: Create announcement skills
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end announcement flow
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add Tweepy Dependency
- **Task ID**: build-deps
- **Depends On**: none
- **Assigned To**: x-client-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `tweepy>=4.14.0` to `pyproject.toml`
- Run `uv sync` to install
- Update `.env.example` with X API credential placeholders

### 2. Build X Client
- **Task ID**: build-x-client
- **Depends On**: build-deps
- **Assigned To**: x-client-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/x_client.py` with `post_tweet()` and `post_thread()`
- Add credential loading from `.env`
- Add error handling for rate limits and auth failures
- Test with dry-run mode if no credentials available

### 3. Build ProductHunt Client
- **Task ID**: build-ph-client
- **Depends On**: none
- **Assigned To**: ph-client-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/producthunt_client.py`
- Implement browser automation via `agent-browser`
- Add manual fallback mode (generate content, user posts)
- Handle authentication state

### 4. Create /announce-x Skill
- **Task ID**: build-skill-x
- **Depends On**: build-x-client
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/announce-x/SKILL.md`
- Accept text or generate from context
- Call `tools/x_client.post_tweet()`
- Return tweet URL on success

### 5. Create /announce-producthunt Skill
- **Task ID**: build-skill-ph
- **Depends On**: build-ph-client
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/announce-producthunt/SKILL.md`
- Accept product slug, title, body
- Call `tools/producthunt_client.post_update()`
- Return update URL on success

### 6. Create /release-announce Skill
- **Task ID**: build-skill-release
- **Depends On**: build-skill-x, build-skill-ph
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/release-announce/SKILL.md`
- Generate platform-appropriate copy from release notes
- Orchestrate posting to X and ProductHunt
- Return all URLs on completion

### 7. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-skill-release
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify X client can authenticate
- Verify ProductHunt automation works (or fallback)
- Test full release announcement flow
- Verify URLs are returned correctly

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/social-announcements.md`
- Add entry to `docs/features/README.md`
- Document credential setup in README or deployment docs

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `python -c "import tweepy; print('Tweepy OK')"` — Tweepy installed
- `python -c "from tools.x_client import get_client; print('X client OK')"` — X client loads
- `python -c "from tools.producthunt_client import post_update; print('PH client OK')"` — PH client loads
- `ls .claude/skills/announce-x/SKILL.md` — X skill exists
- `ls .claude/skills/announce-producthunt/SKILL.md` — PH skill exists
- `ls .claude/skills/release-announce/SKILL.md` — Release skill exists
- `cat docs/features/social-announcements.md` — Documentation exists

## Open Questions

1. **X API tier**: Which X API tier do we have access to? (Free: 1,500 tweets/month, Basic: $100/month for more)
2. **ProductHunt auth**: Do we have a ProductHunt maker account with posting privileges?
