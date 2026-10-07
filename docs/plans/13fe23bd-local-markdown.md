---
tracking: none
slug: 13fe23bd-local-markdown
type: plan
status: planned
critique_rounds: 1
review_rounds: 2
---

# The local chat page renders message bodies as Markdown

Feature, task 13fe23bd0686. Tom, 2026-10-07: "for visual purposes, use a
markdown to html renderer for the message bodies."

**Problem.** `bridges/local/chat.js` sets `li.textContent = row.text`, so
Valor's messages (written in Markdown) show as raw `#`, `-`, and backticks.
`docs/bridges/local.md` promises that a send's text never runs, and lists
"markdown rendering" under Gaps.

**Stakes.** The page is Tom's approval surface and holds the token, so a
message text that runs script could post `approve` as Tom; the renderer
must never let a row's text run.

## What is built

1. **Vendored renderer and sanitizer**, in `bridges/local/vendor/`:
   - `marked.umd.js` from `marked` (MIT), the latest 16.x at build time,
     pinned to that exact version;
   - `purify.min.js` from `dompurify` (Apache-2.0 OR MPL-2.0), the latest
     3.x at build time, pinned to that exact version;
   - each package's license file beside it (`marked.LICENSE`,
     `dompurify.LICENSE`), unmodified.
   Fetched with `npm pack <pkg>@<version>` into a fresh empty directory
   outside the repo, the tarball's integrity checked against the registry's
   `dist.integrity`, and only the two built files and the licenses copied
   in. No `package.json`, no `node_modules`, no build step in the repo.
   The exact versions, the npm `dist.integrity` of each tarball, and the
   sha256 of each vendored file are recorded in `docs/bridges/local.md`.
   No README in `vendor/` (a directory README would owe the Not-here
   paragraph; the doc is the record).

2. **Served by the bridge**, `bridges/local/__init__.py`: two explicit
   routes, `GET /vendor/marked.umd.js` and `GET /vendor/purify.min.js`,
   each reading its one file, `text/javascript`, like `script`. No static
   directory handler, so no path is resolved from the request. Served
   without the token, as `/chat.js` is.

3. **Content-Security-Policy** on `GET /`: `frame-ancestors 'none';
   script-src 'self'`. The page has no inline script or handler, so
   nothing it uses is blocked; an injected inline script or `on*` handler
   that got past the sanitizer would be refused by the browser too. This
   is the header the Brief allows, not a new check: no request is refused
   by it and no step is added.

4. **`chat.html`**: `<script src="/vendor/marked.umd.js">` and
   `<script src="/vendor/purify.min.js">` before `/chat.js`. Styles:
   - `#log li` loses `white-space: pre-wrap` (the rendered HTML carries
     its own breaks); `pre` keeps `white-space: pre` with `overflow-x:
     auto`.
   - Plain, readable rules scoped to `#log li`: first/last child margins
     zeroed so a one-paragraph row looks as it does today; headings at
     modest sizes (1.25em, 1.1em, 1em) with small margins; `ul`/`ol` with
     a 1.4em left padding; inline `code` and `pre` in a monospace face on
     a faint background with a 4px radius; `table` collapsed with 1px
     `#d1d5db` cell borders and 4px 8px padding, wrapped to scroll
     horizontally when wide; `blockquote` a left rule in grey; links in
     the page's blue.

5. **`chat.js`**, one function `render(text)` used by `add`:
   - `marked.parse(text, {gfm: true, breaks: true, async: false})`, with
     a renderer whose `html` hook returns the raw HTML escaped, so HTML
     typed in a message is shown as text, as today, and only Markdown
     becomes markup.
   - The result trimmed, then `DOMPurify.sanitize(html,
     {RETURN_DOM_FRAGMENT: true, FORBID_TAGS: ["img", "style", "form",
     "input", "button", "textarea", "select"], FORBID_ATTR: ["style"]})`,
     and the fragment appended to the `li`. The fragment is appended as
     nodes, never assigned through `innerHTML`, so the sanitized tree is
     not re-parsed. Images are dropped: the page shows text only, as
     `docs/bridges/local.md` says for files, and a remote image would be
     a fetch made by reading.
   - Links get `rel="noopener noreferrer"` and `target="_blank"` through
     one `DOMPurify.addHook("afterSanitizeAttributes", ...)`, so a link
     opens outside the page and the page is not handed to it. DOMPurify's
     default URI allow-list removes `javascript:` and other script URLs.
   - A click on a link inside one of Valor's rows does not toggle the
     reply choice (the row's click handler ignores clicks whose target is
     inside an `a`).
   - The "Replying to:" line stays `textContent` of the raw text.
   - The file's header comment changes from "renders with textContent
     only" to what it does now.

