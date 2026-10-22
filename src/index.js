// Pages the 2026-10 rewrite removed, sent to the page that now covers them.
// Keys have no ".html", so /memory and /memory.html both redirect.
const MOVED = {
  "/runtime": "/durability.html",
  "/codebase": "/durability.html",
  "/memory": "/durability.html#memory",
  "/reflections": "/chat.html#routines",
  "/work": "/chat.html",
  "/research": "/comparisons.html",
  "/research/valor-vs-paperclip": "/comparisons.html",
  "/research/valor-vs-managed-agents": "/comparisons.html",
  "/research/managed-agents-vs-perplexity-computer": "/comparisons.html",
  "/research/openhuman-vs-hermes": "/comparisons.html",
};

// /activity.svg: Valor's merged pull requests by day over the last 26 weeks,
// read from GitHub at request time and cached for an hour.
const WEEKS = 26;
const DAY_MS = 86400000;
const RAMP = ["#ebe5d7", "#ddbda8", "#d09579", "#c26d4a", "#b5451b"];
const SERIF = `"Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif`;
const MONO = "ui-monospace, Menlo, monospace";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const QUERY = `query($q: String!, $cursor: String) {
  search(type: ISSUE, query: $q, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { mergedAt url repository { nameWithOwner isPrivate } } }
  }
}`;

const isoDate = (ms) => new Date(ms).toISOString().slice(0, 10);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// The grid's first day: the Monday WEEKS - 1 weeks before the Monday of asOf's week.
function gridStart(asOf) {
  const t = Date.parse(asOf + "T00:00:00Z");
  const sinceMonday = (new Date(t).getUTCDay() + 6) % 7;
  return t - (sinceMonday + 7 * (WEEKS - 1)) * DAY_MS;
}

// days: { "YYYY-MM-DD": { count, url } } where url is the day's first public PR or null.
// totals: { prs, repos, public } over the last 30 days. days null draws the unavailable state.
function renderActivity(days, totals, asOf) {
  const out = [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 150" width="760" height="150" role="img" aria-label="Merged pull requests by day, last 26 weeks">`,
    `<rect width="760" height="150" fill="#faf7f0"/>`,
  ];
  const text = (x, y, size, fill, body, extra = "") =>
    `<text x="${x}" y="${y}" font-family='${MONO}' font-size="${size}" fill="${fill}"${extra}>${body}</text>`;
  if (!days) {
    out.push(text(0, 16, 12, "#1b1a17", "Activity is read from GitHub; it is not available right now."), "</svg>");
    return out.join("");
  }
  const n = (v) => `<tspan fill="#b5451b">${v}</tspan>`;
  out.push(
    text(0, 16, 12, "#1b1a17",
      `Last 30 days: ${n(totals.prs)} pull requests merged · ${n(totals.repos)} repositories · ${n(totals.public)} public`),
    text(760, 16, 11, "#8b867b", `as of ${esc(asOf)}`, ` text-anchor="end"`),
  );
  const max = Math.max(0, ...Object.values(days).map((d) => d.count));
  const x0 = 30, y0 = 50, step = 13;
  for (const [row, label] of [[0, "Mon"], [2, "Wed"], [4, "Fri"]]) {
    out.push(text(0, y0 + row * step + 9, 9, "#8b867b", label));
  }
  const start = gridStart(asOf);
  for (let col = 0; col < WEEKS; col++) {
    const x = x0 + col * step;
    for (let row = 0; row < 7; row++) {
      const t = start + (col * 7 + row) * DAY_MS;
      const date = isoDate(t);
      if (date.endsWith("-01")) out.push(text(x, y0 - 8, 10, "#8b867b", MONTHS[new Date(t).getUTCMonth()]));
      const y = y0 + row * step;
      if (date > asOf) {
        out.push(`<rect x="${x}" y="${y}" width="10" height="10" rx="2" fill="none" stroke="#e9e4d8"/>`);
        continue;
      }
      const day = days[date] || { count: 0, url: null };
      const level = day.count ? Math.max(1, Math.ceil((4 * day.count) / max)) : 0;
      const cell = `<rect x="${x}" y="${y}" width="10" height="10" rx="2" fill="${RAMP[level]}"><title>${date}: ${day.count} merged</title></rect>`;
      out.push(day.url ? `<a href="${esc(day.url)}" target="_top">${cell}</a>` : cell);
    }
  }
  out.push(
    `<text x="${x0 + WEEKS * step + 30}" y="${y0 + 16}" font-family='${SERIF}' font-size="18" font-weight="600" fill="#1b1a17">Merged pull requests, by day</text>`,
    text(x0 + WEEKS * step + 30, y0 + 36, 11, "#5b574e", "github.com/valorengels · last 26 weeks"),
    text(x0 + WEEKS * step + 30, y0 + 54, 11, "#5b574e", "a day links to its first public pull request"),
  );
  let lx = 760 - 5 * step - 34;
  out.push(text(lx - 6, 146, 10, "#8b867b", "fewer", ` text-anchor="end"`));
  for (const fill of RAMP) {
    out.push(`<rect x="${lx}" y="137" width="10" height="10" rx="2" fill="${fill}"/>`);
    lx += step;
  }
  out.push(text(lx + 3, 146, 10, "#8b867b", "more"), "</svg>");
  return out.join("");
}

