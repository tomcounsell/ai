// Pages the 2026-10 rewrite removed, sent to the page that now covers them.
// Keys have no ".html", so /memory and /memory.html both redirect.
const MOVED = {
  "/runtime": "/durability.html",
  "/codebase": "/durability.html",
  "/memory": "/durability.html#memory",
  "/reflections": "/work.html#routines",
  "/research": "/comparisons.html",
  "/research/valor-vs-paperclip": "/comparisons.html",
  "/research/valor-vs-managed-agents": "/comparisons.html",
  "/research/managed-agents-vs-perplexity-computer": "/comparisons.html",
  "/research/openhuman-vs-hermes": "/comparisons.html",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "www.valorengels.com") {
      url.hostname = "valorengels.com";
      return Response.redirect(url.toString(), 301);
    }
    const moved = MOVED[url.pathname.replace(/\/$/, "").replace(/\.html$/, "")];
    if (moved) {
      return Response.redirect(new URL(moved, url.origin).toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
