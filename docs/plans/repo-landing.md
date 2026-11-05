# The repo's landing: product and project work beside engineering

**Ask.** Tom, Slack DM, 2026-11-05 06:43 UTC, after valorengels.com went live
as "AI product manager and engineer at Yudame" (`docs/plans/site-product.md`):
"review the tomcounsell/ai repo and make similar adjustments to the repo
description and readme because that's also a common landing page for curious
parties".

**Premise, checked.** `gh repo view` says PUBLIC; description "A productive AI
coworker that learns, self-improves, and ships work."; no homepage; nine
engineering topics. README.md opens with the dictionary entry for
*corrigible*, then one 180-word paragraph, then `## Mission`. That paragraph
says "one Mac per client" (true: `docs/machine.md`, four Macs, Tom
2026-10-11), "fully free, with no spend ceiling" (true as a commitment:
`docs/mission.md`, turns run on Valor's own Claude subscription), "every
message and pull request it sends leaves as Valor" (stale: the kernel merges
and there is no pull request, `skills/sdlc/`; and only a chat session sends).

## What is built

1. **README opening** (everything between the title and `## Mission`). Keep
   the *corrigible* entry (it names the repo's idea). Replace the paragraph
   with three short parts, plain words, present tense, only what is built:
   - *Who:* Valor Engels, an AI product manager and engineer at Yudame,
     working beside Tom Counsell on his own accounts; link to
     https://valorengels.com.
   - *What he does today:* two short lists mirroring `site/product.html` and
     the engineering page. Product and projects: backlog Sheets and the
     readiness review, blockers chased and shipped work reviewed, the decision
     board, requests in chat becoming tasks that report back, Meet calls,
     research reports and the public Yudame Research podcast, client invoices
     and email drafts. Engineering: plans, builds, tests and merges in the
     repositories he works in.
   - *How it is built:* two or three sentences keeping the true parts of the
     old paragraph: one install per Mac, one Mac per client; a deterministic
     kernel holds authority (effects bounded, spending metered, an
     append-only ledger, stop at any instant); autonomy grows by Tom's
     decision. Drop or fix "pull request".
   Each claim is checked against its source file at build time (the site's
   cited sources serve); anything unsupported is cut. No bank, no client, no
   private podcast name, no counts, no "soon". Then the de-slop skill over
   the new copy.
2. **Repo settings**, with `gh repo edit tomcounsell/ai`: description "AI
   product manager and engineer at Yudame: runs backlogs, decisions and
   client work, and builds and ships the code." (de-slopped, under 350
   chars); homepage https://valorengels.com; add topics
   `product-management`, `project-management`, `ai-coworker`,
   `ai-product-manager`, `yudame`, keeping the existing nine.

## Out of scope

Mission, "What corrigible means here", the governance paragraph, the hard
rules, every other README section, file and folder names, repo visibility.
The `## Status` section is stale (says "setup phase" and that the old system
lives on `main`; it is at tag `old-system`); it is below Mission, so it is
named in the report, not changed.

## Tests

No code changes, so no new test. `tests/test_bloat.py` and the full suite stay
green. Checked by hand at build: `git diff main -- README.md` touches only
lines above `## Mission`; `grep` over README and description finds no bank
name, client name or private podcast name; every link in the new opening
resolves; `gh repo view --json description,homepageUrl,repositoryTopics`
shows the new values.

**Stakes.** Public copy that is easy to revert; the cost of a mistake is a
false or private claim on Valor's public landing page.
**Loops.** Critique 0. Review 1: the reviewer checks each claim against its
source and that nothing below the opening moved.
