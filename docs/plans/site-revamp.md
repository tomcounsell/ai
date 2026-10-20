# valorengels.com, rebuilt for the rebuilt system

**Ask.** Tom, valor team chat, 2026-10-18: revamp valorengels.com after
the rebuild. The structure may start over, but the site stays an
educational resource on Valor, what he does, and how the plumbing works.
On 2026-10-20 Tom set the content in priority order: "Valor's identity,
maker, account links, infra references (can mostly keep as is); the
guiding philosophy; alignment, corrigibility, RSI; the hard rules; SDLC
pipeline; durability strategy and db models". He asked to review the look
from screenshots before anything is published.

**Premise, checked.** `site/` came back unchanged from the old system
(0965d3125). Its runtime, memory, reflections, codebase and pipeline pages
describe Redis, Popoto, the worker and the bridge. The site is served by
the Cloudflare Worker `valorengels-site`, which serves `./site` and
redirects www to the apex. The old system deployed it with `wrangler.jsonc`,
`src/index.js` and `scripts/deploy-site.sh` (`git show 9023ffbad^:<path>`),
and the rebuild dropped all three. The token is the 1Password item
2qm4qqe5zs4gdv5divlz246iga (vault m-valor, field API_TOKEN), on Yudame's
account. github.com/tomcounsell/ai is public, so source links can point
at files on `main`.

## What is built

1. **Pages, in Tom's order.** Each is one HTML file in `site/` and keeps
   the current site's CSS as the base, adjusted to the present skill's page
   standards with house-style as the fallback: one column on a phone,
   about 16px minimum body text, nothing wider than the screen.
   1. `index.html`: identity, maker, account links and infra references
      kept from today's page. Only what the rebuild made wrong is
      corrected, against `docs/persona.md`, `persona/`, `docs/machine.md`
      and `docs/tech-stack.md`. A short map of the site follows.
   2. `philosophy.html`: the guiding philosophy, from `docs/mission.md`,
      `docs/persona.md` and the `persona/` sources (one identity, owned
      work, asking well, done means used).
   3. `alignment.html`: alignment, corrigibility and RSI. Grounded only in
      `docs/mission.md`, `docs/persona.md`, how corrections are kept and
      rendered into every turn (`core/`, `persona/`), the
      steering-as-data rule, effect ceilings and the approval surface. RSI
      means how Valor changes Valor: through tasks, plans, critique and
      review like any other repo, and rule 7. Every statement links to its
      file.
   4. `rules.html`: the governance paragraph and the ten hard rules
      verbatim from `CLAUDE.md`, each with one plain line on why it
      exists, told in general terms with no clients named.
   5. `pipeline.html`: the SDLC stages and transitions from
      `docs/sdlc-state-machine.md` and `skills/sdlc/`, as an inline SVG
      diagram plus text, covering critique and review rounds, the merge
      predicate and the delivery.
   6. `durability.html`: the resident kernel, the Postgres ledger and its
      tables, recovery and reaping, retries, turn slots and rollout
      restarts, from `core/README.md`, `docs/architecture.md`,
      `docs/data.md` and the model or schema files they name. Also a short
      tour of harnesses and bridges (`docs/harnesses.md`, `docs/bridges/`).
2. **Research.** `research.html` and `research/*.html` stay as they are,
   with one dated note on each saying it compares the system as it was
   before the 2026-10-14 cutover.
3. **Removed.** `runtime.html`, `memory.html`, `reflections.html`,
   `codebase.html` and `assets/graph.js`, a 36k-line snapshot of the old
   codebase. `src/index.js` answers each old URL with a 301 to its nearest
   new page, so existing links keep working. `sitemap.xml` and `404.html`
   are updated to match.
4. **Deploy restored.** `wrangler.jsonc` and `src/index.js` come back as
   they were, plus the redirect map. `scripts/deploy-site.sh` reads the
   token at run time with
   `op item get 2qm4qqe5zs4gdv5divlz246iga --vault m-valor --fields API_TOKEN --reveal`,
   sets `CLOUDFLARE_ACCOUNT_ID` from the account the token lists
   (`/accounts`), runs `wrangler deploy` and curls the apex. The token
   never touches the repo, and the `.env` token is not used.
5. **Public safety.** No client names, chat ids, keys, email addresses
   other than Valor's, or people other than Tom.

## Order of work

1. Build the home page, `philosophy.html` and `rules.html`.
2. Screenshot them with BYOB on a phone and on desktop, then write the
   PNG paths to the chat session for Tom's review of the look.
3. Build the rest, adjusting to his feedback.
4. Publish only after Tom approves the look, check the live URLs, and
   report.

## Out of scope

- Rewriting the research comparisons.
- Any change to the system the site describes.
- A build step or framework. The site stays static HTML.
- Wiring the deploy into the merge path. It runs by hand from
  `scripts/deploy-site.sh`; automating it would be a second mechanism
  nobody asked for.

## Tests (`tests/test_site.py`)

- Every internal `href`/`src` in `site/` resolves to a file, and every
  `#anchor` exists on its target page.
- Every link into `github.com/tomcounsell/ai/blob/main/<path>` names a path
  that exists in this checkout, so a renamed doc breaks the suite, not the
  site.
- `sitemap.xml` lists exactly the HTML pages in `site/` (404 excluded).
- No page contains a Telegram chat id (`-100` followed by digits), a token
  shape (`ops_`, `sk-`, `ghp_`, 40-character hex), or an email address
  other than valor@yuda.me. This is the documented rule for a public site.
- Each old URL in the redirect map of `src/index.js` points at a page that
  exists.

By hand: BYOB screenshots at phone width (390px) and desktop (1440px) of
every page, light and dark if the CSS has both, with no horizontal scroll
on the phone. After the deploy: `curl -sI` for the apex 200, www 301, and
an old URL 301.

**Governance.** None added. The tests assert the site's documented
invariants. The deploy script's liveness curl reports, it doesn't gate.
