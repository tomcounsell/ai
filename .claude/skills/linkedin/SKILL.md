---
name: linkedin
description: "Use when browsing LinkedIn, reading posts, writing comments, checking DMs, or engaging with content. Triggered by requests to comment on LinkedIn, browse feed, interact with posts, or read/reply to LinkedIn messages."
allowed-tools: mcp__byob__browser_list_tabs, mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_get_html, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_press_key, mcp__byob__browser_scroll, mcp__byob__browser_wait_for, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab, mcp__byob__browser_switch_tab, Bash(agent-browser:*), Bash(git:*), Read, Write, Edit, Grep, Glob, Agent
user-invocable: true
---

# LinkedIn Activity

**Migrated from `agent-browser` in #1274.** This skill drives the user's real,
logged-in Chrome session via the **BYOB** stack — the Chrome extension + native
messaging host + MCP server (`mcp__byob__browser_*`). No CDP flag, no
`state.json`, no headless-fingerprint detection. The fallback CDP-attach path
is still documented below for machines where BYOB is not yet installed; it
will be removed in a followup once BYOB is verified working everywhere.

## Default Behavior (no arguments)

Run all three tasks in order:

1. **Check messages** — reply to any DMs that need attention
2. **Write a post** — about recent work from git history
3. **Browse feed** — find and comment on 3 posts

If arguments are given, interpret them and do only what's asked.

**This skill executes — it does not pause for confirmation.** When the skill says "show the draft inline before publishing," that means render the draft in your response *and continue with the publish step in the same turn*. Don't stop to ask "should I post this?" — the user already opted in by invoking the skill, and they can interrupt mid-stream if they want changes. The only legitimate stop conditions are: (a) hard tool failure with no fallback (see the BYOB share-modal limitation under Task 2), (b) a finding that contradicts the skill's premise (e.g. "no DMs need replying" → skip Task 1 cleanly with one sentence of why).

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
Unix socket live. If any line is red, the BYOB MCP tools below will return
a transport error -- run `/setup` and answer "yes" to the computer-use
opt-in to repair.

After `bun run doctor` passes, sanity-check from inside the agent that
the extension is actually talking to Chrome by listing open tabs:

```text
mcp__byob__browser_list_tabs    # returns the user's currently open Chrome tabs
```

If `browser_list_tabs` returns an empty list or a transport error, the
extension loaded but isn't bound to an active Chrome window -- open Chrome
(or focus it) and retry. **Do not proceed to LinkedIn work until
`browser_list_tabs` returns at least one tab.** A silent transport failure
here means every subsequent BYOB call returns wrong-shaped output and the
skill drives nothing.

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
    --role dev \
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

### The `byob:idx` workflow

1. `browser_read(url, reuseTab=true, screens=5)` returns `interactiveElements: [{idx, tag, role, name, bounds}, ...]` — up to 1000 per call. The `name` is the accessible label ("Like", "Comment", "Sort by: Top", "Start a post").
2. Find the element you want by its `name` in that list.
3. Click it with `browser_click(tabId, selector="byob:idx=<that idx>")`.
4. **The next `browser_read` invalidates older indices** — its `interactiveSessionTag` changes. After every click that mutates the DOM (sending a comment, opening a thread, expanding a menu), re-read before the next click.

When multiple `interactiveElements` map to the same logical control (the Like button shows up as `div role=button` + `p role=button` + `span role=button`), prefer the entry whose `tag: "button"` — that's the outermost real button. If none has `tag: "button"`, any of them clicks fine.

### Gotchas to remember

