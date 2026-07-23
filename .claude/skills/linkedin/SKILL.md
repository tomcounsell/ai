---
name: linkedin
description: "Use when reading posts or engaging on LinkedIn. Triggered by requests to comment on LinkedIn, browse the feed, write or interact with posts, check DMs, or read/reply to LinkedIn messages."
allowed-tools: mcp__byob__browser_list_tabs, mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_get_html, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_press_key, mcp__byob__browser_scroll, mcp__byob__browser_wait_for, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, mcp__byob__browser_switch_tab, Bash(git:*), Read, Write, Edit, Grep, Glob, Agent
user-invocable: true
---

# LinkedIn Activity

Drives the user's real, logged-in Chrome session via the **BYOB** stack
(`mcp__byob__browser_*`). No CDP flag, no `state.json`, no
headless-fingerprint detection.

## Default Behavior (no arguments)

Run all three tasks in order:

1. **Check messages** — reply to any DMs that need attention
2. **Write a post** — about recent work from git history
3. **Browse feed** — find and comment on 3 posts

If arguments are given, interpret them and do only what's asked.

**Nothing goes live — post, comment, or DM reply — without passing both gates in order: first `Skill('de-slop')` (fresh-context review of the draft file only — removes AI-writing tells, blocks hollow drafts), then `Skill('authenticity-pass')`; the full gate procedures are in `references/posting.md` and `references/feed-engagement.md`.**

**This skill executes — it does not pause for confirmation.** When the skill says "show the draft inline before publishing," that means render the draft in your response *and continue with the publish step in the same turn*. Don't stop to ask "should I post this?" — the user already opted in by invoking the skill, and they can interrupt mid-stream if they want changes. The only legitimate stop conditions are: (a) hard tool failure with no fallback (see the BYOB share-modal limitation in [references/posting.md](references/posting.md)), (b) a finding that contradicts the skill's premise (e.g. "no DMs need replying" → skip Task 1 cleanly with one sentence of why).

## Task guides (load on demand)

Each task's full procedure lives in a reference file. Load only what the current run needs:

| Load... | When... |
|---|---|
| [references/dom-model.md](references/dom-model.md) | Before the first `browser_read`/`browser_click` on any feed, post, or profile page — the `byob:idx` workflow and hard-won gotchas |
| [references/messages.md](references/messages.md) | Task 1: reading the DM inbox, researching contacts, drafting and sending replies, updating the knowledge base |
| [references/posting.md](references/posting.md) | Task 2: extracting a portable lesson from recent work, subagent drafting, persona cold-read loop, authenticity gate, publish (and the BYOB share-modal limitation) |
| [references/feed-engagement.md](references/feed-engagement.md) | Task 3: volume targets, liking, following, comment screening, comment drafting loop, posting workflow — plus editing/deleting posted comments |

## Prerequisites

This skill needs **two preconditions** plus a **scheduler-gate flag**:

### 1. BYOB extension installed and connected

BYOB is the "Bring Your Own Browser" stack: a Chrome extension + native
messaging host + MCP server that lets this skill act on the user's already
logged-in Chrome (no headless profile, no per-session re-auth). Set up via
`/setup`'s computer-use opt-in or by following
[`docs/features/byob-browser-control.md`](../../../docs/features/byob-browser-control.md).

Verify the install in one shot:

```bash
cd ~/.byob && bun run doctor
```

All status lines should be green: extension loaded, native bridge running,
Unix socket live. If any line is red, run `/setup` and answer "yes" to the
computer-use opt-in to repair.

After `bun run doctor` passes, sanity-check that the extension is actually
talking to Chrome:

```text
mcp__byob__browser_list_tabs    # returns the user's currently open Chrome tabs
```

**Do not proceed to LinkedIn work until `browser_list_tabs` returns at
least one tab.** An empty list or transport error means the extension isn't
bound to an active Chrome window — open or focus Chrome and retry. A silent
transport failure here means every subsequent BYOB call returns wrong-shaped
output and the skill drives nothing.