6. **`docs/bridges/local.md`**:
   - The page section: rows render as Markdown (GFM, line breaks kept)
     through marked, then DOMPurify, appended as a fragment; raw HTML in
     a text shows as text; images are dropped; `javascript:` links are
     removed; links open in a new tab. The promise "a send's text never
     runs" stays, now with these reasons.
   - Routes: the two vendor routes, and the CSP value.
   - Threat model: a bullet, "A message's text", naming the sanitizer,
     the escaped raw HTML, and `script-src 'self'` as the second layer.
   - A short "Vendored code" subsection: package, exact version, license,
     tarball integrity, file sha256, and how to update (the same
     `npm pack` steps, the doc's values changed in the same commit).
   - Gaps: "markdown rendering" removed.
   - The implementation table: `vendor/`.

## Tests

Browser tests go in `tests/test_local_page.py` on its existing headless
Chromium harness (`page_up`, `Tab`); rows are put on the ledger with
`notices.request`, as `test_a_401_stops_the_polling_and_the_sending` does,
so each text arrives as one of Valor's rows through the real poll.

- **`test_a_hostile_row_renders_with_nothing_live`**: one notice per
  text, each set to write `window.pwned` if it ever ran:
  - `<script>window.pwned=1</script>`;
  - `<img src=x onerror="window.pwned=1">`;
  - `[click me](javascript:window.pwned=1)`;
  - `<a href="javascript:window.pwned=1">raw</a>`;
  - `<svg onload="window.pwned=1"></svg>`;
  - `![x](x" onerror="window.pwned=1)` (an image whose title tries to
    carry a handler).
  After all rows show, assert: no `script`, `img`, `svg`, or `iframe`
  under `#log`; no element under `#log` with any `on*` attribute; no
  `a[href]` under `#log` whose `href` starts with `javascript:`
  (case-insensitive, whitespace-trimmed); the Markdown link's anchor is
  clicked and `window.pwned` is still `undefined`, then asserted again
  after a 1s wait so a deferred `onerror` would have fired. The raw-HTML
  rows' `textContent` equals their text, so they show as typed.
- **`test_markdown_renders`**: one notice holding a heading, a bulleted
  list, a numbered list, `**bold**`, inline code, a fenced code block,
  and a two-column GFM table. Assert inside that row: an `h1` with the
  heading's text; a `ul` with two `li`; an `ol` with two `li`; a
  `strong`; a `code` not inside `pre`; a `pre > code` holding the fence
  body with its newlines; a `table` with a `th` per column. A link in it
  has `target="_blank"` and a `rel` holding `noopener`.
- **Tom's rows render too**: in the same test, the page sends
  `**from tom**`, and its `li.tom` holds a `strong`.
- **A click on a link does not choose the row**: clicking the link in
  Valor's Markdown row leaves no `li.chosen`.
- **Existing assertions hold unchanged**: `test_the_page_sends_...`
  compares `textContent` exactly (`'hello from the page'`, the
  `<img ... onerror>` text, `'after the 401'`); trimming the output and
  escaping raw HTML keep those exact. These tests are run, not edited.

Route tests, no browser:

- `tests/test_local_edges.py`,
  `test_the_page_and_its_script_are_served_without_the_token`: extended
  to fetch both vendor routes (200, `javascript` content type, bytes
  equal the files in `bridges/local/vendor/`), and to assert the page
  includes both `<script src="/vendor/...">` tags before `/chat.js`.
  `GET /vendor/../__init__.py` and `GET /vendor/other.js` are 404.
- `tests/test_local_bridge.py`: the framing assertion becomes
  `"frame-ancestors 'none'; script-src 'self'"`.

Suites run: `tests/test_local_page.py`, `tests/test_local_edges.py`,
`tests/test_local_bridge.py`, then the whole suite, with any failure
compared against the base commit to separate pre-existing ones.

## Out of scope

- Files and images in either direction (still a Gap).
- Rendering in the "Replying to:" line, in Telegram, or in email.
- Syntax highlighting, Markdown in the input box, a preview.
- Any check, gate, hook, or validator; no lint or CI step for the vendored
  files.

## Decisions Tom may want to change

- Raw HTML typed in a message is shown as text, not rendered (even
  sanitized). The other reading renders safe HTML such as `<b>`; it would
  also make the existing test's `<img onerror>` text disappear instead of
  showing.
- Images are dropped rather than shown.
- Tom's own rows render as Markdown too, since the request says "message
  bodies".
- Single newlines become line breaks (`breaks: true`), matching how the
  rows read today with `pre-wrap`.
