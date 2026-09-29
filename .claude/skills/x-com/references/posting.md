# Task 2: Write a Post

## Audience

X skews to AI/dev Twitter: people who already know "agent," "RAG," "MCP," "context window," scroll fast, and have seen every launch announcement and lessons-learned listicle. The bar to stop a stranger scrolling is high.

What earns engagement here: a sharp behavioral observation (common use vs. real leverage), a specific number nobody else has, one weird real detail from inside a system, an honest take on something hyped, builder-noticing-builder. What dies: launch announcements, LinkedIn-style lesson lists, hashtags, performative humility or chest-thumping, curiosity-bait questions, confidently restating the obvious. Skim the user's own For You and Following timelines before drafting.

## Research

`git log --oneline --since="3 days ago"`, then the diffs and feature docs. Look for a surprising bug, footgun, or constraint with a quotable name; a small pattern someone would steal; a number that frames a real tradeoff; an observation that isn't in the marketing layer.

If you can't say "the angle is X" in one sentence, ship nothing this run.

## The receipt (first self-reply)

The main post is the claim; a self-reply within a minute or two is the receipt, and keeps links out of the main post (X mildly demotes link-tweets, and a `github.com/...` URL in the body reads as self-promo). Post one only when it adds something:

- Good: link to repo, gist, PR, blog post, or reference docs for a niche tool the post names; a screenshot of the real thing (terminal output, chart, the bug); a one-line caveat the post couldn't fit; a genuine thread continuation.
- Never: "follow me for more," newsletter plugs, "Hot take 🔥," "Bookmark this," padding.

Code snippet or PR → link. Named behavior or pattern → screenshot of it in action. Self-contained post → skip the self-reply.

## Draft in a fresh subagent

The parent's engineering context bleeds into drafts, so a `general-purpose` subagent drafts:

```
You are drafting a post on X on behalf of Valor Engels (software engineer at
Yudame, @ValorEngels). The audience is AI/dev Twitter: people who already
know "agent," "RAG," "MCP," "context window," and have seen every launch
announcement and lessons-learned listicle.

THE NON-NEGOTIABLE: a stranger scrolling fast has to stop. They stop on one
concrete, defensible detail in sentence one: a number, a name, an observation
about behavior. Not a thesis statement, not a vibe.

## Source material

<<<[commit hashes, feature doc paths, 2-3 sentence factual summary]>>>

Read those files before drafting.

## What works on this feed

- Sharp behavioral observation: "Most people use X like Y. The leverage is
  in Z."
- A specific number nobody else has: "Restarted 412 times in a week." "Cut
  p99 from 8s to 600ms."
- One weird, real, funny detail from inside the system. Internal names
  ("worker self-suicide guard," "orphan reaper") travel because they're funny
  AND technically real. The detail IS the post.
- Honest take on something everyone is hyping: what's under the hood.
- Builder-noticing-builder: "Tried X, here's the one thing it nailed and the
  one thing it didn't."

## What dies

"We just launched X." "5 things I learned building an agent." Hashtags.
"Just a small thing I built…" / "Absolutely crushed it." Curiosity-bait
("What's the one tool you can't live without?"). Restating the obvious
("Agents need memory." Yes. So?).

## Rules

- Open with the lesson or a promise, never setup. Lesson-hook: "pytest-xdist
  will find your shared state." Promise-hook: "Here's something that took us
  a day to find:". Readers on a fast feed don't reach sentence three. No
  "Hot take:" / "Real talk:" / "Genuinely…" preamble.
- One idea per post; two ideas is a thread, so pick the sharper one.
- Keep the anchor specific (the named tool, pattern, fix, time cost).
  Genericize incidental scaffolding: if swapping a named thing for another
  example wouldn't change the lesson, use the category ("anything sharing
  state"). Naming "Redis" made a Postgres user think it didn't apply.
- Dry over enthusiastic; exclamation points read as desperate.
- Zero em-dashes (—); readers clock them as LLM and discount the post. Use
  periods, colons, commas, or parentheses.
- No hashtags. No emoji unless it adds signal (📈 next to a real stat).
- Hard cap 280 characters; default 200-260.
- No `github.com/tomcounsell/ai` in the post unless it resolves something the
  post promises. Name niche tools' docs links in your output for the
  first-reply receipt.

Before returning: would a stranger three tweets deep stop on sentence one?
Is the post built around one concrete anchor? Could a launch announcement be
edited to say this (if so, find the angle inside it)?

## Output

Write the draft to `/tmp/x-post.txt`; return the full text, its character
count, and one sentence naming the anchor.
```

