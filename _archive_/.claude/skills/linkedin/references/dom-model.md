# LinkedIn `byob:idx` Workflow and DOM Gotchas

Companion to the two-surface DOM model table in `SKILL.md`. Read this before the
first `browser_read` / `browser_click` on any feed, post, or profile page.

## The `byob:idx` workflow

1. `browser_read(url, reuseTab=true, screens=5)` returns `interactiveElements: [{idx, tag, role, name, bounds}, ...]` — up to 1000 per call. The `name` is the accessible label ("Like", "Comment", "Sort by: Top", "Start a post").
2. Find the element you want by its `name` in that list.
3. Click it with `browser_click(tabId, selector="byob:idx=<that idx>")`.
4. **The next `browser_read` invalidates older indices** — its `interactiveSessionTag` changes. After every click that mutates the DOM (sending a comment, opening a thread, expanding a menu), re-read before the next click.

When multiple `interactiveElements` map to the same logical control (the Like button shows up as `div role=button` + `p role=button` + `span role=button`), prefer the entry whose `tag: "button"` — that's the outermost real button. If none has `tag: "button"`, any of them clicks fine.

## Gotchas to remember

- **`browser_navigate` accepts `tabId`** to reuse an existing tab. **`browser_read` does NOT accept `tabId`** — pass `reuseTab: true` along with the same URL the tab is already on. Without `reuseTab` you'll spawn a duplicate.
- **`?sortBy=RECENT` is dropped** by LinkedIn on direct navigation. You land on the default Top feed. To switch, click the "Sort by: Top" element and pick "Recent" from the dropdown that opens (the dropdown lives in a portal that `browser_read` can't see — re-read after clicking and look for "Recent" in the new IE list, or just work the default Top feed).
- **`browser_scroll` with `y: <number>` or `to: "bottom"` does nothing on the feed** — LinkedIn scrolls an inner container, not the window. The returned `scrollY` will be `0` regardless. Use `browser_scroll(tabId, text: "<unique substring>")` or `selector: "byob:idx=N"` to bring a specific element into view; ignore the `scrollY` field. For bulk feed loading, just bump `browser_read`'s `screens` parameter — it auto-scrolls and is the right tool.
- **Feed reads cap at 1000 IEs.** When the read returns `stopReason: "limit_reached"` and `canContinue: true`, you've only seen the first slice. Process those, then call `browser_read` again to advance.
- **Post bodies often don't appear in `interactiveElements`** because they're non-interactive `<div>`s. The IE list captures author headers, action buttons, and accessibility labels — not the post text itself. To read the actual post body, use `browser_get_html(tabId, selector="main")` and parse text out, or open the post URL directly (`/feed/update/urn:li:share:<id>/`) and use `browser_read` on the dedicated post page where the body usually surfaces in chunks.
- **Tool-result file overflow:** both `browser_read` and `browser_get_html` on rich pages routinely exceed the inline tool-result limit and dump to `tool-results/*.txt`. Be ready to parse those out via Bash/grep/jq. Set realistic `maxBytes` and `screens` defaults to keep the inline path viable when you can.
- **Block list:** BYOB upstream blocks reading `chrome://`, `file://`, and login pages for Google/Microsoft/Apple. Not relevant for in-session LinkedIn use.
