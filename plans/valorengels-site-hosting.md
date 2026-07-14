# Plan: Host the Valor docs site at valorengels.com

Status: **done**. Live at https://valorengels.com (deployed 2026-07-13).

Site source: `site/` on branch `docs/valor-site`. Self-contained static HTML/CSS/JS, no build step, no external requests. About 1 MB total, dominated by `assets/graph.js` (the bundled knowledge graph, ~130 KB over the wire with Brotli).

## Goal

Serve `site/` at https://valorengels.com for $0/month. The domain is already registered on Cloudflare, so hosting stays on Cloudflare.

## Approach

Cloudflare Workers static assets, deployed by direct upload (the private repo never connects to Cloudflare's git integration):

1. One-time human step: `npm i -g wrangler && wrangler login` (interactive OAuth).
2. Add a minimal `wrangler.jsonc` at the repo root or under `site/` with `assets.directory` pointing at the site files and a custom-domain route for `valorengels.com`.
3. Deploy with `wrangler deploy`. Re-deploy the same way after any `site/` change.
4. Attach the custom domain (one dashboard step or a `routes` entry; DNS is already on Cloudflare).

Classic Cloudflare Pages (`wrangler pages deploy site/`) is an equally free fallback if Workers assets hits friction.

## Before first deploy

- [x] Confirm with Tom that publishing the bundled knowledge graph is acceptable: it exposes file paths, function and class names, and per-file summaries of the private repo. Flagged on 2026-07-12. **Resolved 2026-07-13: the ai repo is public open source, publish as-is.**
- [x] Add `robots.txt` and a 404 page (Workers assets `not_found_handling`).
- [x] Add an `og:image` so social shares render a card (meta tags are already in place, text-only).
- [x] Pick apex as canonical and redirect `www` to it (canonical links on the pages already point at the apex).

## What actually shipped

- `wrangler.jsonc` at the repo root: Workers static assets (`assets.directory: ./site`), `custom_domain` routes for both `valorengels.com` and `www.valorengels.com`, and `assets.run_worker_first: true`.
- `src/index.js`: a one-route Worker (`main` entry) that 301-redirects `www.valorengels.com` to the apex, then falls through to `env.ASSETS.fetch(request)` for everything else. `run_worker_first: true` is required — by default Cloudflare serves matching static assets directly and never invokes the Worker script, which silently no-ops any hostname-based logic like this redirect.
- `site/404.html`, `site/robots.txt`, `site/sitemap.xml`, `site/assets/og-image.png` (1200x630, generated with Pillow, no external asset pipeline).
- Cloudflare auth: `wrangler login` (interactive OAuth) failed because claude-in-chrome's automated browser tab doesn't share cookies/session with the user's regular logged-in Chrome window. Fell back to a scoped API token (Zone DNS/Workers Routes/SSL edit + Account Workers Scripts edit, scoped to the `valorengels.com` zone and the Yudame Account) stored as `CLOUDFLARE_API_TOKEN` in `~/Desktop/Valor/.env`, plus `CLOUDFLARE_ACCOUNT_ID` (wrangler's `/memberships` account-lookup call needs a broader token scope than this one has, so the account ID must be supplied explicitly).

## Redeploy path

```
cd /Users/valorengels/src/ai   # or a worktree checked out on docs/valor-site
wrangler deploy
```

`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are read from the vault `.env` automatically. Any change under `site/` (new files need `git add -f` — see `.gitignore` line 322) or to `wrangler.jsonc`/`src/index.js` just needs a re-run of `wrangler deploy`.

## Later, optional

- A small deploy script or CI step so redeploys are one command; manual `wrangler deploy` is already one command, so low priority.
- Merge `docs/valor-site` to main, or keep the site on its own branch and deploy from the worktree. Note `.gitignore` line 322 has a pre-existing `/site` rule; the files are force-added, so drop the rule if `site/` becomes permanent.

## Done when

- [x] https://valorengels.com serves the site over HTTPS with the custom domain attached.
- [x] The redeploy path is written down (site README or a script), so any agent can ship an update.
