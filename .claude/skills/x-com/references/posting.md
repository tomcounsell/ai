# Task 2: Write a Post

## Audience model

X is faster, sharper, and more technical than LinkedIn. The audience skews toward AI/dev Twitter. People who already know the words "agent," "RAG," "MCP," "context window." They scroll fast and have seen every launch announcement and every "lessons learned" listicle. The bar for a stranger to stop scrolling is high.

## What works on this feed (observed, not theorized)

Skim the user's own For You and Following timeline before drafting. Patterns that earn engagement in this niche:

- **Sharp behavioral observation.** Form: *"Most people use X like Y. The actual leverage is in Z."* Names a gap between common use and real use. Lands when the Z is something the reader can immediately picture.
- **A specific number nobody else has.** "Restarted 412 times in a week." "Cut p99 from 8s to 600ms." "The bug ate 17% of our request budget." A number you can defend wins over an adjective every time.
- **One concrete weird detail from inside a system.** Names of things ("worker self-suicide guard," "the orphan reaper," "session steering inbox") travel because they're funny *and* technically real. The detail is the post; explanation is one sentence at most.
- **Honest take on something everyone else is hyping.** Not contrarian-for-its-own-sake. Just naming what's actually under the hood when a launch announcement is everywhere. ("Templates are the easy part. The hard part is the harness.")
- **Builder-noticing-builder posts.** "I tried X, here's the one thing it nailed and the one thing it didn't." Specific, useful, low-key. No grandstanding.

## What dies on arrival

- **"We just launched X."** Read as marketing, scrolled past. If you have something to ship, name *one* surprising property of it, not the launch.
- **LinkedIn-style "lessons learned" lists.** "5 things I learned building an agent" reads out of register here. Save it for the other platform.
- **Hashtags.** Spam signal in 2026. None.
- **Performative humility / chest-thumping.** "Just a small thing I built…" / "We absolutely crushed this." Both die.
- **Curiosity-bait questions.** "What's one tool you can't live without?". Engagement-farm vibe. Don't.
- **Restating the obvious in confident voice.** "Agents need memory." Yes. So?

## Voice rules

- **One idea per post.** If you have two, you have a thread; otherwise pick the sharper one.
- **Open on the substance.** No "Hot take:" / "Real talk:" / "Genuinely…" preamble. Sentence one is the point.
- **Specific over clever.** A real detail beats a punchline.
- **Dry over enthusiastic.** Understatement reads as confident. Exclamation points read as desperate.
- **No em-dashes (—). Ever.** Em-dashes are a vanilla-LLM tell in 2026. Readers clock them instantly and discount the rest of the post. Use a period and start a new sentence. Or a colon. Or a comma. Or parentheses. Never an em-dash. This applies to posts, replies, DMs, and any text this skill produces. Search-and-destroy in drafts before publishing.
- **No emoji unless adding signal.** A 📈 next to a real chart, fine. Decorative emoji, no.
- **Hard ceiling 280 chars.** Threads only if the idea genuinely needs the room (rare). Default 200-260.
- **No hashtags. None.** The X algorithm in 2026 surfaces by engagement velocity + ML topic clustering, not by tag matching. Hashtags add zero reach. They also signal marketer-coded voice in AI/dev twitter. Two narrow exceptions exist (live-event tags during the event, niche community tags) but neither applies to this account. Skip universally.
- **Naked links don't belong in the main post.** X mildly demotes link-tweets, and a `github.com/...` URL in the body reads as self-promo. Put the link in a self-reply (see "First reply: the receipt" below).

## First reply: the receipt

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

## Research

```bash
git log --oneline --since="3 days ago"
```

Read the diffs and feature docs. Look for:
- A surprising bug, footgun, or constraint with a name worth quoting
- A small, sharp pattern someone else might steal
- A concrete number that frames a real tradeoff
- An observation about building agents/tools that isn't in the marketing layer

