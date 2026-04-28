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
  `Popoto`, file paths, function names — none of those belong in a
  LinkedIn post. If you find yourself reaching for them, the lesson hasn't
  been translated yet.
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
3. Shows the final post inline to the user before publishing
4. Publishes via agent-browser

If the draft fails the casual-reader test, send the subagent back with specific feedback (e.g. "sentence two still uses 'API call' — translate that") rather than rewriting it inline. The whole point of the subagent is to keep the engineering context out of the draft; rewriting inline reintroduces it.

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

As you read through posts, Like any that are relevant to the work — agentic systems, memory, RAG, async architecture, developer tooling, AI in production. This trains the feed toward more of the same. A thumbs-up takes one click and doesn't require a comment.

**Commenting always implies a Like.** If a post passes the screening gate and you draft a comment for it, also like the post. Engagement should be coherent — a comment without the like reads as half-engaged. The reverse isn't true: plenty of posts are worth a like but not a comment.

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

### Audience model for comments

**The post sets the audience, not your codebase.** A strategy post's readers are PMs and execs — write for them. A design-system post's readers are designers — write for them. Only when an AI-engineering post is in front of a self-selected technical audience can you reach for technical framing, and even then with restraint.

Common failure mode: the parent session has just spent 20 minutes inside engineering files and writes a comment that mirrors that context — file paths, function names, internal terms — onto a post whose audience can't decode any of it. The codebase grounding is for *you*, not for the comment text. File paths and function names belong in the drafter's notes, never in the published reply.

### For each post: read, gather, delegate, verify

**Read:** Open the post URL directly to get the full text.

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
3. Show the final comment inline to the user before posting
4. Save to `/tmp/linkedin-comment-N.txt` and post via agent-browser

**Like the post you commented on.** Engagement should be coherent — if you cared enough to write a comment, you cared enough to react. After the comment posts, find the post-level "React Like" button (not the comment-level one — the post-level Like sits next to "Comment" and "Repost" in the post's action bar) and click it. If the button label shows "Unreact Like" or `[pressed]`, the post was already liked; skip. The reaction goes on the post itself, never on your own comment.

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
