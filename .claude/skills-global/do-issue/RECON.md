# Reconnaissance Routine

Surface unknowns, conflicts, and stale assumptions before they get baked into the
issue: work already done, dead code, or two systems conflated as one. Run it for
any issue touching multiple files or systems, and always when the area changed
recently (the most common source of stale assumptions).

## 1. Broad scan

One Explore agent (thoroughness "very thorough", budget 5 minutes) maps the
affected area: source files, tests, the last few PRs, and docs describing the
intended architecture. It returns paths and key details.

## 2. Concerns

From the scan, list 3-8 specific, answerable questions about what is:
already done, conflicting (code vs docs, or two systems doing one job), stale,
conflated, missing infrastructure the request assumes, or at odds with the
current design direction. "Does the retry logic in X get called in production?"
is a concern; "investigate the job queue" is not.

## 3. Fan-out

One read-only Explore agent per concern, all in one message (budget 5 minutes
each). Each prompt carries the question, the files from the scan to read, and the
required return: findings plus a recommendation (CREATE / EXTEND / SKIP / FIX
FIRST / SPLIT).

## 4. Synthesize

Reconcile into four buckets; disagreements between agents are worth reporting,
since they reveal real architectural ambiguity. The issue body carries this
section verbatim in shape (a hook parses the heading and bucket labels):

```
## Recon Summary

**Confirmed:** [N items] — ready to include
- [Item]: [one-line summary]

**Revised:** [N items] — scope adjusted
- [Item]: [what changed and why]

**Pre-requisites:** [N items] — must fix first
- [Item]: [what's blocking and suggested action]

**Dropped:** [N items] — removed from scope
- [Item]: [why it was dropped]
```

Recon often changes scope. In an interactive session, show this summary and ask
before writing the issue. In a pipeline or headless run, put the summary in the
issue body and continue.