- **`browser_navigate` accepts `tabId`** to reuse an existing tab. **`browser_read` does NOT accept `tabId`** — pass `reuseTab: true` along with the same URL the tab is already on. Without `reuseTab` you'll spawn a duplicate.
- **`?sortBy=RECENT` is dropped** by LinkedIn on direct navigation. You land on the default Top feed. To switch, click the "Sort by: Top" element and pick "Recent" from the dropdown that opens (the dropdown lives in a portal that `browser_read` can't see — re-read after clicking and look for "Recent" in the new IE list, or just work the default Top feed).
- **`browser_scroll` with `y: <number>` or `to: "bottom"` does nothing on the feed** — LinkedIn scrolls an inner container, not the window. The returned `scrollY` will be `0` regardless. Use `browser_scroll(tabId, text: "<unique substring>")` or `selector: "byob:idx=N"` to bring a specific element into view; ignore the `scrollY` field. For bulk feed loading, just bump `browser_read`'s `screens` parameter — it auto-scrolls and is the right tool.
- **Feed reads cap at 1000 IEs.** When the read returns `stopReason: "limit_reached"` and `canContinue: true`, you've only seen the first slice. Process those, then call `browser_read` again to advance.
- **Post bodies often don't appear in `interactiveElements`** because they're non-interactive `<div>`s. The IE list captures author headers, action buttons, and accessibility labels — not the post text itself. To read the actual post body, use `browser_get_html(tabId, selector="main")` and parse text out, or open the post URL directly (`/feed/update/urn:li:share:<id>/`) and use `browser_read` on the dedicated post page where the body usually surfaces in chunks.
- **Tool-result file overflow:** both `browser_read` and `browser_get_html` on rich pages routinely exceed the inline tool-result limit and dump to `tool-results/*.txt`. Be ready to parse those out via Bash/grep/jq. Set realistic `maxBytes` and `screens` defaults to keep the inline path viable when you can.
- **Block list:** BYOB upstream blocks reading `chrome://`, `file://`, and login pages for Google/Microsoft/Apple. Not relevant for in-session LinkedIn use.

### Fallback path — deprecated

The recipe below pre-dates BYOB and is kept for one release cycle so
operators on machines without BYOB still have a working LinkedIn
skill. **Do not use this path if BYOB is installed.** It will be
removed in a followup issue once BYOB is verified working on every
operator machine.

```bash
# DEPRECATED — use BYOB above. CDP-attach hack:
pkill -f "Google Chrome" && sleep 2
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile &>/dev/null &
sleep 5
agent-browser connect 9222
```

When using the fallback, every `mcp__byob__browser_<verb>` call below maps to
`agent-browser <verb>`, with the same arguments and roughly equivalent
behavior (anonymous CDP-attached profile rather than the user's real
Chrome). The fallback path requires the user to manually relaunch Chrome
with debug flags before each session — which is exactly the friction
BYOB removes.

---

## Task 1: Check Messages

### Voice for DMs

Write as Valor Engels: direct, concise, genuine. DMs are conversational — not email.

- **Short messages** — 1-3 sentences max unless depth is warranted
- **Curious, not eager** — ask about their work, don't pitch ours
- **No sycophancy** — no "so great to hear from you!", no performative enthusiasm
- **No assumptions** — they might want to hire, collaborate, sell, or just say hi
- **Warm but professional** — friendly peer, not a salesperson

### Read the inbox

Navigate the existing tab, then pull the conversation list HTML directly (the messaging surface is stable-class territory):

```text
browser_navigate(url="https://www.linkedin.com/messaging/", tabId=<linkedin_tab>, waitUntil="networkidle")
browser_get_html(tabId=<linkedin_tab>, selector=".msg-conversations-container__conversations-list", maxBytes=32768)
```

The HTML returned has one `<li class="...msg-conversation-listitem...">` per conversation. From each list item you can read:

- `.msg-conversation-card__participant-names` → who it's with
- `.msg-conversation-card__message-snippet` → preview text (often starts with `You:` if Valor was last sender)
- `.msg-conversation-card__pill` → badges like "Sponsored" (skip these)
- `.msg-conversation-listitem__time-stamp` → recency

**Default skip rules** (apply before opening any conversation):
- Sponsored ads → skip
- Snippet starts with `You:` AND the timestamp is < 4 weeks old → skip (you're already waiting on them; following up reads as needy)
- Obvious recruiter templates → skip
- If after these filters the inbox is empty: state "no DMs need replies right now" with a one-line reason and move to Task 2. Don't open conversations just to confirm.

For each remaining conversation worth attention, open it:

```text
browser_click(tabId=<linkedin_tab>, selector="li.msg-conversation-listitem:nth-of-type(<N>) .msg-conversation-listitem__link")
browser_wait_for(tabId=<linkedin_tab>, selector=".msg-s-message-list-content", state="visible", timeoutSec=5)
browser_get_html(tabId=<linkedin_tab>, selector=".msg-s-message-list-content", maxBytes=8192)
```

Understand: who is this person, what did they say, is this new/ongoing/cold outreach? Spam and recruiter templates don't need replies.

### Research before replying (mandatory)

```bash
ls ~/work-vault/Consulting/leads/
ls ~/work-vault/Consulting/chats/
```

- **Known lead** (in `leads/`): Read their file. Reply with awareness of what they need.
- **Known chat** (in `chats/`): Read their file. Reply conversationally.
- **Unknown person**: Quick profile scan. Default to friendly and curious.
- **Spam/automated**: Skip.

### Draft and send

Write draft to `/tmp/linkedin-reply.txt`.

**Message style by context:**
- **Greeting**: Respond warmly, ask what they're working on
- **Question about our work**: Answer directly, link to code/docs if relevant
- **Business inquiry**: Ask what they're trying to solve — don't pitch
- **Cold outreach/sales**: Polite one-sentence decline or redirect

Quality check: Is it short enough? Does it invite a response without being needy? Would Valor actually say this?

Then send it. The message input is a contenteditable, not an `<input>`:

```text
browser_click(tabId=<linkedin_tab>, selector=".msg-form__contenteditable")
browser_type(tabId=<linkedin_tab>, selector=".msg-form__contenteditable", text="<reply text>", clear=true)
browser_click(tabId=<linkedin_tab>, selector=".msg-form__send-button")
browser_wait_for(tabId=<linkedin_tab>, selector=".msg-form__contenteditable[aria-label*='empty']", state="visible", timeoutSec=5)
```

If a selector ever returns `selector_not_found`, dump a fresh `browser_get_html(selector=".msg-form")` and read what the current class names are — the stable thing here is the `msg-form__` prefix, not specific suffixes.

### Update knowledge base

- **Confirmed lead**: Update `~/work-vault/Consulting/leads/{name}.md`
- **New professional contact**: Create `~/work-vault/Consulting/chats/{name}.md`
- **Casual/one-off**: No file needed

---

## Task 2: Write a Post

### Audience model

LinkedIn is broad: PMs, designers, executives, salespeople, students, recruiters. Most readers will never see the codebase, and most aren't engineers. **Write for the smart professional in a different field, not the peer who builds the same thing.** A successful post lets a marketer, lawyer, or product manager close the tab feeling they learned something useful for their own work.

If the only audience that can decode the post is "engineers who build the same thing I do," it's a blog post, not LinkedIn. Save it for the repo's docs.

### Research

```bash
git log --oneline --since="5 days ago"
```

Read the relevant files and feature docs. But don't stop at "what changed" — keep going until you can answer:

**What general lesson did this work teach me, that anyone could use?**

That lesson is the post. The codebase work is one concrete example of the lesson, not the subject of the post.

**Only post if there's a portable lesson.** Skip routine bug fixes, formatting, dependency bumps, and any insight whose only audience is engineers in the same niche.

### Draft (delegate to a fresh subagent)

**Drafting happens in a subagent, not the parent session.** The parent session is loaded with engineering context — code excerpts, file paths, internal jargon — and that context bleeds into any post written inline. The post turns into an internal memo. A subagent starts cold and only knows what its brief tells it.

The parent's job here is to assemble a brief and hand it off. Keep the candidate technical work in your hands; pass only what the subagent needs to extract the lesson.

**Spawn the subagent with `general-purpose`** and the prompt template below. Fill in the source material; everything else is fixed.

```
You are drafting a LinkedIn post on behalf of Valor Engels (software engineer
at Yudame). The audience is the broad LinkedIn feed: PMs, designers,
executives, salespeople, students, recruiters. Most readers will never see
the codebase, and most aren't engineers.

THE NON-NEGOTIABLE RULE: write so a marketing director, a lawyer, or a
product manager — someone smart with no engineering background — finishes
the post and feels they learned something useful for their own work. If
only engineers in the same niche can decode it, you've failed the brief.

## Source material

<<<
[Paste here: relevant commit hashes, the feature doc path(s), and a 2-3
sentence factual summary of what was built and why. Nothing more. Do NOT
paraphrase the technical detail in the brief — let the subagent read the
files and extract the lesson itself.]
>>>

Read those files yourself before drafting.

## The hard step: extract the portable lesson

Before drafting, write down explicitly: what general lesson did this work
teach, that anyone could use?

The lesson must be portable. It should make sense outside this codebase,
outside engineering, ideally outside tech entirely. If your candidate
lesson is something like "deterministic call sites should be cached," try
again — that's a tactic, not a lesson. Keep distilling until you have a
sentence like "Before optimizing for speed, audit what you're assuming is
holding still" — something a contracts team reusing past clauses or a
designer reusing past templates would also nod at.

The lesson IS the post. The codebase work is one concrete instance of the
lesson, not the subject.

## Structure

1. Open with the lesson. One sentence, plain language. Not project context,
   not "we just shipped X." The lesson itself.
2. Set the stage. One or two sentences naming the kind of situation where
   this lesson shows up. Use everyday framing.
3. One concrete example — drawn from the codebase, stripped to the smallest
   amount of jargon needed to make the point. If you must use a term like
   "cache" or "model," explain it in passing or replace with a plain
   analogy. Specifics earn their place by making the lesson vivid; they do
   not carry the post.
4. Land on a portable takeaway. A closing sentence the reader can apply to
   their own field. Must make sense to someone who never reads this
   codebase.

## Style

- Write like a teacher to a curious adult learner. Generous, plainspoken,
  specific.
- No abbreviations or in-jargon without translation. Terms like `os.replace`,
  `asyncio.Lock`, `MODEL_EXPERIMENT`, `sha256`, `LRU`, `RAG`, `MCP`,
  `Popoto`, `pull request`, `pytest`, `CI`, file paths, function names —
  none of those belong in a LinkedIn post. ("Pull request" in particular:
  most non-engineers read it as "asking for something" — translate to
  "a proposed change" or just "a change.") If you find yourself reaching
  for any of these, the lesson hasn't been translated yet.
- No listicle bullets unless the content is naturally a list.
- No "we just shipped" / "I just built" framing — that's an announcement.
- No performative humility, no chest-thumping.

## Length and closing

- ~800 characters as default. Go longer only if the lesson needs the room.
- End with `github.com/tomcounsell/ai` and 3-5 hashtags from this set:
  #AIAgents #AgenticAI #ClaudeAI #OpenSource #DeveloperTools #LLMs
  #MachineLearning #SoftwareEngineering

## Mandatory self-review before returning

1. Casual-reader test. Read your draft pretending you're a marketing
   director who has never written code. Do you finish with something
   useful for your own work? Do you bounce off jargon by sentence two?
   If the post only makes sense to an AI engineer, the lesson hasn't been
   extracted yet — start over from the lesson step.
2. Substance. Cut anything that reads like an announcement or a feature
   changelog. The lesson is at the front; the example earns its place by
   making the lesson vivid.

## Output

Write the final draft to `/tmp/linkedin-post.txt` and return the full
draft text in your reply, plus one line stating the portable lesson you
extracted.
```

When the subagent returns, the parent session:
1. Reads `/tmp/linkedin-post.txt`
2. Sanity-checks against the casual-reader test one more time (jargon creep, announcement framing, missing portable takeaway)
3. Renders the final post inline in the response
4. **In the same turn**, attempts to publish (see ⚠️ below)

If the draft fails the casual-reader test, send the subagent back with specific feedback (e.g. "sentence two still uses 'API call' — translate that") rather than rewriting it inline. The whole point of the subagent is to keep the engineering context out of the draft; rewriting inline reintroduces it.

### Publish

> **⚠️ KNOWN LIMITATION (verified live 2026-05-05, see PR #1286 / issue #1274):** BYOB **cannot drive LinkedIn's "Start a post" composer modal.** Clicking the "Start a post" trigger opens the modal visually (confirmed via screenshot showing "What do you want to talk about?"), but the modal's contenteditable textbox renders inside a **React portal** that `browser_read`, `browser_get_html`, and `browser_wait_for` cannot traverse. With no selector to target, `browser_type` has nothing to type into. `browser_press_key` does dispatch into the focused textbox, but it's single-key-per-call — typing 1500 chars one at a time is impractical (1500 round-trips). This is a general BYOB gap (every React-portal-rendered modal on every site likely has the same problem), not a LinkedIn-specific one — worth filing upstream.
>
> **What to do until BYOB resolves this:** at the point you'd publish, render the final draft inline AND state plainly: *"BYOB can't drive the post composer modal yet (React portal — see SKILL.md KNOWN LIMITATION) — paste this from `/tmp/linkedin-post.txt` into LinkedIn yourself, or send via the mobile app where the share flow is different."* Then proceed to Task 3 (which works fine — comments use an inline textbox, not a portal modal).
>
> Things that have been verified NOT to work for the share modal:
> - `browser_click("byob:idx=...")` on every "Start a post" IE variant (`idx=137..141`, `tag` "div"/"p", with and without `force:true`) — modal opens but textbox not in document
> - `browser_get_html("body")` after open (returns navigation chrome only — modal lives elsewhere)
> - `browser_get_html("[contenteditable=true]")`, `[role='dialog']`, `.share-creation-state`, `.ql-editor` (all `selector_not_found`)
> - `browser_wait_for(...)` for any of those selectors (always times out)
> - Hashed React class selectors are deploy-volatile and not worth chasing
>
> If/when BYOB gains portal traversal, the intended flow is: navigate → read → click "Start a post" idx → re-read for editor textbox idx → `browser_type` the post text → re-read for "Post" submit idx → click → screenshot for confirmation.

---

## Task 3: Browse Feed and Comment

### Read the feed

```text
browser_navigate(url="https://www.linkedin.com/feed/", tabId=<linkedin_tab>, waitUntil="networkidle")
browser_read(url="https://www.linkedin.com/feed/", reuseTab=true, screens=5)
```

`screens=5` lets BYOB auto-scroll five viewport heights to load lazy posts. Bump to 10+ if you need more. If the read returns `stopReason: "limit_reached"`, call `browser_read` again to get the next slice (the IE indices reset; `interactiveSessionTag` changes).

### React as you scroll

As you read through posts, Like any that are relevant to the work — agentic systems, memory, RAG, async architecture, developer tooling, AI in production. This trains the feed toward more of the same. A thumbs-up takes one click and doesn't require a comment.

**Commenting always implies a Like.** If a post passes the screening gate and you draft a comment for it, also like the post. Engagement should be coherent — a comment without the like reads as half-engaged. The reverse isn't true: plenty of posts are worth a like but not a comment.

In the IE list, Like buttons appear with `name: "Reaction button state: no reactionLike"` (or `name: "Like"`). After clicking, the same button's name flips to `"Reaction button state: Like"` — that's the reliable success indicator (don't gate on reaction-count diffs, those can lag). If the name already shows `"Reaction button state: Like"` or `"Unreact Like"`, the post is already liked — skip.

```text
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<like_idx>")
```

### Screen candidates (mandatory before drafting)

For each candidate, grep the codebase for the post's core topic:

```
Check: tools/, bridge/, agent/, docs/features/, config/, .claude/skills/
```

**Before checking the codebase, classify the post cold:** Ignore the browsing session so far. Ask — what domain is this post actually in? Who is the intended audience? What would a relevant reply look like for *them*? Write that classification in one sentence before proceeding.

Then ask: does this codebase operate in that domain?

A post passes if:
- The post's domain is one we work in directly (agentic systems, memory, async pipelines, LLM tooling, etc.)
- There's a specific file, pattern, or decision in this codebase that speaks to the post's actual claim
- The comment would make sense to the post's audience without any context about Claude or AI agents

A loose analogy ("this reminds me of a different problem we had") is not a pass. Keyword overlap without domain overlap is not a pass.

Target: 3 posts that pass this gate. If the feed only yields 1-2 quality candidates, post fewer good comments rather than padding with weak ones.

### Read the full post body before drafting

The feed inline rarely shows the full post body in `browser_read`'s text or IE list. Always open the post URL directly before drafting:

```text
browser_navigate(url="https://www.linkedin.com/feed/update/urn:li:share:<id>/", tabId=<linkedin_tab>, waitUntil="networkidle")
```

Then either:
- `browser_read(url=..., reuseTab=true, screens=2)` — works on the dedicated post page (text usually surfaces in chunks), OR
- `browser_get_html(tabId, selector="main")` and grep visible text out

**Why this matters:** A live test once drafted a comment from the feed snippet alone and missed the post's actual punchline (the joke was buried below the visible fold). The author was making a satire about ChatGPT's sycophant tic; the draft had earnestly engaged with a parenthetical they'd thrown in. Always read the full body before extracting the audience and the insight.

### Audience model for comments

**The post sets the audience, not your codebase.** A strategy post's readers are PMs and execs — write for them. A design-system post's readers are designers — write for them. Only when an AI-engineering post is in front of a self-selected technical audience can you reach for technical framing, and even then with restraint.

Common failure mode: the parent session has just spent 20 minutes inside engineering files and writes a comment that mirrors that context — file paths, function names, internal terms — onto a post whose audience can't decode any of it. The codebase grounding is for *you*, not for the comment text. File paths and function names belong in the drafter's notes, never in the published reply.

### For each post: read, gather, delegate, verify

**Gather (parent session):** Inside the parent, do the codebase research and extract:
- The post's audience (one sentence — strategist? designer? AI engineer?)
- The portable insight you'd bring (one sentence in plain language — no jargon, no file paths)
- Two or three lines of plain-language experience that ground the insight (what we've seen, in everyday terms — "we run a system that decides which AI requests to skip when the inputs haven't changed" beats "JSON cache for deterministic call sites")

Pass these to a fresh subagent. The subagent does NOT see the codebase. It only sees what you tell it.

**Delegate to subagent (`general-purpose`)** with this prompt template:

```
You are drafting a LinkedIn comment on behalf of Valor Engels (software
engineer at Yudame). The audience is set by the original post — not by
Valor's codebase. Your job is to write a comment the post's audience will
find genuinely useful.

## The post

<<<
[Paste the full post text here.]
>>>

## The post's audience

<<<
[One sentence: who reads this post? Strategists? Designers? AI engineers?
Recruiters?]
>>>

## What to bring

Portable insight (the parent already extracted this for you):
<<<
[One sentence in plain language. The parent did the engineering research;
you just need to say it well for the audience.]
>>>

Plain-language experience that grounds it:
<<<
[2-3 lines, jargon-free. What we've seen. No file paths, no function
names, no internal terms.]
>>>

## How to write the comment

1. Lead with the portable insight, in language the post's audience uses.
   Comments are read top-down with attention falling fast — the insight
   goes in sentence one.
2. Add the experience as supporting evidence, in 1-2 sentences.
3. Optionally close with a question or sharpened line that invites
   thought, not a sign-off.

## Hard rules

- Match the post's audience, not Valor's codebase. If the post is in plain
  English, the comment is in plain English.
- No file paths (`config/models.py`, `MODEL_EXPERIMENT`), no function
  names, no internal terms (`Popoto`, `MCP`, `RAG`, `LLM call sites`),
  no Python identifiers. These are bragging tokens, not contributions.
  If a term you reach for would not appear in the post itself, don't put
  it in the comment.
- No sycophantic opener ("Great point!", "Love this!").
- No fake authority. The grounding is the experience the parent gave
  you, not invented detail.

## Length

Default ~200-300 characters. Go up to ~600 only if the insight genuinely
needs the room. Hard cap 1250 (LinkedIn limit).

## Self-review before returning

1. Audience check. Read your draft as if you were the post's audience
   (a strategist for a strategy post, a designer for a design post). Does
   it land? Or does sentence one use a term they'd skim past?
2. Bragging-token check. Search your draft for any file path, function
   name, or internal-jargon term. If present, rewrite without them.
3. Insight-up-front check. Is the portable insight in sentence one? If
   it's the closing line, move it.

## Output

Return the final comment text. Nothing else.
```

**Verify (parent session):**
1. Read the returned draft against the audience-check, bragging-token, and insight-up-front rules
2. If it fails any of them, send the subagent back with specific feedback ("draft still uses 'cache' in sentence one — try 'we skip a request when the inputs haven't changed'")
3. Render the final comment inline in your response
4. **In the same turn**, save to `/tmp/linkedin-comment-N.txt` and post via BYOB

### Posting workflow (verified live)

```text
# Already on the post page from the read step. Like first (lower stakes).
browser_read(url="<post_url>", reuseTab=true, screens=2)
# Find the post-action-bar Like — name "Reaction button state: no reaction" with tag "button"
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<like_idx>")

# Type the comment into the inline textbox — name "Text editor for creating comment", role "textbox"
browser_type(tabId=<linkedin_tab>, selector="byob:idx=<editor_idx>", text="<comment text>")

# Re-read — typing into the textbox enables a NEW "Comment" submit button (button tag, distinct from the post-action-bar "Comment" that opens the composer)
browser_read(url="<post_url>", reuseTab=true, screens=1)
# The submit button is named "Comment" (button tag) and sits to the right of the textbox (high x-coordinate, e.g. ~844px); the post-action-bar "Comment" sits at ~565px and just opens the composer
browser_click(tabId=<linkedin_tab>, selector="byob:idx=<submit_idx>")

# Confirm — screenshot OR re-read and look for "Reaction button state: Like" on the post-level button + comment count incremented + your comment appearing with author "Valor Engels" and timestamp "now"
browser_screenshot(tabId=<linkedin_tab>, savePath="/tmp/linkedin-comment-confirmation.jpg", format="jpeg", quality=55)
```

**Distinguishing the two "Comment" IEs**: after typing, you'll see TWO entries with `name: "Comment"`:
- The post-action-bar Comment (lower idx, bounds x ≈ 565) — this OPENS the composer; clicking does nothing useful when composer is already open
- The submit Comment (higher idx, bounds x ≈ 844) — this PUBLISHES the comment

Pick the higher-x-coordinate one. After successful submit the textbox empties and your comment appears in the comment list with timestamp "now".

---

## Editing Comments

**Never use Edit to post a correction.** Editing replaces the full text — a "Correction: ..." opener reads as nonsense once the original is gone.

If a posted comment needs correcting:
- **Delete it** and post a clean replacement, or
- **Post a new reply** beneath it with the correction in context

Only use Edit to fix typos or rewrite the whole comment as a clean standalone.

---

## Notes

- **Always reuse the same `tabId`** across a session. Discover it once at the start and pass it to every call.
- **Re-read after every DOM-mutating click** — sending a comment, opening a thread, expanding a menu, opening the post composer. The `interactiveSessionTag` changes; old `byob:idx` values no longer point where you think.
- **`browser_get_html` for messaging, `browser_read` for feed.** They're not interchangeable on LinkedIn.
- **`scrollY` is meaningless on LinkedIn** — it's an inner scroll container. Don't gate decisions on the value `browser_scroll` returns.
- **Wait 2-3s after navigation** for SPA hydration — `waitUntil="networkidle"` mostly handles this; add `browser_wait_for(selector, state="visible")` for specific elements.
- **Opening a message marks it as read** — be aware of "seen" indicators if you don't intend to actually engage.
- **Screenshots over 1MB fail** — use `format="jpeg"` and `quality=50-60` for confirmation captures.
- **No Playwright fallback in BYOB.** If the bridge dies mid-session, the agent surfaces a clear error — don't try to reroute through `bowser` (different stack, anonymous, no LinkedIn login).
- **If `mcp__byob__browser_*` calls return transport errors mid-session**, the Chrome extension may have lost its bridge — run `cd ~/.byob && bun run doctor` in a fresh shell to repair, then retry the failed call.