## Iterate with rotating cold readers

A single cold reader drifts into agreement with the drafter within a round or two, so each round uses a different persona in a fresh subagent with no shared context. Where two personas flag the same fix, it is mandatory; one persona alone makes it optional. AI-register tells are de-slop's job at the gate.

1. v1 → **Time-Pressed Scroller**.
2. Drafter rewrites v2 from scratch with the critique (inline patches make Frankenstein drafts) → **Specialist**.
3. Only if a round graded below B+: v3 → **Generalist** or **Skeptic**.
4. Ship the highest-graded version; if none reaches B+, drop the post rather than ship D-tier after the effort spent.

Persona briefs (`{PERSONA}` / `{PERSONA_BIAS}`):

- **Time-Pressed Scroller**: "You're scrolling on the train. You read sentence one and the first six words of sentence two. That's it. Decide: stop or scroll. Posts that bury the point are noise."
- **Specialist**: "You build {field} systems daily. You distinguish 'novel insight' from 'well-known' and catch claims that are confident but technically wrong. Posts that read as insight to outsiders but obvious to insiders are filler."
- **Generalist**: "You work in tech but not this niche. Smart but uninitiated. Every term that needs a Wikipedia tab is a strike."
- **Skeptic**: "You assume the post is overstating. You hunt for the unsupported claim, the 'in our experience' that's one anecdote, the tradeoff treated as a free lunch. Posts without defensible specifics are vibes."

Cold-read prompt:

```
You are {PERSONA}. {PERSONA_BIAS}

You have NO knowledge of the author, any codebase, or insider context. You
see this draft tweet:

---
[paste the current draft]
---

Grade it strictly through your lens. Most posts on this feed are D-tier.
B+ = a stranger stops scrolling and reads to the end. A = a stranger reads it
twice and considers replying. Do not grade on a curve: if your bias finds a
problem, that grade is the grade.

GRADE: [A / A- / B+ / B / B- / C / D / F]
WOULD YOU STOP SCROLLING: [yes / no / maybe + one sentence why]
WHAT THE POST IS SAYING: [one-sentence plain-English paraphrase]
WHAT'S BURIED: [where the lesson lives if not in sentence one]
{PERSONA}-SPECIFIC FINDING: [the one thing your bias catches best]
TOP THREE FIXES: 1. ... 2. ... 3. ...

Under 250 words. Be blunt.
```

Then pass the publish gate in SKILL.md on `/tmp/x-post.txt` (medium: X post).

## Publish (verified live)

```text
browser_navigate(url="https://x.com/home", tabId=<x_tab>, waitUntil="networkidle")
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]")
browser_type(tabId=<x_tab>, selector="[data-testid=\"tweetTextarea_0\"]", text="<post body>", clear=true)
browser_click(tabId=<x_tab>, selector="[data-testid=\"tweetButtonInline\"]")
browser_screenshot(tabId=<x_tab>, savePath="/tmp/x-post-confirm.jpg", format="jpeg", quality=55)
```

Success: textbox empties and the tweet tops the timeline as "Valor Engels @ValorEngels · Now". If the home composer is collapsed or unresponsive, use `https://x.com/compose/post` (same textarea testid). Then post the receipt self-reply if there is one.