The user must be logged into LinkedIn in that Chrome session.

### 2. Real-Chrome scheduler gate (`requires_real_chrome=True`)

LinkedIn driving real Chrome must be **serialized** against any other
real-Chrome session — two concurrent BYOB sessions on the active tab
collide and corrupt each other's DOM. PR #1277 added the
`AgentSession.requires_real_chrome` field; the worker scheduler defers
any second real-Chrome candidate until the first finishes.

There are two paths that set the flag:

- **Bridge-spawned (Telegram or email)**: the bridge calls
  `agent.byob_skill_triggers.infer_requires_real_chrome(message_text)`
  before enqueue. Messages mentioning "linkedin" with first-person /
  intent phrasing (e.g. "check my LinkedIn DMs", "/linkedin") match a
  trigger and the flag is set automatically. No operator action required.
- **CLI-spawned**: launch with the explicit flag:

  ```bash
  valor-session create \
    --role eng \
    --project-key valor \
    --needs-real-chrome \
    --message "list my linkedin DMs"
  ```

  Always use `--needs-real-chrome` for any session that calls this skill.
  Without it, two real-Chrome sessions can race.

## Tab discovery (do this first)

Always start by listing tabs and reusing an existing LinkedIn tab. Opening duplicates clutters the user's window and breaks `tabId`-targeted reads.

```text
browser_list_tabs   →   look for any tab whose URL contains "linkedin.com"
```

Pick the first match → that's your `tabId` for every subsequent navigate / get_html / click / type call. If no LinkedIn tab exists, call `browser_navigate(url="https://www.linkedin.com/feed/")` once to open one and use the returned `tabId`.

## The two-surface DOM model (critical)

LinkedIn has **two different DOMs** that need different read tools:

| Surface | DOM style | Read with | Click with |
|---|---|---|---|
| **Messaging** (`/messaging/...`) | Stable, named classes (`.msg-conversations-container__*`, `.msg-conversation-card__*`) | `browser_get_html(tabId, selector=".msg-*")` | `browser_click(tabId, selector="<stable .msg-* selector>")` |
| **Feed / Post / Profile** (`/feed/`, `/posts/...`, `/in/...`) | Hashed/obfuscated classes (`._06ad2747`); BYOB injects `data-byob-idx="N"` on every element | `browser_read(url, reuseTab=true, screens=5)` → use `interactiveElements` | `browser_click(tabId, selector="byob:idx=N")` where N is from the most recent read |

**Why this matters:** `browser_read` returns near-empty content on `/messaging/` (LinkedIn renders that surface in a way the read pipeline can't see). `browser_get_html` works there because it pulls real DOM via tabId. Conversely, on the feed the hashed classes are deploy-volatile — the only stable handle is the BYOB-injected `data-byob-idx`, which you address as `selector: "byob:idx=N"`.

The step-by-step `byob:idx` workflow and the gotcha list (scroll behavior, `reuseTab`, IE caps, post-body reads, tool-result overflow) live in [references/dom-model.md](references/dom-model.md) — read it before the first feed/post/profile interaction.

## Notes

- **Always reuse the same `tabId`** across a session. Discover it once at the start and pass it to every call.
- **Wait 2-3s after navigation** for SPA hydration — `waitUntil="networkidle"` mostly handles this; add `browser_wait_for(selector, state="visible")` for specific elements.
- **Opening a message marks it as read** — be aware of "seen" indicators if you don't intend to actually engage.
- **Screenshots over 1MB fail** — use `format="jpeg"` and `quality=50-60` for confirmation captures.
- **No fallback browser surface.** BYOB is the only browser tool. If `mcp__byob__browser_*` calls return transport errors mid-session, run `cd ~/.byob && bun run doctor` in a fresh shell to repair, then retry the failed call.