**Skip routine commits.** A post needs an angle a stranger would screenshot or quote-tweet. If you can't say "the angle is X" in one sentence, skip and ship nothing this run. Better than padding the feed.

## Draft (delegate to a fresh subagent)

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

## The cold-reader cast

A single cold-reader agent spirals into agreement with the drafter after one or two rounds. They both end up in the same echo chamber, grading drafts on whether they match the previous round's critique rather than whether they actually land on a stranger.

**Fix: rotate through five distinct reader personas.** Each round uses a different persona (and a fresh subagent — no shared context with prior rounds). Their disagreements with each other are signal: where two of five flag the same problem, the problem is real.

The five personas, with their defining bias:

1. **The Skeptic** — assumes the post is wrong. Hunts for the unsupported claim, the "in our experience" that's actually one anecdote, the tradeoff treated as a free lunch. Bar: would they reply with a counter-example?
2. **The Specialist** — works in the post's exact niche (e.g. for an AI/agents post: someone who builds agent systems daily). Distinguishes "novel insight" from "well-known to anyone in the field." Catches technically-incorrect-but-confident-sounding lines.
3. **The Generalist** — works in tech but not this niche (e.g. mobile engineer reading an AI post, or a backend engineer reading a frontend post). Catches inscrutable jargon and the "even pros need three reads" failure mode.
4. **The Time-Pressed Scroller** — only reads sentence one and the first few words of sentence two. Decides stop / scroll based on that alone. Brutal hook test.
5. **The LLM-Tell Hunter** — purpose-built to catch AI register: em-dashes, tricolons, "in essence," "fundamentally," "it's not just X, it's Y," "this isn't about Y, it's about Z," over-symmetric sentences, every-paragraph-starts-with-a-conjunction. Their grade is binary: tells present (D) or absent (A).

Round order (why: buried lead first, register tells second, technical wrongness third, last-mile problems fourth) is baked into the loop steps below. If two distinct personas flag the same fix, that fix is mandatory. If only one flags it, it's optional.

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

## Iterate: 3-4 rounds, rotating personas

Single-shot drafts ship D-tier posts. The first draft buries the lesson, the second tightens the opener, the third lands the kicker. Each cold-read is a FRESH subagent (no shared context with prior rounds).

1. **Draft v1.** Drafter subagent (above) produces v1 to `/tmp/x-post.txt`.
2. **Cold-read v1** with Time-Pressed Scroller (fresh subagent).
3. **Draft v2.** Drafter rewrites from critique. Don't merge inline — let the drafter rewrite from scratch with the critique as input. Inline patches accumulate into Frankenstein drafts.
4. **Cold-read v2** with LLM-Tell Hunter (fresh subagent).
5. **Draft v3.** Rewrite.
6. **Cold-read v3** with Specialist (fresh subagent).
7. **Draft v4** (only if any prior round graded below B+).
8. **Final cold-read v4** with Generalist or Skeptic.
9. **Ship the highest-graded version,** or drop the post if no version reaches B+. (Don't ship D-tier just because you've spent 4 rounds — premise might be the problem, not the prose.)
10. **Invoke `Skill('de-slop')`** as a fresh-context review of `/tmp/x-post.txt` only (medium: X post) — PASS proceeds; BLOCK returns the draft to the drafter with the diagnosis as revision instructions (max 2 retries before dropping the post).
11. **Invoke `Skill('authenticity-pass')`** on `/tmp/x-post.txt` — PASS proceeds to publish; BLOCK returns the draft to the drafter with the blocking gaps as revision instructions (max 2 retries before dropping the post).

## Publish (verified live)

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]")
browser_type(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]", text="<post body>", clear=true)
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetButtonInline\"]")
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-post-confirm.jpg", format="jpeg", quality=55)
```

Confirm via screenshot: textbox empties; the new tweet appears at the top of the timeline with author "Valor Engels @ValorEngels · Now". If the home composer is collapsed or unresponsive, navigate to `https://x.com/compose/post` for the full-screen composer (same `data-testid="tweetTextarea_0"`).
