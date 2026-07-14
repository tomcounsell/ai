# valorengels.com Docs Site

The public static docs site at https://valorengels.com. As of #2058 its source
(`site/`) lives on `main` as a first-class `/do-docs` documentation location — no
longer a frozen snapshot on the `docs/valor-site` side branch.

## Page inventory

`site/*.html` — `index`, `tour`, `runtime`, `pipeline`, `layers`, `memory`, `404`.
Supporting files: `site/sitemap.xml`, `site/robots.txt`, `site/assets/` (CSS/JS/logos/
og-image). Deploy config: `wrangler.jsonc` + `src/index.js` at repo root. The site is
deliberately no-build, self-contained HTML — keep it that way.

## Living-docs integration

`/do-docs` treats `site/*.html` like any markdown doc: the inventory table, the
semantic doc-impact finder (HTML is preprocessed to heading-delimited text so
`chunk_markdown` chunks it per-section), and the stale-reference sweep
(`rg <term> --glob 'site/*.html'`, HTML-only) all see it. `site/assets/` — including
the 38k-line generated `graph.js` — is never indexed nor swept. The docs-auditor
apply-mode substrate is guarded to `.md` files only, so committed HTML is never
auto-rewritten.

## Redeploy path

```bash
scripts/deploy-site.sh    # wrangler deploy + liveness curl
```

Credentials (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`) load from the vault `.env`.
`/do-merge` runs the script automatically when a merged diff touches `site/`,
`wrangler.jsonc`, or `src/index.js` (see `docs/sdlc/do-merge.md`). On a machine without
`wrangler`/token it exits 0 with a "redeploy needed" notice.

## Rollback

If a deploy serves a bad page, roll back with `wrangler rollback` (Cloudflare keeps prior
deployment versions).

## Graph.js snapshot caveat

`site/assets/graph.js` and any `data-meta="commit"` reference are a machine-generated
snapshot of a past commit and do NOT auto-refresh with cascades. Regenerating them is
tracked separately in #2059; this feature integrates the hand-written page copy only.
