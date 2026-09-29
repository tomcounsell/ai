---
name: weekly-review
description: "Stakeholder-friendly summary of recent commits by category, with contributor stats. Use for a weekly, monthly, sprint, or team review, or 'review the last N days'."
allowed-tools: Bash, Read, Write
argument-hint: "[days] [categories]"
model: sonnet
effort: medium
---

# Weekly Review

Write a one-page engineering review of the repo's recent commits that a product manager,
designer, or executive can read and that still means something to engineers. Plain text
with Unicode emojis, ready to paste into email, Slack, or a doc. Purely git-based.

Arguments: `<days> [categories]`, default 7 days and 5 categories (14 days for
bi-weekly; 30 days and 7 categories for monthly; 3 categories for a short review).

## Gather

```bash
# Fetch + ff-merge the upstream ref rather than `git pull`: `.git/FETCH_HEAD` is shared by
# every worktree, so a concurrent fetch can retarget a bare pull's merge. The merge may
# fail (no upstream, or diverged); this review is read-only either way.
git fetch && git merge --ff-only @{upstream} 2>/dev/null; git branch --show-current
git log --since="<DAYS> days ago" --oneline --no-merges
git log --since="<DAYS> days ago" --format="%an" --no-merges | sort | uniq -c | sort -rn
git log --since="<DAYS> days ago" --stat --no-merges | head -500
```

## Write

Show only the final review. Let the N categories emerge from the actual work, ordered by
impact, each with a specific name and a fitting emoji ("🔐 Credential & Authentication
Infrastructure", not "Auth"), and 2-5 bullets.

```
# Engineering Review - <Date Range>

🔐 **Category Name**
• **Feature/improvement name** - What it does and why it matters for users or the business

📊 **Team Statistics & Recognition**
• [X] total commits over [N] days ([Z] commits/day average)
• [Aggregate metrics: files changed, tests added, features completed]
• **[Name]**: [X] commits ([%]%) - [their focus areas in plain language]
```

- The title shows the full requested period (e.g. "Oct 6-13, 2025" for 7 days), not the span the commits happen to cover.
- Each bullet: bold title, dash, 1-2 sentences on what was done and why it matters. No jargon, code paths, method names, or file references.
- No numbered sections or categories.
- Contributors highest first, with percentages.
- A page, not a report.

## Save

Save the text to `/tmp/eng_review_<mon><day>-<day>.txt` (weekly, e.g.
`/tmp/eng_review_oct6-13.txt`) or `/tmp/eng_review_<mon><day>-<mon><day>.txt` (monthly,
e.g. `/tmp/eng_review_sep7-oct7.txt`). Deliver the review text itself: the recipient
cannot open the saved path, and a local-path reference left in a drafted message is
flagged before delivery.
