// The local chat page: polls /log, posts to /send, renders each text as
// Markdown (marked) sanitized by DOMPurify, with HTML in a text shown as text.
"use strict";
const token = location.hash.slice(1);
const list = document.getElementById("log");
const status = document.getElementById("status");
const replyLine = document.getElementById("reply");
const form = document.getElementById("form");
const box = document.getElementById("text");
const REOPEN = "Reopen this page with: python -m bridges.local open";
const shown = new Set();
let replyTo = null;
let chosen = null;
let stopped = false;

const escapeHtml = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[c]);
const md = new marked.Marked({
  gfm: true,
  breaks: true,
  async: false,
  renderer: { html: (token) => escapeHtml(token.text) },
});
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

function render(text) {
  return DOMPurify.sanitize(md.parse(text).trim(), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "style", "form", "input", "button", "textarea", "select"],
    FORBID_ATTR: ["style"],
    RETURN_DOM_FRAGMENT: true,
  });
}

function stop() {
  stopped = true;
  status.textContent = REOPEN;
  box.disabled = true;
}

function choose(li, row) {
  if (chosen) chosen.classList.remove("chosen");
  if (chosen === li) {
    chosen = null;
    replyTo = null;
    replyLine.textContent = "";
    return;
  }
  chosen = li;
  li.classList.add("chosen");
  replyTo = row.message_id;
  replyLine.textContent = "Replying to: " + row.text.slice(0, 80);
}

function add(row) {
  shown.add(row.event_id);
  const li = document.createElement("li");
  li.className = row.from;
  li.dataset.eventId = row.event_id;
  li.append(render(row.text));
  if (row.from === "valor")
    li.addEventListener("click", (event) => {
      if (!event.target.closest("a")) choose(li, row);
    });
  const after = [...list.children].find((c) => Number(c.dataset.eventId) > row.event_id);
  const atEnd = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
  list.insertBefore(li, after || null);
  if (atEnd) list.scrollTop = list.scrollHeight;
}

async function poll() {
  if (stopped) return;
  try {
    const r = await fetch("/log", { headers: { "X-Valor-Token": token } });
    if (r.status === 401) return stop();
    if (r.ok) {
      for (const row of (await r.json()).rows) if (!shown.has(row.event_id)) add(row);
    }
  } catch (e) {
    // The bridge is restarting: try again on the next poll.
  }
  setTimeout(poll, 2000);
}

async function send(message) {
  while (!stopped) {
    try {
      const r = await fetch("/send", {
        method: "POST",
        headers: { "X-Valor-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify(message),
      });
      if (r.status === 401) return stop();
      if (r.ok) return true;
    } catch (e) {
      // Not answered: the same id again, so it records once.
    }
    await new Promise((done) => setTimeout(done, 2000));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = box.value;
  if (!text.trim() || stopped) return;
  box.disabled = true;
  if (await send({ id: crypto.randomUUID(), text, reply_to: replyTo })) {
    box.value = "";
    if (chosen) choose(chosen, null);
  }
  if (!stopped) box.disabled = false;
});

box.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

if (token) poll();
else stop();
