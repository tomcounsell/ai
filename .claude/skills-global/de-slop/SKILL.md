---
name: de-slop
description: "Pre-publish editorial gate for external-audience content: removes AI-writing tells and blocks hollow drafts. Use on documents, presentations, emails, posts, or web copy once a first draft exists. Triggered by 'de-slop this', 'editorial pass', 'humanize this', 'remove the AI tells', or invoked as a mandatory gate by email, do-presentation, linkedin, and x-com."
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
argument-hint: "[draft-file-path]"
user-invocable: true
---

# De-slop

An editorial gate that runs **after** a first draft exists and **before** the content leaves the building. It does two things:

1. **Style:** detects and removes the characteristic signs of AI writing — slop vocabulary, formulaic structure, inflated significance, formatting tics — while preserving the draft's meaning, facts, and voice.
2. **Substance:** blocks hollow drafts. Slop-free text that says nothing is still not shippable; the editor can spike the piece and send it back with a diagnosis.

**Philosophy:** original writers (human or agent) are not burdened with style rules while drafting. Slop removal is a separate editorial pass, the way a copy editor works after the reporter files. Drafting skills should not inline these rules; they hand finished drafts to this gate.

**Scope:** external-audience artifacts — email, documents, presentations, posts, web copy. Conversational chat replies are dialogue, not drafts, and are not gated.

## Cold-read rule

The author of a draft is the worst judge of its slop — same model, same context, full commitment bias. When this skill runs as a wired gate, the calling skill MUST invoke it as a **fresh-context review** (a subagent or forked context) that receives only: the draft (file path or text), the medium, and the intended audience. It must NOT receive the drafting conversation. Judgments are made from the draft alone, as a first-time reader would encounter it. A manual `/de-slop` invocation by the user may run inline — but if the current context authored the draft, prefer spawning the review anyway.

The catalog of tells is adapted from Wikipedia's [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing), generalized beyond encyclopedia articles.

## Repo Context Probe

If `.claude/skill-context/de-slop.md` exists, read it and honor its declarations; otherwise use the generic defaults described below. The context file is where a repo or user declares house style: tells to ignore (e.g., "em dashes are part of my voice"), extra tells to enforce, spelling conventions, or media-specific rules.

## Input

- **File path argument** → read it, edit in place.
- **Pasted/quoted text in conversation** → return the edited draft in the reply.
- **Called by another skill** (email, do-presentation, linkedin, x-com, docs) → receive the draft path, edit in place, return the change log so the caller can proceed.

First, identify the medium — message, document, deck, or web copy — since thresholds differ (see Medium notes).

## When to load sub-files

- Running the pass on anything longer than a short message, or when a borderline call needs the full catalog with before→after examples → read [references/SIGNS.md](references/SIGNS.md)
- Short messages (a few sentences) → the condensed list below is usually enough

## The pass

1. **Read the whole draft** before editing anything. Note the medium, audience, and the author's voice — the goal is that voice minus the slop, not a house-neutral rewrite.
2. **Substance check first.** Before polishing anything, ask: *if I strip every tell, is there a piece left?* The draft must contain (a) at least one concrete specific the reader didn't already have — a fact, number, name, example, or decision — and (b) a discernible point: something the reader should do, decide, or newly understand after reading. Missing both → **BLOCK** with a diagnosis (what's hollow, and what real material would fill it). Do not polish hollow content; polished emptiness sent to a real audience is worse than a rough draft.
3. **Detection sweep.** Walk the draft against the catalog. Collect findings with location and category. Judgment over keyword matching: one "robust" is fine; a cluster of tells in one paragraph is the signal. The test is always *"would a competent human editor flag this?"* — not *"does this word appear on a list?"*
4. **Rewrite.** Fix findings in place. Prefer the smallest edit that removes the tell: delete the filler clause, swap the slop word for the plain one, break the triad, merge the bullet wall into prose. Never change facts, numbers, names, quotes, or commitments. Beware re-slopping: your rewrites come from the same generation of model that produced the tells — after editing, re-scan your own changes against the catalog.
5. **Verdict + report.** Emit the verdict and change log (format below), including anything flagged but deliberately kept and why.

## Condensed catalog — the high-frequency tells

**Vocabulary clusters** — delve, tapestry, testament, underscore, pivotal, crucial, robust, seamless, leverage, boasts, foster, elevate, landscape, realm, journey, vibrant, comprehensive, "it's worth noting," "in today's fast-paced world." Swap for the plain word or delete.

**Negative parallelism** — "not just X, but Y," "it's not about X, it's about Y." Say the true thing directly.

**Rule of three** — triads of adjectives, clauses, or examples appearing again and again. Keep the strongest item, or use two or four.

**Copula avoidance** — "serves as a," "functions as," "stands as," "features" where "is" or "has" is meant.

**Trailing significance clauses** — ", highlighting the importance of...," ", showcasing...," ", ensuring...," "-ing" phrases that append vague commentary. Delete; if the point matters, make it a real sentence with real content.

