# Task 3: Browse Feed and Comment

## Volume targets

The LinkedIn feed algorithm sharpens to who you like, comment on, and
follow. Sparse engagement keeps the feed thin. Targets per run:

- **Like 15-40 posts.** Anything genuinely relevant (agentic systems,
  memory, async pipelines, LLM tooling, dev tools, AI in production,
  builder posts from people doing real work, plus adjacent design /
  product / strategy posts that read as substantive). Likes are cheap
  signal. Be liberal but not indiscriminate.
- **Comment on up to 5.** Apply the screening gate below. Quality still
  matters more than volume. 2-5 is the normal range; fewer is fine if
  the feed is thin.
- **Follow 3-10 new people you haven't connected with.** This is the
  main feed-curation lever (see "Following new people" below).

Infinite scroll means there's always more to read. Don't stop after one
screen if the feed is yielding signal.

## Read the feed

```text
browser_navigate(url="https://www.linkedin.com/feed/", tabId=<linkedin_tab>, waitUntil="networkidle")
browser_read(url="https://www.linkedin.com/feed/", reuseTab=true, screens=5)
```

`screens=5` lets BYOB auto-scroll five viewport heights to load lazy posts. Bump to 10+ if you need more. If the read returns `stopReason: "limit_reached"`, call `browser_read` again to get the next slice (the IE indices reset; `interactiveSessionTag` changes).

## React as you scroll

As you read through posts, Like any that are relevant to the work — agentic systems, memory, RAG, async architecture, developer tooling, AI in production. This trains the feed toward more of the same. A thumbs-up takes one click and doesn't require a comment.

**Commenting always implies a Like.** If a post passes the screening gate and you draft a comment for it, also like the post. Engagement should be coherent — a comment without the like reads as half-engaged. The reverse isn't true: plenty of posts are worth a like but not a comment.

