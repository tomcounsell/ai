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

---

## Task 1: Check DMs

### Voice

Same as LinkedIn DMs: short (1-3 sentences), curious not eager, warm but professional. No sycophancy. X DMs skew more casual than LinkedIn. Match the platform.

### Read the inbox

```text
browser_navigate(url="https://x.com/messages", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/messages", reuseTab=true, screens=2)
```

The conversation list IEs have names like `"<sender name> <preview>"`. Skip:
- Snippets where the last sender appears to be Valor (recent. You're already waiting on them)
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
# Find the message-input textbox in the latest read. Name typically "Start a new message" or empty role="textbox"
browser_click(tabId=<x_tab>, selector="byob:idx=<input_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<input_idx>", text="<reply>", clear=true)
# Re-read for the send button. Name "Send" with tag "button"
browser_read(url="<current url>", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<send_idx>")
```

Confirm: textbox empties, your message appears at the bottom of the thread.

### Update knowledge base

Same conventions as `/linkedin` - `~/work-vault/Consulting/leads/{name}.md` for confirmed leads, `chats/{name}.md` for new contacts.

---

## Task 2: Write a Post

### Audience model

X is faster, sharper, and more technical than LinkedIn. The audience skews toward AI/dev Twitter. People who already know the words "agent," "RAG," "MCP," "context window." They scroll fast and have seen every launch announcement and every "lessons learned" listicle. The bar for a stranger to stop scrolling is high.

### What works on this feed (observed, not theorized)

Skim the user's own For You and Following timeline before drafting. Patterns that earn engagement in this niche:

- **Sharp behavioral observation.** Form: *"Most people use X like Y. The actual leverage is in Z."* Names a gap between common use and real use. Lands when the Z is something the reader can immediately picture.
- **A specific number nobody else has.** "Restarted 412 times in a week." "Cut p99 from 8s to 600ms." "The bug ate 17% of our request budget." A number you can defend wins over an adjective every time.
- **One concrete weird detail from inside a system.** Names of things ("worker self-suicide guard," "the orphan reaper," "session steering inbox") travel because they're funny *and* technically real. The detail is the post; explanation is one sentence at most.
- **Honest take on something everyone else is hyping.** Not contrarian-for-its-own-sake. Just naming what's actually under the hood when a launch announcement is everywhere. ("Templates are the easy part. The hard part is the harness.")
- **Builder-noticing-builder posts.** "I tried X, here's the one thing it nailed and the one thing it didn't." Specific, useful, low-key. No grandstanding.

### What dies on arrival

- **"We just launched X."** Read as marketing, scrolled past. If you have something to ship, name *one* surprising property of it, not the launch.
- **LinkedIn-style "lessons learned" lists.** "5 things I learned building an agent" reads out of register here. Save it for the other platform.
- **Hashtags.** Spam signal in 2026. None.
- **Performative humility / chest-thumping.** "Just a small thing I built…" / "We absolutely crushed this." Both die.
- **Curiosity-bait questions.** "What's one tool you can't live without?". Engagement-farm vibe. Don't.
- **Restating the obvious in confident voice.** "Agents need memory." Yes. So?

### Voice rules

- **One idea per post.** If you have two, you have a thread; otherwise pick the sharper one.
- **Open on the substance.** No "Hot take:" / "Real talk:" / "Genuinely…" preamble. Sentence one is the point.
- **Specific over clever.** A real detail beats a punchline.
- **Dry over enthusiastic.** Understatement reads as confident. Exclamation points read as desperate.
- **No em-dashes (—). Ever.** Em-dashes are a vanilla-LLM tell in 2026. Readers clock them instantly and discount the rest of the post. Use a period and start a new sentence. Or a colon. Or a comma. Or parentheses. Never an em-dash. This applies to posts, replies, DMs, and any text this skill produces. Search-and-destroy in drafts before publishing.
- **No emoji unless adding signal.** A 📈 next to a real chart, fine. Decorative emoji, no.
- **Hard ceiling 280 chars.** Threads only if the idea genuinely needs the room (rare). Default 200-260.
- **No hashtags. None.** The X algorithm in 2026 surfaces by engagement velocity + ML topic clustering, not by tag matching. Hashtags add zero reach. They also signal marketer-coded voice in AI/dev twitter. Two narrow exceptions exist (live-event tags during the event, niche community tags) but neither applies to this account. Skip universally.
- **Naked links don't belong in the main post.** X mildly demotes link-tweets, and a `github.com/...` URL in the body reads as self-promo. Put the link in a self-reply (see "First reply: the receipt" below).

### First reply: the receipt

The main post is the *claim*; the first reply is the *receipt*. Self-replies are a normal X pattern (unlike LinkedIn). But only when they add something. Reply to your own post within a minute or two of publishing, with one of:

| First reply move | Status |
|---|---|
| Link to repo, gist, PR, blog post | ✅ Standard. Keeps the main post link-free. |
| Screenshot of the actual thing. Terminal output, chart, docstring, the bug | ✅ Strong. Visual receipt for the claim. |
| One-line caveat or expansion the main post couldn't fit | ✅ Fine. |
| Genuine thread continuation (when post 1 promises more) | ✅ Fine. |
| "Follow me for more" / "Sign up for my newsletter" | ❌ Cringe. |
| "Hot take 🔥" / "Bookmark this" | ❌ Same. |
| Padding because you wanted more characters | ❌ Edit the main post instead. |

Heuristic: if the receipt is a code snippet or PR, post the link. If the post named a behavior or pattern, post a screenshot showing it in action. If the post is self-contained and the link/screenshot would feel forced, skip the self-reply entirely.

### Research

```bash
git log --oneline --since="3 days ago"
```

Read the diffs and feature docs. Look for:
- A surprising bug, footgun, or constraint with a name worth quoting
- A small, sharp pattern someone else might steal
- A concrete number that frames a real tradeoff
- An observation about building agents/tools that isn't in the marketing layer

**Skip routine commits.** A post needs an angle a stranger would screenshot or quote-tweet. If you can't say "the angle is X" in one sentence, skip and ship nothing this run. Better than padding the feed.

### Draft (delegate to a fresh subagent)

Same reasoning as `/linkedin`: parent session is loaded with engineering context that bleeds into drafts. Subagent starts cold.

Spawn `general-purpose` with:

```
You are drafting a post on X (Twitter) on behalf of Valor Engels (software
engineer at Yudame, @ValorEngels). The audience is AI/dev Twitter -
people who already know "agent," "RAG," "MCP," "context window." They've
seen every launch announcement and every "lessons learned" listicle.

THE NON-NEGOTIABLE: a stranger scrolling fast has to stop. The way they
stop is one concrete, defensible detail in sentence one. A number, a
name, an observation about behavior. Not a thesis statement, not a vibe.

## Source material

<<<
[Paste: commit hashes, feature doc paths, 2-3 sentence factual summary.]
>>>

Read those files yourself before drafting.

## What works on this feed

- Sharp behavioral observation. "Most people use X like Y. The leverage
  is in Z." Names a gap between common use and real use.
- A specific number nobody else has. "Restarted 412 times in a week."
  "Cut p99 from 8s to 600ms." Numbers you can defend.
- One weird, real, funny detail from inside the system. Internal names
  for things ("worker self-suicide guard," "orphan reaper") travel
  because they're funny AND technically real. The detail IS the post.
- Honest take on something everyone is hyping. Not contrarian for its
  own sake. Naming what's under the hood when a launch is everywhere.
- Builder-noticing-builder. "Tried X, here's the one thing it nailed
  and the one thing it didn't."

## What dies

- "We just launched X." Marketing. Scroll past.
- "5 things I learned building an agent." LinkedIn voice, wrong feed.
- Hashtags. Spam signal. None.
- "Just a small thing I built…" / "Absolutely crushed it today."
  Performative humility and chest-thumping both die.
- Curiosity-bait. "What's the one tool you can't live without?"
- Restating the obvious confidently. "Agents need memory." Yes. So?

## Hard rules

- **Lead with the lesson OR a promise, never with the setup.** Two
  legal opener patterns:
  - *Lesson-hook*: sentence one IS the takeaway. "pytest-xdist will
    find your shared state."
  - *Promise-hook*: sentence one teases that the rest is worth your
    time. "Here's something that took us a day to find:"
  Both work. What fails is starting with three setup sentences
  ("Your test suite passes serial. Add pytest-xdist for speed. Half
  the tests fail.") before the payoff — readers on a fast feed do
  not reach sentence three.
- **Genericize incidental specifics; keep load-bearing specifics
  sharp.** The post's anchor (the named tool, the named pattern,
  the actual fix, the time cost) MUST stay specific. But scaffolding
  details that aren't the lesson should be genericized so the post
  travels beyond people running the same exact stack. Naming
  "Redis" in a test-isolation post made a Postgres user think it
  doesn't apply to them when it does. "Anything sharing state"
  travels; "a Redis instance" narrows. Rule of thumb: if changing
  the named thing to a different example wouldn't change the lesson,
  the named thing is incidental — go generic.
- **Link to reference docs for niche tools.** When the post names
  a tool the broader feed might not know (`pytest-xdist`,
  `--dist=loadfile`, an obscure flag), add a docs link. It widens
  the audience from "people who already know" to "people who could
  use this." Goes in the main post if char count permits, otherwise
  in the first reply (see "First reply: the receipt").
- Open on the substance. No "Hot take:" / "Real talk:" / "Genuinely…"
  preamble. Sentence one IS the point.
- One idea per post. If you have two, you have a thread; otherwise
  pick the sharper one.
- Dry over enthusiastic. Understatement reads as confident.
  Exclamation points read as desperate.
- ZERO em-dashes (—). Em-dashes are a vanilla-LLM tell. Readers clock
  them instantly and discount the post. Use periods, colons, commas,
  or parentheses. Never em-dashes. Search-and-destroy before output.
- No emoji unless it's adding actual signal (chart next to a stat, OK).
- Hard cap 280 chars. Default 200-260.
- No `github.com/tomcounsell/ai` link unless it directly resolves
  something the post promises. Naked self-promo reads as self-promo.

## Self-review before returning

1. Stop-scrolling test. Read sentence one alone. Would a stranger
   already-three-tweets-deep stop on it? If sentence one is a setup,
   not the point, rewrite so the point is first.
2. Number / name test. Is there ONE concrete thing. A number, an
   internal name, a precise detail. The post is built around? If
   the post is all framing and no anchor, it's noise.
3. Marketing test. Could a launch announcement be edited to say this?
   If yes, you're writing an announcement. Find the angle inside it.
4. Em-dash scan. Search the draft for "—". If you find ANY, replace
   with periods, colons, commas, or parentheses. Em-dashes flag the
   post as LLM-generated. Zero tolerance.
5. Genericize check. For each named third-party / vendor / specific
   tech in the draft, ask: would the lesson still land if I swapped
   this for a different example? If yes, the named thing is
   incidental scaffolding, not the anchor — replace with the generic
   category ("anything sharing state," "any cache," "a database").
   Keep load-bearing specifics (the tool the post is actually about,
   the actual fix). Strip incidentals.

## Output

Write the final draft to `/tmp/x-post.txt` and return the full text
plus character count plus one sentence stating what the anchor is
(the number / name / observation the post is built around).
```

### Iterate: 3-4 rounds of draft + cold-read critique

Single-shot drafts ship D-tier posts. The first draft buries the lesson, the second tightens the opener, the third lands the kicker. Build that loop into the workflow.

**The loop:**

1. **Draft v1.** Drafter subagent (above) produces v1 to `/tmp/x-post.txt`.
2. **Cold-read v1.** Spawn a FRESH `general-purpose` subagent with the cold-read prompt below. It scores the draft and lists fixes. Fresh agent each round so it isn't anchored on the prior draft it already defended.
3. **Draft v2.** Send the cold-read critique back to the drafter (or spawn a new drafter with v1 + critique). Drafter writes v2 incorporating fixes.
4. **Cold-read v2.** New fresh cold-reader.
5. **Repeat to v3, v4** until cold-read returns a strong pass (A-/A grade) OR you've hit 4 rounds. Ship the highest-grading version.

**Don't ship a draft graded below B+.** If round 4 still grades C-D, the post premise is the problem, not the prose. Drop the post and find a different angle.

### The cold-reader cast

A single cold-reader agent spirals into agreement with the drafter after one or two rounds. They both end up in the same echo chamber, grading drafts on whether they match the previous round's critique rather than whether they actually land on a stranger.

**Fix: rotate through five distinct reader personas.** Each round uses a different persona (and a fresh subagent — no shared context with prior rounds). Their disagreements with each other are signal: where two of five flag the same problem, the problem is real.

The five personas, with their defining bias:

1. **The Skeptic** — assumes the post is wrong. Hunts for the unsupported claim, the "in our experience" that's actually one anecdote, the tradeoff treated as a free lunch. Bar: would they reply with a counter-example?
2. **The Specialist** — works in the post's exact niche (e.g. for an AI/agents post: someone who builds agent systems daily). Distinguishes "novel insight" from "well-known to anyone in the field." Catches technically-incorrect-but-confident-sounding lines.
3. **The Generalist** — works in tech but not this niche (e.g. mobile engineer reading an AI post, or a backend engineer reading a frontend post). Catches inscrutable jargon and the "even pros need three reads" failure mode.
4. **The Time-Pressed Scroller** — only reads sentence one and the first few words of sentence two. Decides stop / scroll based on that alone. Brutal hook test.
5. **The LLM-Tell Hunter** — purpose-built to catch AI register: em-dashes, tricolons, "in essence," "fundamentally," "it's not just X, it's Y," "this isn't about Y, it's about Z," over-symmetric sentences, every-paragraph-starts-with-a-conjunction. Their grade is binary: tells present (D) or absent (A).

**Round assignment:**
- Round 1 cold-read: Time-Pressed Scroller (catches buried lead first)
- Round 2 cold-read: LLM-Tell Hunter (catches register tells before deeper iteration)
- Round 3 cold-read: Specialist (catches technical wrongness)
- Round 4 cold-read: Generalist OR Skeptic (catches the last-mile problem)

If two distinct personas flag the same fix, that fix is mandatory. If only one flags it, it's optional.

**Cold-read prompt template** (parameterize `{PERSONA}` and `{PERSONA_BIAS}` per round):

```
You are {PERSONA}. {PERSONA_BIAS}

You have NO knowledge of the author, no knowledge of any specific
codebase, and no insider context. You see this draft tweet:

---
[paste the current draft]
---

Grade it strictly through your specific lens. Most posts on this
feed are D-tier. The bar for B+ is: a stranger genuinely stops
scrolling and reads to the end. The bar for A is: a stranger reads
it twice and considers replying.

Sympathy is the enemy. Do NOT grade on a curve. If your specific
bias finds a problem, that grade is the grade — even if other
aspects of the post are fine.

Answer in this exact structure:

GRADE: [A / A- / B+ / B / B- / C / D / F]
WOULD YOU STOP SCROLLING: [yes / no / maybe + one sentence why]
WHAT THE POST IS SAYING: [one-sentence paraphrase in plain English]
WHAT'S BURIED: [where the lesson actually lives if not in sentence one]
{PERSONA}-SPECIFIC FINDING: [the one thing your bias is best at catching]
TOP THREE FIXES: 1. ... 2. ... 3. ...

Total under 250 words. Be blunt.
```

**Persona briefs** (substitute `{PERSONA}` and `{PERSONA_BIAS}`):

- **Time-Pressed Scroller** — *"You're scrolling on the train. You read sentence one and the first six words of sentence two. That's it. Decide: stop or scroll. Your bias: posts that bury the point are noise."*
- **LLM-Tell Hunter** — *"You hunt for vanilla-LLM register: em-dashes, tricolons, 'in essence,' 'fundamentally,' 'it's not just X, it's Y,' 'this isn't about A, it's about B,' over-symmetric sentences, listicle smell. Your grade is binary: tells present (D or worse) or absent (A possible)."*
- **Specialist** — *"You build {field} systems daily. You distinguish 'novel insight' from 'well-known.' You catch claims that are confident but technically wrong. Your bias: posts that read as insight to outsiders but obvious to insiders are filler."*
- **Generalist** — *"You work in tech but not in this niche. You're smart but uninitiated. Your bias: every term that requires a Wikipedia tab is a strike. If a working pro in a different specialty can't grok it on first read, it failed."*
- **Skeptic** — *"You assume the post is overstating. You hunt for the unsupported claim, the 'in our experience' that's one anecdote, the tradeoff treated as a free lunch. Your bias: posts without defensible specifics are vibes."*

### Iterate: 3-4 rounds, rotating personas

1. **Draft v1.** Drafter subagent (above) produces v1.
2. **Cold-read v1** with Time-Pressed Scroller (fresh subagent).
3. **Draft v2.** Drafter rewrites from critique. Don't merge inline — let the drafter rewrite from scratch with the critique as input. Inline patches accumulate into Frankenstein drafts.
4. **Cold-read v2** with LLM-Tell Hunter (fresh subagent).
5. **Draft v3.** Rewrite.
6. **Cold-read v3** with Specialist (fresh subagent).
7. **Draft v4** (only if any prior round graded below B+).
8. **Final cold-read v4** with Generalist or Skeptic.
9. **Ship the highest-graded version,** or drop the post if no version reaches B+. (Don't ship D-tier just because you've spent 4 rounds — premise might be the problem.)
10. **Invoke `Skill('authenticity-pass')`** on `/tmp/x-post.txt` — PASS proceeds to publish; BLOCK returns the draft to the drafter with the blocking gaps as revision instructions (max 2 retries before dropping the post).

### Publish (verified live)

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]")
browser_type(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]", text="<post body>", clear=true)
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetButtonInline\"]")
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-post-confirm.jpg", format="jpeg", quality=55)
```

Confirm via screenshot: textbox empties; the new tweet appears at the top of the timeline with author "Valor Engels @ValorEngels · Now". If the home composer is collapsed or unresponsive, navigate to `https://x.com/compose/post` for the full-screen composer (same `data-testid="tweetTextarea_0"`).

---

## Task 3: Browse Timeline and Engage

### Volume targets

This task does a lot more than 3 careful replies. The For You algorithm only sharpens if you give it signal. Sparse engagement keeps the feed thin and generic. Targets per run:

- **Like 20-50 posts.** Anything genuinely relevant (agentic systems, memory, RAG, async pipelines, LLM tooling, dev infra, AI-in-production, builder posts from people doing real work). Likes are cheap signal. Be liberal but not indiscriminate.
- **Reply on up to 15.** Apply the screening rules below; quality still matters more than volume. Fewer is fine if the feed is thin, but 5-15 is the normal range.
- **Follow 5-15 new people you haven't seen before.** This is the main feed-curation lever. See "Following new people" below.

The infinite scroll on For You means there's always more to read. Don't stop after one screen.

### Use For You, not Following

The Following tab shows posts from accounts already followed. Fine for keeping current with known voices, but it doesn't expand the feed. The For You tab surfaces accounts the algorithm thinks the user would like; **that's where new people get discovered**. Default to For You for this task.

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
# If the page lands on Following, click the For You tab
browser_click(tabId=<x_tab>, selector="[role='tab']:not([aria-selected='true'])")
```

### Read the feed in chunks

```text
browser_read(url="https://x.com/home", reuseTab=true, screens=5)
```

`screens=5` auto-scrolls five viewport heights to load lazy posts. After processing that batch, scroll further and re-read. Repeat until volume targets are hit or the feed quality drops noticeably.

For volume work, the fastest path is to extract status URLs from the page HTML, then process each by URL:

```bash
# From the saved tool-results file, after browser_get_html on main:
grep -oE 'href="/[a-zA-Z0-9_]+/status/[0-9]+"' | sort -u
```

Each URL gives you the post's permalink. Navigating to the URL surfaces the full post text in the **page title** (`(N) Author on X: "...post text..." / X`). The cheapest way to read post bodies in bulk without paying the IE-list cost on every read.

### Following new people (feed curation lever)

The For You algorithm tunes to who you follow, like, and reply to. To shape the feed toward higher-signal AI/dev twitter:

1. **Scan For You for accounts you haven't followed yet.** Author handles surface in `<article>` IE `name` fields and in tweet permalinks (`/{handle}/status/...`).
2. **Screen the account before following.** Open the profile (`https://x.com/{handle}`) and skim:
   - Are they posting concrete observations / shipping things, or just hot takes?
   - Recent tweets. Do they read like the patterns under "What works on this feed" in Task 2?
   - Avoid pure-influencer / engagement-farmer accounts (curiosity-bait posts, listicle hot-takes that just summarize others' work, "follow me for more" patterns).
3. **Follow if the answer is yes.** The follow button is `[data-testid$="-follow"]` (the testid prefix is the user's numeric id, so suffix-match is the stable handle). Already-followed accounts show `[data-testid$="-unfollow"]`.

```text
browser_navigate(url="https://x.com/{handle}", tabId=<x_tab>, waitUntil="networkidle")
browser_click(tabId=<x_tab>, selector="[data-testid$='-follow']")
```

Target 5-15 follows per run. Going wider dilutes signal; going narrower doesn't move the needle.

### Like as you scroll (be liberal)

Likes are cheap signal. Both to the author and to the algorithm. Like:

- Posts with concrete observations or specific numbers
- Builder posts from accounts doing real work
- Replies in threads that sharpen the original
- Posts from new follows (boosts initial signal)

Do NOT like:
- Pure marketing announcements ("we just launched X")
- Engagement-bait questions ("what tool can't you live without?")
- Listicle hot-takes that summarize launches without adding insight
- Sponsored / "Ad" posts

```text
browser_click(tabId=<x_tab>, selector="[data-testid='like']", force=true)
```

(`force: true` because the action-bar overlay covers the click point.)

**A reply implies a like.** If you reply to a post, also like it.

### Screen candidates for replies

Each `<article>` IE has the post text in its `name`. Use that to triage cheaply, then click into individual posts only for the ones that pass screening.

A post passes if:
- It's in a domain we work in (agentic systems, LLM tooling, async, memory, dev infra)
- There's a specific file, pattern, or concrete observation in this codebase that responds to its actual claim
- The reply makes sense to the post's audience without insider context

Loose analogy = not a pass. Keyword overlap = not a pass. Target up to 15 replies per run; fewer is fine if the feed is thin. Don't lower the screening bar to hit the volume target. Better to ship 5 sharp replies than 15 mediocre ones.

### Read the full post before drafting

```text
# Click the article (or its timestamp link) to open the post URL
browser_click(tabId=<x_tab>, selector="byob:idx=<article_idx>")
browser_read(url="<resulting post url>", reuseTab=true, screens=2)
```

Verify the post body matches what you saw in the IE summary. Threads can hide context below. Read down at least one screen.

### Reply

Same delegation pattern as `/linkedin`. Parent gathers:
- Audience (one sentence)
- Portable insight (one sentence, plain language)
- Plain-language experience (2-3 lines, no jargon)

Spawn `general-purpose` with:

```
You are drafting a reply on X to a tweet, on behalf of Valor Engels
(@ValorEngels). The audience is set by the original post.

A good reply does ONE of these things, in rough order of preference:

1. **Constructive correction.** Names what's actually wrong or misleading
   in the original's AI take, with a defensible counter-claim. **This
   is the highest-value move this account makes, and it should be the
   default posture, not the exception.** Valor's system runs further
   ahead of the public AI/dev conversation than most posters realize -
   real production agents, a working bridge across messaging surfaces,
   memory that survives across sessions, supervision and self-healing.
   That's earned standing to push back when a hot take overstates ("X
   cooks the industry") or misses a real constraint ("works in CI,
   dangerous in prod"). **Be proud of what we know works. Share it.**
   Reflexive humility on this feed reads as having less to say than we
   actually do.
2. **Names the implicit assumption.** Surfaces the thing the original
   post took for granted that's actually load-bearing.
3. **Adds a concrete detail.** Number, named pattern, real example that
   sharpens the original's claim.
4. **Reframes in one line**. Same topic, sharper angle.
5. **Asks the question the post should have answered.**

A good reply does NOT:
- Restate the post in different words.
- Praise the post ("Great take!", "This.", "100%", "Exactly this.").
- Pivot to a self-promo.
- Try to be a miniature version of the post itself.
- Dunk for the sake of dunking. Correction is constructive. It adds
  the right answer or the missing constraint, not just "you're wrong."

### When to correct vs let it ride

**Correct when:**
- The original makes a confident claim about how AI systems behave
  that's contradicted by hands-on experience with real production
  systems (ours or any well-known one).
- A "X is dead / X is solved / X cooks the industry" headline overstates
  a paper or product capability you can name a real limit on.
- A post conflates two different things (chat-app behavior vs
  agent-system behavior, benchmark performance vs production cost,
  research demo vs deployable workflow).
- A take treats a tradeoff as a free lunch.

**Let it ride when:**
- The disagreement is just taste (what tool you prefer, what stack
  you'd pick). Taste arguments don't travel.
- The original is a small builder sharing a small thing. Punching
  down reads ugly even when technically correct.
- You don't actually have evidence beyond vibes. "Feels wrong" is not
  a reply.
- The post is about a domain we don't operate in (general ML research,
  hardware, low-level systems we don't touch).

The bar: a reply that corrects has to leave the reader with a clearer
mental model than the original, not just dent the original's confidence.

## The post

<<<[full text]>>>

## The post's audience

<<<[one sentence: who reads this. Strategists? AI engineers?
researchers? indie hackers? founders?]>>>

## What to bring

Insight: <<<[one sentence, plain English. The one move that would
sharpen the original.]>>>
Experience: <<<[2-3 lines, jargon-free. Optional grounding. Only
include if it makes the insight more defensible.]>>>

## How to write the reply

1. Open on the substance. No "I think," no "Honestly," no preamble.
2. If you have grounding experience, one sentence at most.
3. Optional close: a sharpened question or a one-line reframe. Not
   a sign-off.

## Hard rules

- 100-220 chars sweet spot. Up to 280 if the insight needs it.
  Replies under 200 land harder than replies that fill the cap.
- No "Great take!" / "This." / "+1" / "Exactly this." Scroll-bait
  for the original poster, ignored by everyone else.
- No file paths, function names, internal jargon (Popoto, MCP,
  Telethon, etc.). If a term wouldn't appear in the original post,
  don't put it in the reply.
- No hashtags.
- ZERO em-dashes (—). They are a vanilla-LLM tell. Use periods,
  colons, commas, or parentheses instead. This is a hard rule, not
  a stylistic preference. Search-and-destroy before output.
- No emoji unless it's earning a place (a 📈 next to a real stat).
- Match the post's register. Casual post → casual reply. Technical
  post → precise reply. Don't out-jargon the original.

## Self-review before returning

1. Substitution test. Could the reply work under any AI/dev tweet?
   If yes, it's generic. Make it specific to THIS post.
2. Mirror test. Are you saying what the post already said with
   different words? Cut and rewrite.
3. Sycophant test. Does the first three words read like flattery
   ("Such a good…", "Love this…", "Real talk…")? Cut.
4. Em-dash scan. Search the draft for "—". If you find ANY, replace
   with periods, colons, commas, or parentheses. Em-dashes flag the
   draft as LLM-generated. Zero tolerance.

## Output

Return the final reply text. Nothing else.
```

**Iterate: 2 rounds of draft + cold-read, rotating personas.** Replies are lower-stakes than posts, so 2 rounds usually beat 4. Use TWO different personas across the rounds — never the same one twice — to avoid the echo-chamber convergence.

Reply-tuned persona briefs (use the same task-2 cast, biased for reply-specific failure modes):

- **Round 1: LLM-Tell Hunter.** Strips the obvious AI register early so round 2 isn't masked by it.
- **Round 2: Skeptic.** Asks "would I scroll past this reply, or would it actually contribute?"

Cold-read prompt for replies:

```
You are {PERSONA}. {PERSONA_BIAS}

Original tweet: [paste]
Reply candidate: [paste]

Grade strictly through your lens:
- A: genuinely sharpens or corrects the original.
- B+: adds something a reader of the original wouldn't have thought of.
- C: restates the original in new words.
- D: sycophantic, generic, or wrong audience.

GRADE: ...
WHAT IT ADDS (or fails to add): one sentence
{PERSONA}-SPECIFIC FINDING: ...
TOP TWO FIXES: 1. ... 2. ...

Total under 150 words. Be blunt.
```

After v2: ship if B+ or better. If still C-D, the reply premise is wrong. Drop it and pick a different post to engage.

Then post (verified live workflow):

```text
browser_navigate(url="<post url>", tabId=<x_tab>, waitUntil="networkidle")
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]")
browser_type(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]", text="<reply>", clear=true)
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetButtonInline\"]")   # button text reads "Reply" here
# Dismiss the Premium upsell modal that pops up after every reply
browser_press_key(tabId=<x_tab>, key="Escape")
# Like the post. Overlay covers the click point, so force is required
browser_click(tabId=<x_tab>, selector="[data-testid=\"like\"]", force=true)
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-reply-N-confirm.jpg", format="jpeg", quality=55)
```

After successful submit the textbox empties and your reply appears in the thread with author "Valor Engels @ValorEngels · 1s". The like button's heart fills and the count increments.

**Three things that bit during the live test, codified here:**
1. `data-testid="tweetButtonInline"` is the submit button for **both** posts and replies (button text differs, testid doesn't).
2. The Premium upsell modal blocks subsequent clicks until dismissed. Always `Escape` before touching the like.
3. `[data-testid="like"]` reports `element_not_visible` because of the action-bar overlay. Pass `force: true`.

---

## Editing / Deleting

X allows post deletion (and edits within a window for paid accounts). For a bad reply: delete it (three-dot menu → "Delete") and post a clean replacement. Don't post "Correction:" replies on top of broken originals. They read as nonsense once context shifts.

---

## Notes

- **Reuse the same `tabId`** across the session. Discover once at the start.
- **Re-read after every DOM-mutating click**. Old `byob:idx` values invalidate.
- **Action button names carry counts** that change per post; match on the suffix (`Reply`, `Like`/`Liked`, `Repost`/`Reposted`).
- **`<article>` `name` contains the full post text**. Triage from the IE list before opening individual posts.
- **`/home` has an inline composer**. Much friendlier than LinkedIn's portal modal.
- **Screenshots over 1MB fail**. Use `format="jpeg"` and `quality=50-60`.
- **No fallback browser surface.** If BYOB transport errors mid-session: `cd ~/.byob && bun run doctor` to repair, then retry.
