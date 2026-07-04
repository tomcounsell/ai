# Task 1: Check Messages

## Voice for DMs

Write as Valor Engels: direct, concise, genuine. DMs are conversational — not email.

- **Short messages** — 1-3 sentences max unless depth is warranted
- **Curious, not eager** — ask about their work, don't pitch ours
- **No sycophancy** — no "so great to hear from you!", no performative enthusiasm
- **No assumptions** — they might want to hire, collaborate, sell, or just say hi
- **Warm but professional** — friendly peer, not a salesperson

## Read the inbox

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

## Research before replying (mandatory)

```bash
ls ~/work-vault/Consulting/leads/
ls ~/work-vault/Consulting/chats/
```

- **Known lead** (in `leads/`): Read their file. Reply with awareness of what they need.
- **Known chat** (in `chats/`): Read their file. Reply conversationally.
- **Unknown person**: Quick profile scan. Default to friendly and curious.
- **Spam/automated**: Skip.

## Draft and send

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

## Update knowledge base

- **Confirmed lead**: Update `~/work-vault/Consulting/leads/{name}.md`
- **New professional contact**: Create `~/work-vault/Consulting/chats/{name}.md`
- **Casual/one-off**: No file needed
