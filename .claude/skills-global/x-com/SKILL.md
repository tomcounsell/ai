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

1. **Check DMs** — reply to any messages that need attention
2. **Write a post** — about recent work from git history
3. **Browse timeline** — like and reply to 3 posts

If arguments are given, interpret them and do only what's asked.

**Execute, don't pause.** Render drafts inline and continue to publish in the same turn. Only stop on hard tool failure with no fallback, or a finding that contradicts the skill's premise (e.g. "no DMs need replies" → skip Task 1).

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
valor-session create --role dev --project-key valor --needs-real-chrome --message "check my X DMs"
```

## Tab discovery (do this first)

```text
browser_list_tabs   →   look for any tab whose URL contains "x.com" (or "twitter.com")
```

Pick the first match → that's your `tabId`. If no tab, `browser_navigate(url="https://x.com/home")`.

## DOM model

X uses a stable accessibility tree — element names like "Reply", "Like", "Liked", "Repost", "Reposted", "Post text", "Direct Messages" are reliable. Hashed CSS classes change frequently; **do not rely on them**. Use `data-byob-idx` via the `selector: "byob:idx=N"` workflow.

### The `byob:idx` workflow

1. `browser_read(url, reuseTab=true, screens=N)` returns `interactiveElements: [{idx, tag, role, name, bounds}, ...]`.
2. Find your target by `name`. On X the post text is captured directly in the `<article>`'s `name` attribute — this is a big advantage over LinkedIn (post bodies are readable from the IE list).
3. Click with `browser_click(tabId, selector="byob:idx=<idx>")`.
4. **Re-read after every DOM-mutating click** — the `interactiveSessionTag` changes and old indices invalidate.

When multiple IEs map to the same logical control, prefer `tag: "button"`.

### X-specific gotchas

- **Inline composer on /home**: there is a `role: "textbox"` `name: "Post text"` directly on the home timeline (no portal modal). This is much easier than LinkedIn's broken composer flow.
- **Action buttons carry counts in their `name`**: e.g. `name: "147 Replies. Reply"`, `name: "977 Likes. Like"` (or `"Liked"` after liking). Match on the *suffix* (`Reply`, `Like`/`Liked`, `Repost`/`Reposted`) — the count varies per post.
- **`<article>` IEs include the full post text in `name`** — useful for screening without opening each post. Author handles also surface as `@handle` substrings.
- **"See new posts" banner** at top of feed (idx in the 120s area). Click to refresh; otherwise ignore.
- **"Show more" expanders** on long posts — click to expand the article body in the IE list.

---

## Task 1: Check DMs

### Voice

Same as LinkedIn DMs: short (1-3 sentences), curious not eager, warm but professional. No sycophancy. X DMs skew more casual than LinkedIn — match the platform.

### Read the inbox

```text
browser_navigate(url="https://x.com/messages", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/messages", reuseTab=true, screens=2)
```

The conversation list IEs have names like `"<sender name> <preview>"`. Skip:
- Snippets where the last sender appears to be Valor (recent — you're already waiting on them)
- Obvious spam / mass DMs
- Unsolicited sales/crypto pitches

If nothing remains, say "no DMs need replies right now" with a one-line reason and move on.

### Open a thread

```text
browser_click(tabId=<x_tab>, selector="byob:idx=<conversation_idx>")
browser_wait_for(tabId=<x_tab>, selector="[data-testid='dmDrawer'], [aria-label*='Message']", state="visible", timeoutSec=5)
browser_read(url="<current url>", reuseTab=true, screens=2)
```

Read the thread to understand context. Then check the work vault before replying:

```bash
ls ~/work-vault/Consulting/leads/
ls ~/work-vault/Consulting/chats/
```

- Known lead/chat → read their file, reply with awareness
- Unknown → friendly and curious
- Spam → skip

### Send the reply

Write draft to `/tmp/x-dm-reply.txt`. Then:

```text
# Find the message-input textbox in the latest read — name typically "Start a new message" or empty role="textbox"
browser_click(tabId=<x_tab>, selector="byob:idx=<input_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<input_idx>", text="<reply>", clear=true)
# Re-read for the send button — name "Send" with tag "button"
browser_read(url="<current url>", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<send_idx>")
```

Confirm: textbox empties, your message appears at the bottom of the thread.

### Update knowledge base

Same conventions as `/linkedin` — `~/work-vault/Consulting/leads/{name}.md` for confirmed leads, `chats/{name}.md` for new contacts.

---

## Task 2: Write a Post

### Audience model

X is faster, sharper, and more technical than LinkedIn. The audience skews toward AI/dev/tech Twitter. Expectations:
- **Short** — 240-280 chars is the sweet spot. Threads only when warranted.
- **Concrete** — a specific observation beats abstract takes. Numbers and small details land.
- **Voice** — Valor's own. No corporate posture, no LinkedIn-style "lessons learned." Dry, specific, sometimes funny.
- **No hashtags** — X norms changed; hashtags read as spam now. Skip them.
- **Link**: include `github.com/tomcounsell/ai` only if directly relevant.

### Research

```bash
git log --oneline --since="3 days ago"
```

Read the relevant files. Look for:
- A surprising bug or footgun worth naming
- A small, sharp pattern someone else might steal
- A concrete number (latency, rate, count) that frames a real tradeoff
- An honest observation about building agents/tools that's not in the marketing layer

**Skip routine commits.** A post needs a hook a stranger would actually click on or quote-retweet.

### Draft (delegate to a fresh subagent)

Same reasoning as `/linkedin`: parent session is loaded with engineering context that bleeds into drafts. Subagent starts cold.

Spawn `general-purpose` with:

```
You are drafting a post on X (Twitter) on behalf of Valor Engels (software
engineer at Yudame, @ValorEngels). The audience is AI/dev Twitter — sharp,
technical, fast. They scroll past anything that reads like marketing.

## Source material

<<<
[Paste: commit hashes, feature doc paths, 2-3 sentence factual summary.]
>>>

Read those files yourself before drafting.

## What makes a good X post

- 240-280 chars. Hard cap 280 unless explicitly told to thread.
- One concrete observation. A surprising bug, a small pattern, a real
  number, an honest take. Specific beats clever.
- Voice: Valor's own. Dry, specific, sometimes funny. No corporate
  posture, no "lessons learned" framing.
- No hashtags. No emoji unless it's actually adding signal.
- No "we just shipped" framing — that's announcement noise.
- A reader who isn't a customer should still find it interesting.

## Output

Write the final draft to `/tmp/x-post.txt` and return the full text in
your reply. Include character count.
```

When the subagent returns:
1. Read `/tmp/x-post.txt`
2. Sanity check: under 280 chars? Concrete? Doesn't read like a press release?
3. Render inline in the response
4. **Same turn**, publish.

### Publish

The inline composer on `/home` works (verified — unlike LinkedIn's portal modal):

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/home", reuseTab=true, screens=1)
# Find the textbox: name "Post text", role "textbox"
browser_click(tabId=<x_tab>, selector="byob:idx=<post_text_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<post_text_idx>", text="<post body>", clear=true)
# Re-read — typing enables a "Post" submit button (button tag, name "Post")
browser_read(url="https://x.com/home", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<post_submit_idx>")
# Confirm: textbox empties; new tweet appears at top of timeline
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-post-confirm.jpg", format="jpeg", quality=55)
```

If the inline composer is collapsed or doesn't accept input, fall back to the dedicated composer route: `browser_navigate(url="https://x.com/compose/post", ...)` which loads a full-screen composer with the same `name: "Post text"` textbox.

---

## Task 3: Browse Timeline and Engage

### Read the timeline

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/home", reuseTab=true, screens=5)
```

`screens=5` auto-scrolls to load lazy posts. Bump higher if needed.

### Like as you scroll

Like any post relevant to the work — agentic systems, memory, RAG, async, dev tooling, AI in production.

In the IE list, like buttons are named like `"977 Likes. Like"`; after liking, the same button's name flips to `"Likes. Liked"` (or includes the new count). If already `"Liked"`, skip.

```text
browser_click(tabId=<x_tab>, selector="byob:idx=<like_idx>")
```

**A reply implies a like.** If you reply to a post, also like it.

### Screen candidates for replies

Each `<article>` IE has the post text in its `name`. Use that to triage cheaply, then click into individual posts only for the ones that pass screening.

A post passes if:
- It's in a domain we work in (agentic systems, LLM tooling, async, memory, dev infra)
- There's a specific file, pattern, or concrete observation in this codebase that responds to its actual claim
- The reply makes sense to the post's audience without insider context

Loose analogy = not a pass. Keyword overlap = not a pass. Target 3 posts; fewer is fine if the feed is thin.

### Read the full post before drafting

```text
# Click the article (or its timestamp link) to open the post URL
browser_click(tabId=<x_tab>, selector="byob:idx=<article_idx>")
browser_read(url="<resulting post url>", reuseTab=true, screens=2)
```

Verify the post body matches what you saw in the IE summary. Threads can hide context below — read down at least one screen.

### Reply

Same delegation pattern as `/linkedin`. Parent gathers:
- Audience (one sentence)
- Portable insight (one sentence, plain language)
- Plain-language experience (2-3 lines, no jargon)

Spawn `general-purpose` with:

```
You are drafting a reply on X to a tweet, on behalf of Valor Engels
(@ValorEngels). The audience is set by the original post.

## The post

<<<[full text]>>>

## The post's audience

<<<[one sentence]>>>

## What to bring

Insight: <<<[one sentence, plain]>>>
Experience: <<<[2-3 lines, jargon-free]>>>

## How to write the reply

- Lead with the insight in plain language. Reply readers scroll fast.
- Optionally one sentence of grounding experience.
- Often best as a question or a sharper observation that invites
  thought.

## Hard rules

- 240-280 chars max. Replies under 200 chars usually land harder.
- No file paths, function names, internal terms (Popoto, MCP, RAG, etc.).
- No sycophantic opener ("Great take!"). No "this." replies.
- No hashtags.
- Match the post's voice. If it's casual, be casual. If it's technical,
  be precise.

## Output

Return the final reply text. Nothing else.
```

Verify against the rules. Then post:

```text
# On the post page, find the reply textbox: name "Post your reply", role "textbox"
browser_read(url="<post url>", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<reply_textbox_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<reply_textbox_idx>", text="<reply>", clear=true)
# Re-read for the Reply submit button (button tag, name "Reply")
browser_read(url="<post url>", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<reply_submit_idx>")
# Also like the post (re-read first if you haven't already)
browser_click(tabId=<x_tab>, selector="byob:idx=<like_idx>")
# Confirm
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-reply-N-confirm.jpg", format="jpeg", quality=55)
```

After successful submit the textbox empties and your reply appears in the thread with author "Valor Engels @ValorEngels" and timestamp "now".

---

## Editing / Deleting

X allows post deletion (and edits within a window for paid accounts). For a bad reply: delete it (three-dot menu → "Delete") and post a clean replacement. Don't post "Correction:" replies on top of broken originals — they read as nonsense once context shifts.

---

## Notes

- **Reuse the same `tabId`** across the session. Discover once at the start.
- **Re-read after every DOM-mutating click** — old `byob:idx` values invalidate.
- **Action button names carry counts** that change per post; match on the suffix (`Reply`, `Like`/`Liked`, `Repost`/`Reposted`).
- **`<article>` `name` contains the full post text** — triage from the IE list before opening individual posts.
- **`/home` has an inline composer** — much friendlier than LinkedIn's portal modal.
- **Screenshots over 1MB fail** — use `format="jpeg"` and `quality=50-60`.
- **No fallback browser surface.** If BYOB transport errors mid-session: `cd ~/.byob && bun run doctor` to repair, then retry.
