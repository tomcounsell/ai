# valorengels.com: product and project work beside engineering

**Ask.** Tom, Slack DM, 2026-11-04 16:32 UTC: "with all the product work and
capabilities you've taken on, we should update your website accordingly.
quite soon you will be equally adept at being a product/project manager as an
engineer". The site presents what is built today; "quite soon" is not claimed.
Screenshots go to the session first; publishing (`scripts/deploy-site.sh`)
waits for Tom's word.

**Premise, checked.** Every page's footer says "valor engels · ai engineer at
yudame" and "updated 2026-10-22"; the home page title, description, Open Graph
tags and card say "AI engineer at Yudame"; `assets/og-image.png` says "An
autonomous AI software engineer". `chat.html` lists team and engineering chats
only, though product rooms exist (`docs/bridges/slack.md`). The repo is public
(github.com/tomcounsell/ai): no client, person or channel is named on the site.

## What is built

1. **Identity.** Title and `og:title`: "Valor Engels · AI product manager and
   engineer at Yudame"; descriptions to match. Card role "Product, projects and
   engineering · Yudame". Footer on every page (root, `vs/`, 404): "valor engels
   · product manager and engineer at yudame", "updated 2026-11-04".
   `og-image.png` re-rendered at 1200×630 in the same layout with the new line.
2. **Nav.** A "Product and projects" link to `product.html`, before
   Engineering, on every page.
3. **Home.** The hero lede adds the product and project work. A new short
   section, "What Valor does", in two columns: product and projects /
   engineering, each a few lines linking to its page, with sources. The site
   map gets a `product.html` row.
4. **`product.html`**, new, the existing page template, one source line under
   each section, a page-next link:
   - *Product rooms*: the backlog Sheet (goal on top, mandatory and bet
     lanes, ranks only ready items), the readiness review after which Valor
     approves an item for build, chasing blockers with owners, a review after
     each ship. `skills/valor/skills/product-sheet/SKILL.md`,
     `skills/sdlc/roles/product.md`.
   - *Decisions*: one Slack canvas per project of decisions open for Tom,
     answered by chat, call or reaction; the ledger is the record.
     `core/board.py`, `docs/plans/decision-board.md`.
   - *Running the work*: the chat session reads, dispatches to tasks, and
     reports back; questions batched with stated defaults.
     `skills/sdlc/converse.md`, `persona/`.
   - *Calls*: Google Meet, Valor posts the link, joins, answers aloud or in
     the call chat, and the summary goes to the thread. `bridges/meet/`,
     `skills/sdlc/call.md`.
   - *Research and podcasts*: a deep-research run returns a sourced report
     PDF; on Tom's word it becomes an episode on the Yudame Research or Staff
     podcast. `skills/valor/skills/deep-research/`, `docs/plans/research-podcast.md`.
   - *Business operations*: Mercury invoices with a pay link, created after
     Tom's OK, and the unpaid list; no reminders. Email drafts in the asker's
     voice, handed back as text; a send only on the asker's word.
     `skills/valor/skills/mercury-invoicing/SKILL.md`, `skills/sdlc/converse.md`,
     `persona/voice.md`.
   Each claim is checked against its source file at build time; anything the
   file does not support is cut. No counts, no client names, no "soon".
5. **`chat.html` Rooms**: a third column for product rooms.
6. **Sitemap** adds `product.html`.
7. De-slop pass over the new and changed copy.

**Out of scope.** Publishing; other pages' content; the stylesheet; the `vs/`
pages beyond nav and footer.

## Tests

`tests/test_site.py` already covers what this touches: every link and anchor
lands (the new nav on every page, the new page's anchors), every source link
names a real file (each new source line), the sitemap matches the pages
(`product.html`), and the privacy patterns. Beyond it, by hand: grep the site
for "engineer at yudame" and "software engineer" (none left outside history);
grep for every client and channel name in `~/Desktop/Valor/projects/` (none);
full suite green and under 5 minutes. Screenshots at 1440 and 390 wide of
home, product and chat, plus the new OG image, into
`~/Desktop/Valor/site-revamp-screens/product/`; phone views checked for
sideways scroll.

**No governance added.**
