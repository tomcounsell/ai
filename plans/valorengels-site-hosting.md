# Plan: Host the Valor docs site at valorengels.com

Status: high-level outline. A future agent should flesh this out before executing.

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

- [ ] Confirm with Tom that publishing the bundled knowledge graph is acceptable: it exposes file paths, function and class names, and per-file summaries of the private repo. Flagged on 2026-07-12.
- [ ] Add `robots.txt` and a 404 page (Workers assets `not_found_handling`).
- [ ] Add an `og:image` so social shares render a card (meta tags are already in place, text-only).
- [ ] Pick apex as canonical and redirect `www` to it (canonical links on the pages already point at the apex).

## Later, optional

- A small deploy script or CI step so redeploys are one command; manual `wrangler deploy` is fine to start.
- Merge `docs/valor-site` to main, or keep the site on its own branch and deploy from the worktree. Note `.gitignore` line 322 has a pre-existing `/site` rule; the files are force-added, so drop the rule if `site/` becomes permanent.

## Done when

- https://valorengels.com serves the site over HTTPS with the custom domain attached.
- The redeploy path is written down (site README or a script), so any agent can ship an update.
