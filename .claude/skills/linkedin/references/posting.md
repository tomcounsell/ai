# Task 2: Write a Post

## Audience model

LinkedIn is broad: PMs, designers, executives, salespeople, students, recruiters. Most readers will never see the codebase, and most aren't engineers. **Write for the smart professional in a different field, not the peer who builds the same thing.** A successful post lets a marketer, lawyer, or product manager close the tab feeling they learned something useful for their own work.

If the only audience that can decode the post is "engineers who build the same thing I do," it's a blog post, not LinkedIn. Save it for the repo's docs.

## Research

```bash
git log --oneline --since="5 days ago"
```

Read the relevant files and feature docs. But don't stop at "what changed" — keep going until you can answer:

**What general lesson did this work teach me, that anyone could use?**

That lesson is the post. The codebase work is one concrete example of the lesson, not the subject of the post.

**Only post if there's a portable lesson.** Skip routine bug fixes, formatting, dependency bumps, and any insight whose only audience is engineers in the same niche.

## Draft (delegate to a fresh subagent)

**Drafting happens in a subagent, not the parent session.** The parent session is loaded with engineering context — code excerpts, file paths, internal jargon — and that context bleeds into any post written inline. The post turns into an internal memo. A subagent starts cold and only knows what its brief tells it.

The parent's job here is to assemble a brief and hand it off. Keep the candidate technical work in your hands; pass only what the subagent needs to extract the lesson.

**Spawn the subagent with `general-purpose`** and the prompt template below. Fill in the source material; everything else is fixed.

```
You are drafting a LinkedIn post on behalf of Valor Engels (software engineer
at Yudame). The audience is the broad LinkedIn feed: PMs, designers,
executives, salespeople, students, recruiters. Most readers will never see
the codebase, and most aren't engineers.

THE NON-NEGOTIABLE RULE: write so a marketing director, a lawyer, or a
product manager — someone smart with no engineering background — finishes
the post and feels they learned something useful for their own work. If
only engineers in the same niche can decode it, you've failed the brief.

## Source material

<<<
[Paste here: relevant commit hashes, the feature doc path(s), and a 2-3
sentence factual summary of what was built and why. Nothing more. Do NOT
paraphrase the technical detail in the brief — let the subagent read the
files and extract the lesson itself.]
>>>

Read those files yourself before drafting.

## The hard step: extract the portable lesson

Before drafting, write down explicitly: what general lesson did this work
teach, that anyone could use?

The lesson must be portable. It should make sense outside this codebase,
outside engineering, ideally outside tech entirely. If your candidate
lesson is something like "deterministic call sites should be cached," try
again — that's a tactic, not a lesson. Keep distilling until you have a
sentence like "Before optimizing for speed, audit what you're assuming is
holding still" — something a contracts team reusing past clauses or a
designer reusing past templates would also nod at.

The lesson IS the post. The codebase work is one concrete instance of the
lesson, not the subject.

## Structure

1. **Lead with the lesson, never with the setup.** Two legal opener
   patterns:
   - *Lesson-hook*: sentence one IS the takeaway, in plain language.
   - *Promise-hook*: sentence one teases that the rest is worth your
     time ("Here's something we keep relearning the hard way:").
   What fails is starting with three setup sentences before the payoff.
   Most LinkedIn readers bounce in two sentences. Get them to the
   lesson first.
2. Set the stage. One or two sentences naming the kind of situation where
   this lesson shows up. Use everyday framing.
3. One concrete example. Drawn from the codebase, stripped to the smallest
   amount of jargon needed to make the point. If you must use a term like
   "cache" or "model," explain it in passing or replace with a plain
   analogy. Specifics earn their place by making the lesson vivid; they do
   not carry the post.
4. Land on a portable takeaway. A closing sentence the reader can apply to
   their own field. Must make sense to someone who never reads this
   codebase.

## Genericize incidental specifics, keep load-bearing ones sharp

The post's anchor (the actual lesson, the actual fix, the named pattern
the post is built around) MUST stay specific. But scaffolding details
that aren't the lesson should be genericized so the post travels beyond
people running the same exact stack. Naming "Redis" in a post about
shared state makes a Postgres user think it doesn't apply when it does.
"Anything sharing state" travels; "a Redis instance" narrows.

Rule of thumb: for each named third-party tool / vendor / specific tech
in the draft, ask — would the lesson still land if I swapped this for
a different example? If yes, the named thing is incidental scaffolding,
not the anchor. Replace with the generic category ("any cache," "a
database," "anything sharing state"). Keep load-bearing specifics (the
actual tool the lesson is about, the actual fix). Strip incidentals.

## Style

- Write like a teacher to a curious adult learner. Generous, plainspoken,
  specific.
- No abbreviations or in-jargon without translation. Terms like `os.replace`,
  `asyncio.Lock`, `MODEL_EXPERIMENT`, `sha256`, `LRU`, `RAG`, `MCP`,
  `Popoto`, `pull request`, `pytest`, `CI`, file paths, function names —
  none of those belong in a LinkedIn post. ("Pull request" in particular:
  most non-engineers read it as "asking for something" — translate to
  "a proposed change" or just "a change.") If you find yourself reaching
  for any of these, the lesson hasn't been translated yet.
- No listicle bullets unless the content is naturally a list.
- No "we just shipped" / "I just built" framing — that's an announcement.
- No performative humility, no chest-thumping.

## Length and closing

- ~800 characters as default. Go longer only if the lesson needs the room.
- End with `github.com/tomcounsell/ai` and 3-5 hashtags from this set:
  #AIAgents #AgenticAI #ClaudeAI #OpenSource #DeveloperTools #LLMs
  #MachineLearning #SoftwareEngineering

## Mandatory self-review before returning

1. Casual-reader test. Read your draft pretending you're a marketing
   director who has never written code. Do you finish with something
   useful for your own work? Do you bounce off jargon by sentence two?
   If the post only makes sense to an AI engineer, the lesson hasn't been
   extracted yet. Start over from the lesson step.
2. Lead test. Is the lesson in sentence one (or sentence one teases that
   the lesson is coming)? If sentence one is project context or setup,
   rewrite so the point is first.
3. Substance. Cut anything that reads like an announcement or a feature
   changelog. The lesson is at the front; the example earns its place by
   making the lesson vivid.
4. Em-dash scan. Search the draft for "—". If you find ANY, replace with
   periods, colons, commas, or parentheses. Em-dashes are a vanilla-LLM
   tell in 2026 and readers discount the post on sight. Zero tolerance.
5. Genericize check. For each named third-party / vendor / specific tech
   in the draft, ask: would the lesson still land if I swapped this for
   a different example? If yes, the named thing is incidental
   scaffolding. Replace with the generic category. Keep load-bearing
   specifics (the actual tool the post is about, the actual fix). Strip
   incidentals.

## Output

Write the final draft to `/tmp/linkedin-post.txt` and return the full
draft text in your reply, plus one line stating the portable lesson you
extracted.
```

