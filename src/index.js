export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "www.valorengels.com") {
      url.hostname = "valorengels.com";
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
