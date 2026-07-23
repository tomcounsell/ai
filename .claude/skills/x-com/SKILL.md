---
name: x-com
description: "Use when browsing x.com (Twitter), reading the timeline, posting, replying, liking, or checking DMs. Triggered by requests to post on X, browse the X feed, comment on tweets, or read/reply to X DMs."
allowed-tools: mcp__byob__browser_list_tabs, mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_get_html, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_press_key, mcp__byob__browser_scroll, mcp__byob__browser_wait_for, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, mcp__byob__browser_switch_tab, Bash(git:*), Read, Write, Edit, Grep, Glob, Agent
user-invocable: true
---

# X (Twitter) Activity

Drives the user's logged-in Chrome via BYOB. User handle: **@ValorEngels**.

## Default Behavior (no arguments)

Run all three tasks in order:

1. **Check DMs**. Reply to any messages that need attention
2. **Write a post**. About recent work from git history. A second post in the same day is fine if there's a real second angle.
3. **Browse timeline (For You)**. Like 20-50 posts, reply to up to 15, follow new people you haven't seen before. Curate the feed actively.

If arguments are given, interpret them and do only what's asked.

**Nothing goes live — post, reply, or comment — without passing both gates in order: first `Skill('de-slop')` (fresh-context review of the draft file only — removes AI-writing tells, blocks hollow drafts), then `Skill('authenticity-pass')`; the full gate procedure is in `references/posting.md`.**

**Execute, don't pause.** Render drafts inline and continue to publish in the same turn. Only stop on hard tool failure with no fallback, or a finding that contradicts the skill's premise (e.g. "no DMs need replies" → skip Task 1).

## Task guides (load on demand)

Each task's full procedure lives in a reference file. Load only what the current run needs:

| Load... | When... |
|---|---|
| [references/dms.md](references/dms.md) | Task 1: reading the DM inbox, opening threads, drafting and sending replies, updating the knowledge base |
| [references/posting.md](references/posting.md) | Task 2: what works/dies on this feed, voice rules, the receipt self-reply, subagent drafting, persona cold-read loop, authenticity gate, publish |
| [references/timeline-engagement.md](references/timeline-engagement.md) | Task 3: volume targets, For You curation, liking, following, reply screening and drafting loop — plus editing/deleting posts |

## Prerequisites

Same BYOB setup as `/linkedin` (see that skill for the full preconditions). Verify with:

```bash
cd ~/.byob && bun run doctor
```

Then sanity-check from inside the agent:

```text
mcp__byob__browser_list_tabs    # must return at least one tab
```

User must be logged into X in that Chrome session.

### Real-Chrome scheduler gate (`requires_real_chrome=True`)

X driving real Chrome must be **serialized** against other real-Chrome sessions. Bridge-spawned sessions get the flag automatically via `agent.byob_skill_triggers` (the registry includes X triggers). CLI-spawned sessions must pass `--needs-real-chrome`:

```bash
valor-session create --role eng --project-key valor --needs-real-chrome --message "check my X DMs"
```

## Tab discovery (do this first)

```text
browser_list_tabs   →   look for any tab whose URL contains "x.com" (or "twitter.com")
```

Pick the first match → that's your `tabId`. If no tab, `browser_navigate(url="https://x.com/home")`.

## DOM model

X uses a stable accessibility tree. Element names like "Reply", "Like", "Liked", "Repost", "Reposted", "Post text", "Direct Messages" are reliable. Hashed CSS classes change frequently; **do not rely on them**. Use `data-byob-idx` via the `selector: "byob:idx=N"` workflow.

### The `byob:idx` workflow

1. `browser_read(url, reuseTab=true, screens=N)` returns `interactiveElements: [{idx, tag, role, name, bounds}, ...]`.
2. Find your target by `name`. On X the post text is captured directly in the `<article>`'s `name` attribute. This is a big advantage over LinkedIn (post bodies are readable from the IE list).
3. Click with `browser_click(tabId, selector="byob:idx=<idx>")`.
4. **Re-read after every DOM-mutating click**. The `interactiveSessionTag` changes and old indices invalidate.

When multiple IEs map to the same logical control, prefer `tag: "button"`.

### X-specific gotchas (verified live)

- **Prefer `data-testid` selectors over `byob:idx` on X.** The home timeline easily blows past 1000 IEs and `byob:idx=N` clicks fail with `selector_not_found` even right after a fresh read. The reliable handles are:
  - `[data-testid="tweetTextarea_0"]`. The compose / reply textbox (works on `/home`, `/compose/post`, and any post page reply box)
  - `[data-testid="tweetButtonInline"]`. The submit button. **Same testid for posts and replies** (button text is "Post" on /home, "Reply" on a post page; testid doesn't change).
  - `[data-testid="like"]`. The like button. **Often reports `element_not_visible` because the action bar overlay covers the click point. Pass `force: true`.**
  - `article[data-testid="tweet"]`. Full tweet container; child `a[href*="/status/"]` is the permalink.
- **`<article>` IEs include the full post text in `name`**. Useful for cheap triage from the IE list before navigating into individual posts.
- **Best path to read a post body in full**: extract `/handle/status/<id>` URLs from the timeline HTML (`grep -oE 'href="/[^/]+/status/[0-9]+"'`), then `browser_navigate` to each. The post text shows up in the **page title** ("(N) Author on X: \"...full text...\" / X"). Fastest way to read the body.
- **"Want more people to see your reply?" Premium upsell modal** appears after every successful reply submit. Dismiss with `browser_press_key(key="Escape")` before the next click. Otherwise subsequent selectors get blocked.
- **Inline composer on `/home`** - `data-testid="tweetTextarea_0"` is live and editable right on the home timeline. Much easier than LinkedIn's broken portal modal.
- **Action button names carry counts** in IE-list `name` fields (e.g. `"977 Likes. Like"`, `"977 Likes. Liked"`). If you do fall back to `byob:idx`, match on the suffix (`Like`/`Liked`, `Repost`/`Reposted`).
- **"See new posts" banner** at top of feed. Click to refresh the For You tab when timeline returns empty (occasionally happens after navigation).

## Notes

- **Reuse the same `tabId`** across the session. Discover once at the start.
- **Screenshots over 1MB fail**. Use `format="jpeg"` and `quality=50-60`.
- **No fallback browser surface.** If BYOB transport errors mid-session: `cd ~/.byob && bun run doctor` to repair, then retry.
