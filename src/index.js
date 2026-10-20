// The pages the 2026-10 rewrite removed, sent to the page that now covers them.
const MOVED = {
  "/runtime.html": "/durability.html",
  "/codebase.html": "/durability.html",
  "/memory.html": "/durability.html#memory",
  "/reflections.html": "/work.html#routines",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "www.valorengels.com") {
      url.hostname = "valorengels.com";
      return Response.redirect(url.toString(), 301);
    }
    const moved = MOVED[url.pathname];
    if (moved) {
      return Response.redirect(new URL(moved, url.origin).toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
