# Task 2: Write a Post

## Audience

LinkedIn is broad: PMs, designers, executives, salespeople, students, recruiters; most aren't engineers and will never see the codebase. **Write for the smart professional in a different field.** A successful post lets a marketer, lawyer, or product manager close the tab having learned something useful for their own work. If only engineers building the same thing can decode it, it belongs in the repo's docs, not here.

## Research

`git log --oneline --since="5 days ago"`, then the relevant files and feature docs. Keep going past "what changed" until you can answer: **what general lesson did this work teach, that anyone could use?** That lesson is the post; the codebase work is one example of it.

Skip routine fixes, formatting, dependency bumps, and niche-only insights. **Never manufacture a post to hit a cadence**: no lesson this run means no post, and that is the correct outcome.

## Draft in a fresh subagent

The parent's engineering context bleeds jargon into anything it drafts, so a `general-purpose` subagent drafts from a brief. Fill in the source material; the rest is fixed.

```
You are drafting a LinkedIn post on behalf of Valor Engels (software engineer
at Yudame). The audience is the broad LinkedIn feed: PMs, designers,
executives, salespeople, students, recruiters. Most aren't engineers.

THE NON-NEGOTIABLE RULE: a marketing director, a lawyer, or a product
manager, smart with no engineering background, finishes the post feeling
they learned something useful for their own work. If only engineers in the
same niche can decode it, you've failed the brief.

## Source material

<<<
[Relevant commit hashes, feature doc path(s), and a 2-3 sentence factual
summary of what was built and why. Nothing more; the drafter reads the files
and extracts the lesson itself.]
>>>

Read those files before drafting.

## The portable lesson

Before drafting, write down the general lesson this work taught. It must make
sense outside this codebase, outside engineering, ideally outside tech.
"Deterministic call sites should be cached" is a tactic, not a lesson; keep
distilling until you have something like "Before optimizing for speed, audit
what you're assuming is holding still", which a contracts team reusing past
clauses would also nod at. The lesson IS the post; the codebase work is one
concrete instance.

## Structure

1. Never open with setup. Most readers decide within two sentences. Three
   legal openers:
   - Lesson-hook: sentence one IS the takeaway, in plain language.
   - Promise-hook: sentence one teases that the rest is worth it ("Here's
     something we keep relearning the hard way:").
   - Curiosity-hook: sentence one opens a specific gap ("The faster version
     was the bug."). The gap must be real and paid off in the post; an
     unclosed tease is clickbait. A curiosity opener may borrow the example
     from step 3 forward.
2. Set the stage: one or two sentences naming the kind of situation where the
   lesson shows up, in everyday framing.
3. One concrete example from the codebase, with the least jargon that makes
   the point. Explain terms like "cache" in passing or replace them.
4. Land on a takeaway the reader can apply in their own field.

## Specifics

Keep the anchor sharp (the actual lesson, the actual fix). Genericize
incidental scaffolding: for each named tool or vendor, if swapping it for a
different example wouldn't change the lesson, use the category ("anything
sharing state", "a database"). Naming "Redis" makes a Postgres user think it
doesn't apply.

## Style

- A teacher to a curious adult learner: generous, plainspoken, specific.
- Value first. The closing repo link is a soft footer; if the draft reads like
  it routes people to the link, the value isn't carrying its weight.
- No untranslated jargon: `os.replace`, `asyncio.Lock`, `sha256`, `LRU`,
  `RAG`, `MCP`, `Popoto`, `pytest`, `CI`, file paths, function names.
  "Pull request" reads as "asking for something" to non-engineers; say "a
  proposed change".
- No listicle bullets unless the content is a list. No "we just shipped" /
  "I just built" announcements. No performative humility or chest-thumping.
- Zero em-dashes (—); readers discount them as an LLM tell. Use periods,
  colons, commas, or parentheses.

## Length and closing

~800 characters by default; longer only if the lesson needs it. End with
`github.com/tomcounsell/ai` and 3-5 hashtags from: #AIAgents #AgenticAI
#ClaudeAI #OpenSource #DeveloperTools #LLMs #MachineLearning
#SoftwareEngineering

Before returning, reread as a marketing director who has never written code:
lesson (or a soon-closed loop) in sentence one, nothing that reads as a
changelog, no em-dashes, incidental specifics genericized. If it only makes
sense to an AI engineer, restart from the lesson.

## Output

Write the draft to `/tmp/linkedin-post.txt`; return the full text plus one
line stating the portable lesson.
```

