---
name: authenticity-pass
description: "Pre-publish human-signal gate for social drafts. Use when /linkedin or /x-com is about to publish, or to authenticity-check a draft file."
allowed-tools: Read, Bash, Write
user-invocable: true
argument-hint: "[draft-file-path]"
---

# Authenticity Pass

A pre-publish gate for social posts, comments, and replies: does the draft carry the signal of a human with real experience? It scores the draft against three markers and returns PASS or BLOCK with specific remediation. Style and register are de-slop's and the caller's cold-reader loop's job, not this gate's.

**Input:** the draft file path passed by the caller or as the argument (linkedin posts: `/tmp/linkedin-post.txt`; X posts: `/tmp/x-post.txt`). Missing or empty file → BLOCK, reason "no draft found."

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

Nothing beyond this format.

---

## What the calling skill does with the verdict

- **PASS** → publish.
- **BLOCK** → the drafter revises from BLOCKING GAPS and this gate re-runs on the new draft.
- **After 2 BLOCK→revise cycles** → drop the item. Signal that isn't in the source material can't be edited in, and false specificity is worse than not posting.
