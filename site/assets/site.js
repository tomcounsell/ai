/* Shared helpers for the Valor documentation site.
   Reads window.VALOR_GRAPH (assets/graph.js) and fills any
   data-driven elements present on the current page. */

window.Valor = (function () {
  "use strict";

  var G = window.VALOR_GRAPH || { nodes: [], edges: [], layers: [], tour: [], project: {} };

  var nodesById = new Map();
  G.nodes.forEach(function (n) { nodesById.set(n.id, n); });

  // Edge indexes keyed by source and target node id.
  var edgesFrom = new Map();
  var edgesTo = new Map();
  G.edges.forEach(function (e) {
    if (!edgesFrom.has(e.source)) edgesFrom.set(e.source, []);
    edgesFrom.get(e.source).push(e);
    if (!edgesTo.has(e.target)) edgesTo.set(e.target, []);
    edgesTo.get(e.target).push(e);
  });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmt(n) { return Number(n).toLocaleString("en-US"); }

  function node(id) { return nodesById.get(id); }

  function membersOf(fileId) {
    // functions and classes contained by a file node
    return (edgesFrom.get(fileId) || [])
      .filter(function (e) { return e.type === "contains"; })
      .map(function (e) { return nodesById.get(e.target); })
      .filter(Boolean)
      .filter(function (n) { return n.type === "function" || n.type === "class"; });
  }

  function importsOf(fileId) {
    return (edgesFrom.get(fileId) || [])
      .filter(function (e) { return e.type === "imports"; })
      .map(function (e) { return nodesById.get(e.target); })
      .filter(Boolean);
  }

  function importedBy(fileId) {
    return (edgesTo.get(fileId) || [])
      .filter(function (e) { return e.type === "imports"; })
      .map(function (e) { return nodesById.get(e.source); })
      .filter(Boolean);
  }

  // ---------- auto-renderers ----------

  // <element data-stat="nodes|edges|files|layers|functions|classes|tour">
  function renderStats() {
    var files = G.nodes.filter(function (n) {
      return n.type === "file" || n.type === "config" || n.type === "document";
    }).length;
    var vals = {
      nodes: G.nodes.length,
      edges: G.edges.length,
      files: files,
      layers: G.layers.length,
      functions: G.nodes.filter(function (n) { return n.type === "function"; }).length,
      classes: G.nodes.filter(function (n) { return n.type === "class"; }).length,
      tour: (G.tour || []).length
    };
    document.querySelectorAll("[data-stat]").forEach(function (el) {
      var k = el.getAttribute("data-stat");
      if (k in vals) el.textContent = fmt(vals[k]);
    });
  }

  // <element data-meta="commit|analyzed|description|name">
  function renderMeta() {
    var p = G.project || {};
    var vals = {
      commit: (p.gitCommitHash || "").slice(0, 8),
      analyzed: (p.analyzedAt || "").slice(0, 10),
      description: p.description || "",
      name: p.name || ""
    };
    document.querySelectorAll("[data-meta]").forEach(function (el) {
      var k = el.getAttribute("data-meta");
      if (k in vals) el.textContent = vals[k];
    });
  }

  // <div class="file-chips" data-files="id1,id2"> — path + real summary per node
  function renderFileChips() {
    document.querySelectorAll("[data-files]").forEach(function (el) {
      var ids = el.getAttribute("data-files").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      var html = ids.map(function (id) {
        var n = node(id);
        if (!n) return "";
        return '<div class="file-chip"><span class="path">' + esc(n.filePath) +
          '</span><span class="desc">' + esc(n.summary || "") + "</span></div>";
      }).join("");
      el.innerHTML = html;
    });
  }

  // <div class="dist" data-dist="node-types|edge-types|layer-files">
  function renderDists() {
    document.querySelectorAll("[data-dist]").forEach(function (el) {
      var kind = el.getAttribute("data-dist");
      var rows = [];
      if (kind === "node-types") {
        var labels = {
          "function": "Functions", "file": "Source files", "class": "Classes",
          "config": "Config files", "document": "Documents"
        };
        var counts = {};
        G.nodes.forEach(function (n) { counts[n.type] = (counts[n.type] || 0) + 1; });
        rows = Object.keys(counts).map(function (k) {
          return { label: labels[k] || k, value: counts[k] };
        });
      } else if (kind === "edge-types") {
        var elabels = {
          contains: "contains", exports: "exports", imports: "imports", calls: "calls",
          related: "related", documents: "documents", depends_on: "depends on",
          configures: "configures", implements: "implements"
        };
        var ecounts = {};
        G.edges.forEach(function (e) { ecounts[e.type] = (ecounts[e.type] || 0) + 1; });
        rows = Object.keys(ecounts).map(function (k) {
          return { label: elabels[k] || k, value: ecounts[k], mono: true };
        });
      } else if (kind === "layer-files") {
        rows = G.layers.map(function (l) {
          return { label: l.name, value: l.nodeIds.length, href: "layers.html#" + l.id.replace("layer:", "") };
        });
      }
      rows.sort(function (a, b) { return b.value - a.value; });
      var max = rows.reduce(function (m, r) { return Math.max(m, r.value); }, 1);
      el.innerHTML = rows.map(function (r) {
        var lbl = r.href
          ? '<a href="' + esc(r.href) + '">' + esc(r.label) + "</a>"
          : esc(r.label);
        if (r.mono) lbl = "<code>" + lbl + "</code>";
        return '<div class="dist-row"><span class="lbl">' + lbl + "</span>" +
          '<span class="bar-track"><span class="bar" style="width:' +
          (100 * r.value / max).toFixed(1) + '%"></span></span>' +
          '<span class="val">' + fmt(r.value) + "</span></div>";
      }).join("");
    });
  }

  function init() {
    renderStats();
    renderMeta();
    renderFileChips();
    renderDists();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return {
    graph: G,
    node: node,
    nodesById: nodesById,
    edgesFrom: edgesFrom,
    edgesTo: edgesTo,
    membersOf: membersOf,
    importsOf: importsOf,
    importedBy: importedBy,
    esc: esc,
    fmt: fmt
  };
})();