## Iterate with rotating cold readers

One cold reader drifts into agreement with the drafter after a round, so each round uses a different persona in a fresh `general-purpose` subagent with no shared context. Where two personas flag the same problem, the fix is mandatory. AI-register tells are de-slop's job at the gate, not a persona's.

1. Drafter writes v1. Cold-read: **Casual Professional Reader**.
2. Drafter rewrites v2 from scratch with the critique as input (inline patches accumulate into Frankenstein drafts). Cold-read: **Generalist Engineer**.
3. Only if a round graded below B+: v3, cold-read: **Skeptic**.
4. Ship the highest-graded version. If none reaches B+, drop the post; the premise is the problem, not the prose.

Persona briefs (`{PERSONA}` / `{PERSONA_BIAS}`):

- **Casual Professional Reader**: "You are a marketing director (or lawyer, or PM). You've never written code. You read LinkedIn on the train. Any post that needs engineering jargon to follow is not for you. Did you finish with something useful for your own work?"
- **Generalist Engineer**: "You work in tech but not this niche. Smart but uninitiated. Every term that needs a Wikipedia tab is a strike. If a pro in a different specialty can't grok it on first read, it failed."
- **Skeptic**: "You assume the post is overstating. You hunt for the unsupported claim, the 'in our experience' that's one anecdote, the tradeoff treated as a free lunch. Posts without defensible specifics are vibes."

Cold-read prompt:

```
You are {PERSONA}. {PERSONA_BIAS}

You have NO knowledge of the author, any codebase, or insider context. You
see this draft LinkedIn post:

---
[paste current draft]
---

Grade it strictly through your lens. B+ = a stranger reads to the end and
finishes with something useful for their own work. A = a stranger considers
sharing it. Do not grade on a curve: if your bias finds a problem, that grade
is the grade.

GRADE: [A / A- / B+ / B / B- / C / D / F]
WOULD YOU READ TO THE END: [yes / no / maybe + one sentence why]
WHAT THE POST IS SAYING: [one-sentence plain-English paraphrase]
WHAT'S BURIED: [where the lesson lives if not in sentence one]
{PERSONA}-SPECIFIC FINDING: [the one thing your bias catches best]
TOP THREE FIXES: 1. ... 2. ... 3. ...

Under 250 words. Be blunt.
```

Then pass the publish gate in SKILL.md on `/tmp/linkedin-post.txt` (medium: LinkedIn post), render the final post inline, and publish in the same turn.

## Publish: known limitation

BYOB cannot drive LinkedIn's "Start a post" composer (verified 2026-05-05, PR #1286 / issue #1274). The modal's textbox renders in a React portal that `browser_read`, `browser_get_html`, and `browser_wait_for` cannot traverse, so `browser_type` has no target; `browser_press_key` reaches it but only one key per call. Already tried and failed: clicking every "Start a post" IE variant (with and without `force`), and `get_html` / `wait_for` on `body`, `[contenteditable=true]`, `[role='dialog']`, `.share-creation-state`, `.ql-editor`. Hashed React classes are not worth chasing.

So at the publish step: render the final draft inline and say plainly that BYOB can't drive the post composer yet, and the user should paste it from `/tmp/linkedin-post.txt` (or use the mobile app). Then continue to Task 3; comments use an inline textbox and work.

If BYOB gains portal traversal: navigate → read → click "Start a post" → re-read for the editor idx → `browser_type` → re-read for the "Post" idx → click → screenshot.