## Iterate: 3-4 rounds of draft + cold-read with rotating personas

Single-shot drafts ship mediocre posts. The first draft buries the lesson
in setup, the second tightens the opener, the third lands the takeaway.
Build that loop into the workflow.

A *single* cold-reader spirals into agreement with the drafter after one
round. They both end up grading drafts on whether they match the prior
round's critique rather than whether they actually land on a stranger.
**Fix: rotate distinct personas across rounds. Fresh subagent each time
(no shared context).** Where two of four personas flag the same problem,
the problem is real and the fix is mandatory.

The four personas (each a fresh `general-purpose` subagent):

1. **The Casual Professional Reader** — a marketing director, lawyer, or
   PM with no engineering background. Bias: posts that require
   engineering jargon to decode are filler for them. Bar: do they finish
   feeling they learned something useful for *their* work?
2. **The LLM-Tell Hunter** — hunts for vanilla-LLM register: em-dashes,
   tricolons, "in essence," "fundamentally," "it's not just X, it's Y,"
   "this isn't about A, it's about B," over-symmetric sentences,
   listicle smell. Grade is binary: tells present (D or worse) or
   absent (A possible).
3. **The Generalist Engineer** — works in tech but a different niche
   (mobile, data, embedded). Catches inscrutable in-niche jargon and
   the "even pros need three reads" failure mode.
4. **The Skeptic** — assumes the post is overstating. Hunts for the
   unsupported claim, the "in our experience" that's one anecdote,
   the tradeoff treated as a free lunch.

**Round assignment:**
- Round 1 cold-read: Casual Professional Reader (catches jargon and
  buried-lead failures first; this is the post's actual audience)
- Round 2 cold-read: LLM-Tell Hunter (strips register tells before
  deeper iteration)
- Round 3 cold-read: Generalist Engineer
- Round 4 cold-read (if needed): Skeptic

**Cold-read prompt template** (parameterize `{PERSONA}` and
`{PERSONA_BIAS}` per round):

```
You are {PERSONA}. {PERSONA_BIAS}

You have NO knowledge of the author, no knowledge of any specific
codebase, and no insider context. You see this draft LinkedIn post:

---
[paste current draft]
---

Grade it strictly through your specific lens. The bar for B+ is: a
stranger reads to the end and finishes with something useful for their
own work. The bar for A is: a stranger considers reposting or sharing.

Sympathy is the enemy. Do NOT grade on a curve. If your specific bias
finds a problem, that grade is the grade, even if other aspects are
fine.

Answer in this exact structure:

GRADE: [A / A- / B+ / B / B- / C / D / F]
WOULD YOU READ TO THE END: [yes / no / maybe + one sentence why]
WHAT THE POST IS SAYING: [one-sentence paraphrase in plain English]
WHAT'S BURIED: [where the lesson actually lives if not in sentence one]
{PERSONA}-SPECIFIC FINDING: [the one thing your bias catches best]
TOP THREE FIXES: 1. ... 2. ... 3. ...

Total under 250 words. Be blunt.
```

**Persona briefs** (substitute into the template):

- **Casual Professional Reader** — *"You are a marketing director (or
  lawyer, or PM). You've never written code. You read LinkedIn on the
  train. Your bias: any post that needs engineering jargon to follow is
  not for you. Decide: did you finish with something useful for your
  own work?"*