**Inflated significance** — "marks a pivotal moment," "plays a vital role," "underscores a broader shift." Either state the concrete stake or cut.

**Weasel attribution** — "experts agree," "many believe," "industry reports suggest." Name the source or own the claim.

**Formulaic scaffolding** — "In conclusion," intro paragraphs that preview the sections, closing paragraphs that restate them, "Challenges and Future Prospects"-shaped sections. Cut summaries that add nothing.

**Formatting tics** — bold sprinkled for mechanical emphasis, `**Header:** description` bullet walls that should be prose, emoji as list markers or section dividers, title-case headings where sentence case is house style, gratuitous horizontal rules, em/en dashes doing work commas and periods should do.

**Message boilerplate** — "I hope this email finds you well," "Great question!," "Thanks for reaching out!," reflexive "Let me know if you have any questions!" closers, restating the recipient's question back at them.

**Hedge-hype mix** — "could potentially," "truly unique," stacked intensifiers, superlatives about ordinary things.

**Uniform rhythm** — every sentence the same length and shape, every paragraph three sentences. Vary it.

**AI artifacts** — leftover citation markers (`oaicite`, `contentReference`, `turn0search`, `[cite: 1]`), placeholder text ("This section would explore..."), knowledge-cutoff disclaimers ("As of my last update"). These are always bugs; remove on sight.

## What NOT to do

- **Don't flatten the voice.** Removing slop should make the writing sound more like its author, not like nothing. If the author genuinely uses em dashes or an occasional triad, the context file can say so — and even without one, a tell used once with intent is not a finding.
- **Don't change substance.** Facts, numbers, names, promises, and hedges that carry real uncertainty all survive verbatim. If a claim looks wrong, flag it in the report; do not silently fix it.
- **Don't strip structure the medium needs.** Decks need bullets; runbooks need numbered steps; a README needs headings.
- **Don't add.** No new claims, examples, or enthusiasm. This pass only removes and rewords.
- **Don't keyword-police.** A finding is a *pattern* a human editor would catch, never a banned-word hit.

## Verdict format

```
DE-SLOP: PASS (edited, 14 changes)   # or PASS (clean), or BLOCK

  vocabulary (5)      "leverage" → "use", "pivotal" → cut, ...
  parallelism (2)     "not just a tool, but a partner" → "a tool that ..."
  scaffolding (1)     cut closing paragraph restating the three sections
  boilerplate (2)     cut "I hope this finds you well", "Great question!"
  artifacts (1)       removed stray "[cite: 3]"

  KEPT: two em dashes in §2 — doing real appositive work
  FLAGGED (not fixed): "$40k saved" — verify before sending; facts are edited by no one but the author
```

On BLOCK:

```
DE-SLOP: BLOCK

  DIAGNOSIS: [what is hollow — e.g. "three sections of scaffolding around zero
  specifics; no number, example, or decision a reader could act on"]

  TO UNBLOCK: [what real material would fill it — pulled from what the draft
  gestures at, e.g. "the actual migration date, the one metric that motivated
  this, or the decision you're asking the client to make"]
```

**What the caller does with the verdict:**
- **PASS** → proceed to send/export/publish.
- **BLOCK** → return the draft to the drafter with the diagnosis as revision instructions; re-run de-slop (fresh context again) on the revision.
- **After 2 BLOCK→revise cycles** → stop and surface to the human with both diagnoses. Do not ship, and do not keep looping — a draft that can't find one concrete specific in three attempts has a sourcing problem, not a writing problem.

## Composition with authenticity-pass

These are complementary gates. De-slop is the general editorial gate (style + a baseline substance check) for all external media; `authenticity-pass` is the stricter, social-calibrated substance rubric (metric, constraint, opinion). For LinkedIn/X posts, run de-slop first, then authenticity-pass. For everything else — docs, decks, email, web copy — de-slop alone is the pre-publish gate.

## Wired callers

Skills that MUST invoke this gate (cold-read, per the rule above) before content goes out:

| Caller | Gate point |
|--------|-----------|
| `email` | before sending or finalizing any draft to an external recipient |
| `do-presentation` | after the self-review pass, before export |
| `linkedin` / `x-com` | before `authenticity-pass`, for anything going live |

Conversational chat (Telegram replies, inline answers to the user) is exempt by design.

## Medium notes

- **Messages / email**: boilerplate openers and closers, length inflation (three paragraphs for a one-line answer), over-formatting a chat message with headers and bold.
- **Documents**: scaffolding, bold overuse, section summaries, title-case headings, bullet walls that should be prose.
- **Presentations**: bullets are fine; watch for rule-of-three slides, inflated-significance titles, and identical rhythm across every slide.
- **Web / marketing copy**: promotional adjective stacks ("vibrant," "seamless," "nestled"), hedge-hype mix, negative parallelism in headlines.
