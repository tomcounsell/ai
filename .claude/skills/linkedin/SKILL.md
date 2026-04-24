---
name: linkedin
description: "Use when browsing LinkedIn, reading posts, writing comments, checking DMs, or engaging with content. Triggered by requests to comment on LinkedIn, browse feed, interact with posts, or read/reply to LinkedIn messages."
allowed-tools: Bash(agent-browser:*), Bash(git:*), Read, Write, Edit, Grep, Glob, Agent
user-invocable: true
---

# LinkedIn Activity

Browse LinkedIn, engage with posts, and manage direct messages using agent-browser connected to Chrome via CDP.

## Default Behavior (no arguments)

Run all three tasks in order:

1. **Check messages** — reply to any DMs that need attention
2. **Write a post** — about recent work from git history
3. **Browse feed** — find and comment on 3 posts

If arguments are given, interpret them and do only what's asked.

## Prerequisites

Chrome must be running with CDP enabled and connected to agent-browser:

```bash
# If not already connected:
pkill -f "Google Chrome" && sleep 2
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile &>/dev/null &
sleep 5
agent-browser connect 9222
```

User must be logged into LinkedIn in the browser.

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

```bash
agent-browser open "https://www.linkedin.com/messaging/"
sleep 3
agent-browser snapshot -i
```

For each conversation with unread messages or recent activity:

```bash
agent-browser click @eN   # click the conversation
sleep 2
agent-browser get text ".msg-s-message-list-content"
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

```bash
agent-browser snapshot -i
agent-browser click @eN   # focus message input
agent-browser fill @eN "reply text"
agent-browser snapshot -i
agent-browser click @eN   # Send
sleep 2
agent-browser snapshot -i  # verify sent
```

### Update knowledge base

- **Confirmed lead**: Update `~/work-vault/Consulting/leads/{name}.md`
- **New professional contact**: Create `~/work-vault/Consulting/chats/{name}.md`
- **Casual/one-off**: No file needed

---

## Task 2: Write a Post

### Research

```bash
git log --oneline --since="5 days ago"
```

For interesting commits, read the relevant files to understand what actually changed. Check `docs/features/` for new feature docs. Look for patterns — what problem space was being worked on?

**Only post if there's something genuinely interesting:**
- A non-obvious architectural decision
- A problem that took real effort to solve
- A pattern other builders would find useful
- Skip routine bug fixes, formatting, dependency bumps

### Draft

Write to `/tmp/linkedin-post.txt`.

**Quality standards:**
- **Opening**: Frame who this is for before dropping into the problem. "Running AI agents on multi-hour dev tasks exposes..." lands better than "Context compaction is...". The first sentence should answer: who is this relevant to, and why now?
- **Body**: Show the technical detail — what the approach was, why it was interesting, one concrete takeaway a peer could apply
- **No bullet-point listicles** unless the content is naturally a list
- **Closing**: End with the open source repo link (`github.com/tomcounsell/ai`) and 3–5 relevant hashtags. Hashtags to choose from based on content: `#AIAgents` `#AgenticAI` `#ClaudeAI` `#OpenSource` `#DeveloperTools` `#LLMs` `#MachineLearning` `#SoftwareEngineering`
- Default ~800 characters; go longer only if the content genuinely needs it

### Revise (2 passes)

1. Cut anything that reads like a summary or announcement — show, don't narrate
2. Read as a peer who builds similar systems: does it teach them something?

Show the final post inline before publishing.

### Publish

```bash
agent-browser open "https://www.linkedin.com/feed/"
# snapshot -i, find "Start a post" button, click it
# fill the textbox, find and click Post
# snapshot -i to verify it appears
```

---

## Task 3: Browse Feed and Comment

### Read the feed

```bash
agent-browser open "https://www.linkedin.com/feed/?sortBy=RECENT"
sleep 3
agent-browser get text "main" 2>&1 | head -150
```

Scroll and repeat to load more posts:

```bash
agent-browser scroll down 1500
sleep 2
agent-browser get text "main" 2>&1 | head -150
```

If the feed cycles (same posts repeating), stop — you're already on Recent.

### React as you scroll

As you read through posts, Like any that are relevant to the work — agentic systems, memory, RAG, async architecture, developer tooling, AI in production. This trains the feed toward more of the same. You don't need to comment to react; a thumbs-up takes one click.

```bash
agent-browser snapshot -i
# Find "React Like" button for the post
agent-browser click @eN
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

Target: 3 posts that pass this gate.

### For each post: read, draft, revise, post

**Read:** Open the post URL directly to get the full text.

**Draft** to `/tmp/linkedin-comment-N.txt` (N = 1, 2, 3):
- Default ~200 characters; go longer only when sharing architecture or linking code
- Every claim must trace to a specific file in this repo
- Max 1250 characters (LinkedIn limit)

**Revise (2 passes):**
1. **Substance** — Does every sentence add value? Does every claim map to real code?
2. **Tone & hook** — Peer reviewer, not fan. Does it provoke thought? Can it be shorter?

Show the final comment inline before posting.

**Post:**
```bash
agent-browser snapshot -i
# Find Comment button by proximity to author name or reaction count
# — don't rely on nth= index, it shifts as the page loads more content
agent-browser click @eN
sleep 2
agent-browser snapshot -i
# Find textbox "Text editor for creating comment"
agent-browser fill @eN "comment text"
# Submit is labeled "Comment" (not "Post") — find it adjacent to the textbox
agent-browser click @eN
sleep 3
agent-browser snapshot  # verify comment appears
```

---

## Editing Comments

**Never use Edit to post a correction.** Editing replaces the full text — a "Correction: ..." opener reads as nonsense once the original is gone.

If a posted comment needs correcting:
- **Delete it** and post a clean replacement, or
- **Post a new reply** beneath it with the correction in context

Only use Edit to fix typos or rewrite the whole comment as a clean standalone.

---

## Notes

- CDP connection persists across commands in the same session
- LinkedIn's DOM changes frequently — always re-snapshot after interactions
- Wait 2-3 seconds after navigation for dynamic content to load
- If elements aren't found, scroll down and re-snapshot
- Opening a message marks it as read — be aware of "seen" indicators