- **LLM-Tell Hunter** — *"You hunt for vanilla-LLM register: em-dashes,
  tricolons, 'in essence,' 'fundamentally,' 'it's not just X, it's Y,'
  over-symmetric sentences, listicle smell. Your grade is binary: tells
  present (D or worse) or absent (A possible)."*
- **Generalist Engineer** — *"You work in tech but not this niche.
  You're smart but uninitiated. Every term that requires a Wikipedia
  tab is a strike. If a working pro in a different specialty can't grok
  it on first read, it failed."*
- **Skeptic** — *"You assume the post is overstating. You hunt for the
  unsupported claim, the 'in our experience' that's one anecdote, the
  tradeoff treated as a free lunch. Posts without defensible specifics
  are vibes."*

**The loop:**

1. Drafter subagent produces v1 to `/tmp/linkedin-post.txt`.
2. Cold-read v1 with **Casual Professional Reader** (fresh subagent).
3. Drafter rewrites v2 from the critique. Don't merge inline. Let the
   drafter rewrite from scratch with the critique as input. Inline
   patches accumulate into Frankenstein drafts.
4. Cold-read v2 with **LLM-Tell Hunter** (fresh subagent).
5. Draft v3 → cold-read with **Generalist Engineer**.
6. Round 4 only if any prior round graded below B+: draft v4 →
   cold-read with **Skeptic**.
7. **Ship the highest-graded version.** If no version reaches B+, drop
   the post. The premise is the problem, not the prose. Better to ship
   nothing than a D-tier post.

When the subagent returns the final draft, the parent session:
1. Reads `/tmp/linkedin-post.txt`
2. **Invokes `Skill('de-slop')`** as a fresh-context review of `/tmp/linkedin-post.txt` only (medium: LinkedIn post) — PASS proceeds; BLOCK returns the draft to the drafter with the diagnosis as revision instructions (max 2 retries before dropping the post)
3. **Invokes `Skill('authenticity-pass')`** — PASS proceeds; BLOCK returns the draft to the drafter with the blocking gaps as revision instructions (max 2 retries before dropping the post)
4. Renders the final post inline in the response
5. **In the same turn**, attempts to publish (see ⚠️ below)

## Publish

> **⚠️ KNOWN LIMITATION (verified live 2026-05-05, see PR #1286 / issue #1274):** BYOB **cannot drive LinkedIn's "Start a post" composer modal.** Clicking the "Start a post" trigger opens the modal visually (confirmed via screenshot showing "What do you want to talk about?"), but the modal's contenteditable textbox renders inside a **React portal** that `browser_read`, `browser_get_html`, and `browser_wait_for` cannot traverse. With no selector to target, `browser_type` has nothing to type into. `browser_press_key` does dispatch into the focused textbox, but it's single-key-per-call — typing 1500 chars one at a time is impractical (1500 round-trips). This is a general BYOB gap (every React-portal-rendered modal on every site likely has the same problem), not a LinkedIn-specific one — worth filing upstream.
>
> **What to do until BYOB resolves this:** at the point you'd publish, render the final draft inline AND state plainly: *"BYOB can't drive the post composer modal yet (React portal — see SKILL.md KNOWN LIMITATION) — paste this from `/tmp/linkedin-post.txt` into LinkedIn yourself, or send via the mobile app where the share flow is different."* Then proceed to Task 3 (which works fine — comments use an inline textbox, not a portal modal).
>
> Things that have been verified NOT to work for the share modal:
> - `browser_click("byob:idx=...")` on every "Start a post" IE variant (`idx=137..141`, `tag` "div"/"p", with and without `force:true`) — modal opens but textbox not in document
> - `browser_get_html("body")` after open (returns navigation chrome only — modal lives elsewhere)
> - `browser_get_html("[contenteditable=true]")`, `[role='dialog']`, `.share-creation-state`, `.ql-editor` (all `selector_not_found`)
> - `browser_wait_for(...)` for any of those selectors (always times out)
> - Hashed React class selectors are deploy-volatile and not worth chasing
>
> If/when BYOB gains portal traversal, the intended flow is: navigate → read → click "Start a post" idx → re-read for editor textbox idx → `browser_type` the post text → re-read for "Post" submit idx → click → screenshot for confirmation.
