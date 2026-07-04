---
name: authenticity-pass
description: "Pre-publish human-signal gate for social media drafts. Use when /linkedin or /x-com is about to publish a post, or when asked to run an authenticity check on any draft file."
allowed-tools: Read, Bash, Write
user-invocable: true
argument-hint: "[draft-file-path]"
---

# Authenticity Pass

A mandatory pre-publish gate. Reads the draft, scores it against three human-signal markers, and returns a structured PASS or BLOCK verdict with specific remediation.

The cold-reader loop (in `/linkedin` and `/x-com`) handles style, register, and audience fit. This gate handles one thing only: does this content carry signal that proves a human with real experience wrote it?

## Input

**Called by `/linkedin`:** reads `/tmp/linkedin-post.txt`
**Called by `/x-com`:** reads `/tmp/x-post.txt`
**Called directly:** reads the file path given as argument, or prompts if none.

If the file doesn't exist or is empty, return BLOCK with reason "no draft found."

---

## The three human-signal markers

### 1. METRIC
A specific, defensible number. Vague intensifiers don't count.

| COUNTS | DOES NOT COUNT |
|--------|----------------|
| "8s → 600ms" | "significantly faster" |
| "412 restarts in a week" | "restarted many times" |
| "30% fewer impressions" | "fewer impressions" |
| "200 to 3,000 followers in 4 months" | "huge growth" |
| "$53k per violation" | "large fines" |
| "5 hours/week" | "a few hours" |

### 2. CONSTRAINT
An acknowledged limitation, failure, or honest tradeoff. The post must admit something didn't work, costs something, only applies under conditions, or was a hard call.

| COUNTS | DOES NOT COUNT |
|--------|----------------|
| "broke in prod until we added X" | pure success framing |
| "only works if Y is true" | "works great" |
| "costs 3× more than the naive approach" | "it's efficient" |
| "we tried Z first and it failed" | "we built X and it works" |
| "the tradeoff is losing P to gain Q" | generic capability summary |
| "this breaks when load exceeds N" | no conditions named |

### 3. OPINION
A clear point of view the author holds that could be argued against. Factual description is not opinion.

| COUNTS | DOES NOT COUNT |
|--------|----------------|
| "most teams underestimate this cost" | "here's how it works" |
| "I'd do this differently now" | "we built X" |
| "the real bottleneck is Y not Z" | neutral summary |
| "this approach is better when..." | "both approaches have merit" |
| "the industry is getting this backwards" | "there are different schools of thought" |

---

## Gate criteria

**LinkedIn / long-form (post body > 280 chars):**
- PASS = all three markers present
- BLOCK = any marker missing

**X / short-form (post body ≤ 280 chars):**
- PASS = at least METRIC or CONSTRAINT is present
- BLOCK = zero markers present (pure framing with no grounding)

Replies on either platform follow the X/short-form rule.

---

## Verdict format

Return exactly this structure, nothing else:

```
AUTHENTICITY VERDICT: PASS

MARKERS:
  METRIC:     FOUND — "the exact phrase from the draft"
  CONSTRAINT: FOUND — "the exact phrase from the draft"
  OPINION:    FOUND — "the exact phrase from the draft"

→ CLEARED FOR PUBLISH
```

Or on failure:

```
AUTHENTICITY VERDICT: BLOCK

MARKERS:
  METRIC:     FOUND — "..." / MISSING
  CONSTRAINT: FOUND — "..." / MISSING
  OPINION:    FOUND — "..." / MISSING

BLOCKING GAPS:
  [List only the missing markers]

TO UNBLOCK:
  [One specific, actionable suggestion per missing marker. Not "add a metric" —
   instead: "from the source material, the number X (e.g. 'we ran Y tests in Z seconds')
   would satisfy METRIC" or "naming the failure mode ('this breaks when...') would
   satisfy CONSTRAINT." Pull from what you know about the content's context.]
```

Do not editorialize beyond this format. Style is the cold-reader loop's job.

---

## What the calling skill does with the verdict

- **PASS** → proceed to publish
- **BLOCK** → return the draft to the drafter subagent with the BLOCKING GAPS as revision instructions. The drafter revises and the authenticity-pass is re-invoked on the new draft.
- **After 2 BLOCK→revise cycles with no PASS** → drop the post. The source material is too generic; no amount of editing will manufacture signal that isn't there.

The 2-retry cap matters. A post that needs three authenticity loops to find one real number is a post without an angle. Better to ship nothing than to manufacture false specificity.

---

## Why this gate exists

The Yudame Research report on AI content marketing (May 2026) identified the authenticity pass as "the single highest-leverage habit change" for founders using AI for content. The evidence:

- 30-40% fewer LinkedIn impressions for pure-AI posts (360Brew practitioner data)
- LinkedIn's 360Brew algorithm specifically penalizes content that lacks embedded professional experience
- X's Phoenix algorithm suppresses high-frequency, low-variation automated posting patterns
- 85% of consumers report "uncanny valley" reactions to AI-generated content
- The only published cases of AI-assisted content driving measurable growth kept ideation and editorial review human

The three markers in this gate are the minimum human-signal footprint that distinguishes "AI helped me write this" from "AI wrote this instead of me."