In the IE list, Like buttons appear with `name: "Reaction button state: no reactionLike"` (or `name: "Like"`). After clicking, the same button's name flips to `"Reaction button state: Like"` — that's the reliable success indicator (don't gate on reaction-count diffs, those can lag). If the name already shows `"Reaction button state: Like"` or `"Unreact Like"`, the post is already liked — skip.

```text
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<like_idx>")
```

## Following new people (feed curation lever)

The LinkedIn feed tunes to who you follow and engage with. To shape the
feed toward higher-signal builders / strategists / researchers:

1. **Scan the feed for authors you haven't followed yet.** Author names
   surface in `<article>` IE `name` fields and in post permalinks.
2. **Screen the profile before following.** Open `https://www.linkedin.com/in/{handle}/`
   and skim recent posts:
   - Are they posting concrete observations / shipping things, or just
     hot takes and reposts?
   - Do their posts read like the patterns under "What works on this
     feed"? (sharp behavioral observation, specific numbers, real
     examples, builder-noticing-builder)
   - Avoid pure-influencer accounts (engagement-bait questions, listicle
     hot-takes that just summarize others' work, "DM me for the
     framework" patterns).
3. **Follow if yes.** Follow buttons on LinkedIn vary by profile state.
   Use the IE list to find the button named "Follow" (not "Connect" or
   "Message"). Already-following accounts show "Following".

Target 3-10 follows per run.

## Screen candidates (mandatory before drafting)

For each candidate, grep the codebase for the post's core topic:

```
Check: tools/, bridge/, agent/, docs/features/, config/, .claude/skills/
```

**Before checking the codebase, classify the post cold:** Ignore the browsing session so far. Ask — what domain is this post actually in? Who is the intended audience? What would a relevant reply look like for *them*? Write that classification in one sentence before proceeding.

Then ask: does this codebase operate in that domain?

A post passes if:
- The post's domain is one we work in directly (agentic systems, memory, async pipelines, LLM tooling, etc.)
- There's a specific file, pattern, or decision in this codebase that speaks to the post's actual claim
- The comment would make sense to the post's audience without any context about Claude or AI agents

A loose analogy ("this reminds me of a different problem we had") is not a pass. Keyword overlap without domain overlap is not a pass.

Target: 3 posts that pass this gate. If the feed only yields 1-2 quality candidates, post fewer good comments rather than padding with weak ones.

## Read the full post body before drafting

The feed inline rarely shows the full post body in `browser_read`'s text or IE list. Always open the post URL directly before drafting:

```text
browser_navigate(url="https://www.linkedin.com/feed/update/urn:li:share:<id>/", tabId=<linkedin_tab>, waitUntil="networkidle")
```

Then either:
- `browser_read(url=..., reuseTab=true, screens=2)` — works on the dedicated post page (text usually surfaces in chunks), OR
- `browser_get_html(tabId, selector="main")` and grep visible text out

**Why this matters:** A live test once drafted a comment from the feed snippet alone and missed the post's actual punchline (the joke was buried below the visible fold). The author was making a satire about ChatGPT's sycophant tic; the draft had earnestly engaged with a parenthetical they'd thrown in. Always read the full body before extracting the audience and the insight.

## Audience model for comments

**The post sets the audience, not your codebase.** A strategy post's readers are PMs and execs — write for them. A design-system post's readers are designers — write for them. Only when an AI-engineering post is in front of a self-selected technical audience can you reach for technical framing, and even then with restraint.

Common failure mode: the parent session has just spent 20 minutes inside engineering files and writes a comment that mirrors that context — file paths, function names, internal terms — onto a post whose audience can't decode any of it. The codebase grounding is for *you*, not for the comment text. File paths and function names belong in the drafter's notes, never in the published reply.

## For each post: read, gather, delegate, verify

**Gather (parent session):** Inside the parent, do the codebase research and extract:
- The post's audience (one sentence — strategist? designer? AI engineer?)
- The portable insight you'd bring (one sentence in plain language — no jargon, no file paths)
- Two or three lines of plain-language experience that ground the insight (what we've seen, in everyday terms — "we run a system that decides which AI requests to skip when the inputs haven't changed" beats "JSON cache for deterministic call sites")

Pass these to a fresh subagent. The subagent does NOT see the codebase. It only sees what you tell it.

**Delegate to subagent (`general-purpose`)** with this prompt template:

```
You are drafting a LinkedIn comment on behalf of Valor Engels (software
engineer at Yudame). The audience is set by the original post — not by
Valor's codebase. Your job is to write a comment the post's audience will
find genuinely useful.

## The post

<<<
[Paste the full post text here.]
>>>

## The post's audience

<<<
[One sentence: who reads this post? Strategists? Designers? AI engineers?
Recruiters?]
>>>

## What to bring

Portable insight (the parent already extracted this for you):
<<<
[One sentence in plain language. The parent did the engineering research;
you just need to say it well for the audience.]
>>>

Plain-language experience that grounds it:
<<<
[2-3 lines, jargon-free. What we've seen. No file paths, no function
names, no internal terms.]
>>>

## How to write the comment

1. Lead with the portable insight, in language the post's audience uses.
   Comments are read top-down with attention falling fast — the insight
   goes in sentence one.
2. Add the experience as supporting evidence, in 1-2 sentences.
3. Optionally close with a question or sharpened line that invites
   thought, not a sign-off.

## Hard rules

- Match the post's audience, not Valor's codebase. If the post is in plain
  English, the comment is in plain English.
- No file paths (`config/models.py`, `MODEL_EXPERIMENT`), no function
  names, no internal terms (`Popoto`, `MCP`, `RAG`, `LLM call sites`),
  no Python identifiers. These are bragging tokens, not contributions.
  If a term you reach for would not appear in the post itself, don't put
  it in the comment.
- No sycophantic opener ("Great point!", "Love this!").
- No fake authority. The grounding is the experience the parent gave
  you, not invented detail.
- ZERO em-dashes (—). They are a vanilla-LLM tell in 2026. Use periods,
  colons, commas, or parentheses instead. Search-and-destroy before
  output.

## Length

Default ~200-300 characters. Go up to ~600 only if the insight genuinely
needs the room. Hard cap 1250 (LinkedIn limit).

## Self-review before returning

1. Audience check. Read your draft as if you were the post's audience
   (a strategist for a strategy post, a designer for a design post). Does
   it land? Or does sentence one use a term they'd skim past?
2. Bragging-token check. Search your draft for any file path, function
   name, or internal-jargon term. If present, rewrite without them.
3. Insight-up-front check. Is the portable insight in sentence one? If
   it's the closing line, move it.
4. Em-dash scan. Search the draft for "—". If you find ANY, replace
   with periods, colons, commas, or parentheses. Zero tolerance.

## Output

Return the final comment text. Nothing else.
```

**Iterate: 2 rounds of draft + cold-read with rotating personas.**
Comments are lower-stakes than posts, so 2 rounds usually beat 4. Use
TWO different personas across the rounds, never the same one twice, to
avoid echo-chamber convergence.

- **Round 1: LLM-Tell Hunter.** Strips obvious AI register early so
  round 2 isn't masked by it.
- **Round 2: Audience Stand-In.** A fresh subagent role-playing the
  post's actual audience (strategist for a strategy post, designer for
  a design post, etc.). Asks "would this comment land for *me*?"

Cold-read prompt for comments:

```
You are {PERSONA}. {PERSONA_BIAS}

Original post: [paste]
Comment candidate: [paste]

Grade strictly through your lens:
- A: genuinely sharpens, corrects, or extends the original.
- B+: adds something a reader of the original wouldn't have thought of.
- C: restates the original in new words.
- D: sycophantic, generic, or wrong audience.

GRADE: ...
WHAT IT ADDS (or fails to add): one sentence
{PERSONA}-SPECIFIC FINDING: ...
TOP TWO FIXES: 1. ... 2. ...

Total under 150 words. Be blunt.
```

After v2: ship if B+ or better. If still C-D, the comment premise is
wrong. Drop it and pick a different post to engage.

**Verify (parent session):**
1. Read the final draft against audience-check, bragging-token, and
   insight-up-front rules one more time
2. Render the final comment inline in your response
3. **In the same turn**, save to `/tmp/linkedin-comment-N.txt` and post
   via BYOB

## Posting workflow (verified live)

```text
# Already on the post page from the read step. Like first (lower stakes).
browser_read(url="<post_url>", reuseTab=true, screens=2)
# Find the post-action-bar Like — name "Reaction button state: no reaction" with tag "button"
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<like_idx>")

# Type the comment into the inline textbox — name "Text editor for creating comment", role "textbox"
browser_type(tabId=<linkedin_tab>, selector="byob:idx=<editor_idx>", text="<comment text>")

# Re-read — typing into the textbox enables a NEW "Comment" submit button (button tag, distinct from the post-action-bar "Comment" that opens the composer)
browser_read(url="<post_url>", reuseTab=true, screens=1)
# The submit button is named "Comment" (button tag) and sits to the right of the textbox (high x-coordinate, e.g. ~844px); the post-action-bar "Comment" sits at ~565px and just opens the composer
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<submit_idx>")

# Confirm — screenshot OR re-read and look for "Reaction button state: Like" on the post-level button + comment count incremented + your comment appearing with author "Valor Engels" and timestamp "now"
browser_screenshot(tabId=<linkedin_tab>, savePath="/tmp/linkedin-comment-confirmation.jpg", format="jpeg", quality=55)
```

**Distinguishing the two "Comment" IEs**: after typing, you'll see TWO entries with `name: "Comment"`:
- The post-action-bar Comment (lower idx, bounds x ≈ 565) — this OPENS the composer; clicking does nothing useful when composer is already open
- The submit Comment (higher idx, bounds x ≈ 844) — this PUBLISHES the comment

Pick the higher-x-coordinate one. After successful submit the textbox empties and your comment appears in the comment list with timestamp "now".

## Editing Comments

**Never use Edit to post a correction.** Editing replaces the full text — a "Correction: ..." opener reads as nonsense once the original is gone.

If a posted comment needs correcting:
- **Delete it** and post a clean replacement, or
- **Post a new reply** beneath it with the correction in context

Only use Edit to fix typos or rewrite the whole comment as a clean standalone.
