---
name: weekly-review
description: "Summarize a week, sprint, month, or requested period of engineering work from git history with contributor statistics."
---

# Weekly Review

Resolve the date range and timezone; default to the last seven days. Identify the relevant branch or remote ref and state it. Fetch if needed, but do not checkout, merge, stash, or alter the working tree to produce a review.
Read commit subjects, bodies, dates, authors, and relevant diffs for the full period. Exclude merge commits from contribution counts and account for squash history when describing the limits of those counts. Deduplicate work spanning several commits. A count of commits measures activity, not productivity or business impact.
Group the actual work into a few useful categories, ordered by consequence. Describe what changed and why users or the business care. Include accurate contributor counts and percentages; distinguish shipped work from changes merely present on a branch.
Save a concise Markdown review with the full requested date range in its title. Follow the user's voice and formatting preferences; avoid stock categories, invented benefits, and mandatory emoji. Return the artifact link.
