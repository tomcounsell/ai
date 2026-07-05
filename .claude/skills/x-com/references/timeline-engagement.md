# Task 3: Browse Timeline and Engage

## Volume targets

This task does a lot more than 3 careful replies. The For You algorithm only sharpens if you give it signal. Sparse engagement keeps the feed thin and generic. Targets per run:

- **Like 20-50 posts.** Anything genuinely relevant (agentic systems, memory, RAG, async pipelines, LLM tooling, dev infra, AI-in-production, builder posts from people doing real work). Likes are cheap signal. Be liberal but not indiscriminate.
- **Reply on up to 15.** Apply the screening rules below; quality still matters more than volume. Fewer is fine if the feed is thin, but 5-15 is the normal range.
- **Follow 5-15 new people you haven't seen before.** This is the main feed-curation lever. See "Following new people" below.

The infinite scroll on For You means there's always more to read. Don't stop after one screen.

## Use For You, not Following

The Following tab shows posts from accounts already followed. Fine for keeping current with known voices, but it doesn't expand the feed. The For You tab surfaces accounts the algorithm thinks the user would like; **that's where new people get discovered**. Default to For You for this task.

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
# If the page lands on Following, click the For You tab
browser_click(tabId=<x_tab>, selector="[role='tab']:not([aria-selected='true'])")
```

## Read the feed in chunks

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

## Following new people (feed curation lever)

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

## Like as you scroll (be liberal)

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

## Screen candidates for replies

Each `<article>` IE has the post text in its `name`. Use that to triage cheaply, then click into individual posts only for the ones that pass screening.

A post passes if:
- It's in a domain we work in (agentic systems, LLM tooling, async, memory, dev infra)
- There's a specific file, pattern, or concrete observation in this codebase that responds to its actual claim
- The reply makes sense to the post's audience without insider context

Loose analogy = not a pass. Keyword overlap = not a pass. Target up to 15 replies per run; fewer is fine if the feed is thin. Don't lower the screening bar to hit the volume target. Better to ship 5 sharp replies than 15 mediocre ones.

## Read the full post before drafting

```text
# Click the article (or its timestamp link) to open the post URL
browser_click(tabId=<x_tab>, selector="byob:idx=<article_idx>")
browser_read(url="<resulting post url>", reuseTab=true, screens=2)
```

Verify the post body matches what you saw in the IE summary. Threads can hide context below. Read down at least one screen.

## Reply

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

## Editing / Deleting

X allows post deletion (and edits within a window for paid accounts). For a bad reply: delete it (three-dot menu → "Delete") and post a clean replacement. Don't post "Correction:" replies on top of broken originals. They read as nonsense once context shifts.
