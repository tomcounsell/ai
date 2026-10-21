# valorengels.com, rebuilt for the rebuilt system

**Ask.** Tom, valor team chat, 2026-10-18: revamp valorengels.com after
the rebuild. The structure may start over, but the site stays an
educational resource on Valor, what he does, and how the plumbing works.
On 2026-10-20 Tom set the content in priority order: "Valor's identity,
maker, account links, infra references (can mostly keep as is); the
guiding philosophy; alignment, corrigibility, RSI; the hard rules; SDLC
pipeline; durability strategy and db models". He asked to review the look
from screenshots before anything is published. Those two messages reached
this task from the valor chat session (the "written to you while you
worked" notes of 2026-10-20), not from answers.md. They supersede its
"publish once the site is ready" only in holding the publish until Tom has
seen the look.

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
      tour of harnesses and bridges (`docs/harnesses.md`, `docs/bridges/`),
      plus a **memory** section (`docs/memory.md`, `memory/`): what is
      kept, where, and how it is recalled into a turn.
   7. `work.html`: what Valor does, covering projects and rooms
      (`projects/`, room descriptions, team chats compared with eng
      chats), tasks from a message to a merged PR and what reaches Tom
      (`skills/sdlc/converse.md`, `persona/delivery.md`), and **routines**
      (`docs/routines.md`, `routines/`). Placed after the pipeline in the
      nav, because Tom's list does not name it.
2. **Comparisons, replacing research.** Tom, 2026-10-21 15:07 UTC: "let's
   replace our comparisons. the actual closest comparison is actually
   Firstmate ... you can drop Paperclip and others like Herdr that are
   mostly interfaces". `comparisons.html` compares Valor with Firstmate
   first, then OpenClaw and the hosted agents (Claude Cowork, Meta Muse,
   Grok Bot, OpenAI dots) on independence and data sovereignty, each
   product claim sourced through the web agent. `research.html` and the
   four `research/*.html` articles are deleted, and `src/index.js` sends
   each old address, with or without `.html`, to `comparisons.html`.
3. **Removed.** `runtime.html`, `memory.html`, `reflections.html`,
   `codebase.html`, `assets/graph.js` (a 36k-line snapshot of the old
   codebase), and `assets/site.js`, which only renders that graph
   (`window.VALOR_GRAPH`, all 134 lines). `src/index.js` answers each old URL
   with a 301 so existing links keep working: `/runtime.html` and
   `/codebase.html` go to `/durability.html`, `/memory.html` to
   `/durability.html#memory`, and `/reflections.html` to
   `/work.html#routines`. `sitemap.xml` and `404.html`
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
2. Screenshot them with BYOB on a phone and on desktop, saving the PNGs
   inside the workspace, and leave their absolute paths and sha256s to the
   chat session. That session asked for exactly this and attaches them to
   Tom on Telegram as files, since a bare path on this machine is no use to
   him.
3. Build the rest, adjusting to his feedback.
4. Publish only after Tom approves the look, check the live URLs, and
   report.

## Out of scope

- Any change to the system the site describes.
- A build step or framework. The site stays static HTML.
- Wiring the deploy into the merge path. It runs by hand from
  `scripts/deploy-site.sh`; automating it would be a second mechanism
  nobody asked for.

## Tests (`tests/test_site.py`)

- Every internal `href`/`src` in `site/` resolves to a file, and every
  `#anchor` exists on its target page. A redirect is not a file: no page
  links to a deleted URL.
- Every link into `github.com/tomcounsell/ai/blob/main/<path>` or
  `.../tree/main/<path>` names a path that exists in this checkout, a file
  for `blob` and a directory for `tree`. A renamed doc then breaks the
  suite, not the site.
- `sitemap.xml` lists exactly the HTML pages in `site/` (404 excluded).
- No file in `site/` contains a Telegram chat id (`-100\d{6,}`), a token
  shape (`\bsk-[A-Za-z0-9]{20,}`, `\bghp_[A-Za-z0-9]{36}`,
  `\bops_[A-Za-z0-9]{20,}`), or an email address other than valor@yuda.me.
  There is no bare hex check, because commit hashes are legitimate. This is
  the documented rule for a public site.
- Each old URL in the redirect map of `src/index.js` points at a page that
  exists.

By hand: BYOB screenshots at phone width (390px) and desktop (1440px) of
every page, light and dark if the CSS has both, with no horizontal scroll
on the phone. After the deploy: `curl -sI` for the apex 200, www 301, and
an old URL 301.

**Governance.** None added. The tests assert the site's documented
invariants. The deploy script's liveness curl reports, it doesn't gate.