async function readActivity(token, asOf) {
  const q = `author:valorengels is:pr is:merged merged:>=${isoDate(gridStart(asOf))}`;
  const days = {};
  const recent = { prs: 0, repos: new Set(), public: new Set() };
  const since30 = isoDate(Date.parse(asOf + "T00:00:00Z") - 29 * DAY_MS);
  let cursor = null;
  for (let page = 0; page < 10; page++) {
    const res = await fetch("https://api.github.com/graphql", {
      method: "POST",
      headers: { Authorization: `bearer ${token}`, "User-Agent": "valorengels-site", "Content-Type": "application/json" },
      body: JSON.stringify({ query: QUERY, variables: { q, cursor } }),
    });
    if (!res.ok) throw new Error(`GitHub answered ${res.status}`);
    const body = await res.json();
    if (body.errors || !body.data) throw new Error("GitHub returned errors");
    const search = body.data.search;
    for (const pr of search.nodes) {
      if (!pr.mergedAt) continue;
      const date = pr.mergedAt.slice(0, 10);
      const day = (days[date] ||= { count: 0, url: null });
      day.count += 1;
      if (!pr.repository.isPrivate && !day.url) day.url = pr.url;
      if (date >= since30) {
        recent.prs += 1;
        recent.repos.add(pr.repository.nameWithOwner);
        if (!pr.repository.isPrivate) recent.public.add(pr.repository.nameWithOwner);
      }
    }
    if (!search.pageInfo.hasNextPage) break;
    cursor = search.pageInfo.endCursor;
  }
  return { days, totals: { prs: recent.prs, repos: recent.repos.size, public: recent.public.size } };
}

async function activity(request, env, ctx) {
  const key = new Request(request.url);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const asOf = isoDate(Date.now());
  try {
    if (!env.GITHUB_TOKEN) throw new Error("no GITHUB_TOKEN");
    const { days, totals } = await readActivity(env.GITHUB_TOKEN, asOf);
    const res = new Response(renderActivity(days, totals, asOf), {
      headers: { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "public, max-age=3600" },
    });
    ctx.waitUntil(caches.default.put(key, res.clone()));
    return res;
  } catch (err) {
    console.log(`activity.svg: ${err.message}`);
    return new Response(renderActivity(null, null, asOf), {
      headers: { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.hostname === "www.valorengels.com") {
      url.hostname = "valorengels.com";
      return Response.redirect(url.toString(), 301);
    }
    if (url.pathname === "/activity.svg" && request.method === "GET") {
      return activity(request, env, ctx);
    }
    const moved = MOVED[url.pathname.replace(/\/$/, "").replace(/\.html$/, "")];
    if (moved) {
      return Response.redirect(new URL(moved, url.origin).toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
