---
name: de-slop
description: "Editorial pass that removes AI-writing tells from a draft before it is published or sent. Use on documents, presentations, emails, messages, posts, README prose, or web copy once a first draft exists. Triggered by 'de-slop this', 'editorial pass', 'humanize this', 'remove the AI tells', or invoked by another skill as a pre-publish step."
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
argument-hint: "[draft-file-path]"
user-invocable: true
---

# De-slop

An editorial review step that runs **after** a first draft exists and **before** the content leaves the building. It detects and removes the characteristic signs of AI writing — slop vocabulary, formulaic structure, inflated significance, formatting tics — while preserving the draft's meaning, facts, and voice.

**Philosophy:** original writers (human or agent) are not burdened with style rules while drafting. Slop removal is a separate editorial pass, the way a copy editor works after the reporter files. Drafting skills should not inline these rules; they should hand finished drafts to this pass.

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
2. **Detection sweep.** Walk the draft against the catalog. Collect findings with location and category. Judgment over keyword matching: one "robust" is fine; a cluster of tells in one paragraph is the signal. The test is always *"would a competent human editor flag this?"* — not *"does this word appear on a list?"*
3. **Rewrite.** Fix findings in place. Prefer the smallest edit that removes the tell: delete the filler clause, swap the slop word for the plain one, break the triad, merge the bullet wall into prose. Never change facts, numbers, names, quotes, or commitments.
4. **Report.** Emit the change log (format below), including anything flagged but deliberately kept and why.

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

## Change log format

```
DE-SLOP: EDITED (14 changes)   # or CLEAN, or REWRITE RECOMMENDED

  vocabulary (5)      "leverage" → "use", "pivotal" → cut, ...
  parallelism (2)     "not just a tool, but a partner" → "a tool that ..."
  scaffolding (1)     cut closing paragraph restating the three sections
  boilerplate (2)     cut "I hope this finds you well", "Great question!"
  artifacts (1)       removed stray "[cite: 3]"

  KEPT: two em dashes in §2 — doing real appositive work
  FLAGGED (not fixed): "$40k saved" — verify before sending; substance is out of scope
```

`REWRITE RECOMMENDED` is for drafts where the slop is structural (the whole piece is scaffolding around nothing) — hand it back to the drafter with the diagnosis rather than polishing it.

## Composition with authenticity-pass

These are complementary gates. De-slop removes negative tells (signs AI wrote it); `authenticity-pass` verifies positive human signal (metric, constraint, opinion) in social drafts. For LinkedIn/X posts, run de-slop first, then authenticity-pass. For everything else — docs, decks, email, web copy — de-slop alone is the pre-publish step.

## Medium notes

- **Messages / email**: boilerplate openers and closers, length inflation (three paragraphs for a one-line answer), over-formatting a chat message with headers and bold.
- **Documents**: scaffolding, bold overuse, section summaries, title-case headings, bullet walls that should be prose.
- **Presentations**: bullets are fine; watch for rule-of-three slides, inflated-significance titles, and identical rhythm across every slide.
- **Web / marketing copy**: promotional adjective stacks ("vibrant," "seamless," "nestled"), hedge-hype mix, negative parallelism in headlines.
