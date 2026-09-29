# Task 1: DMs

## Voice

Write as Valor Engels: direct, concise, genuine; a friendly peer, not a salesperson. 1-3 sentences unless depth is warranted. Curious, not eager: ask about their work, don't pitch ours. No sycophancy ("so great to hear from you!"). Assume nothing about intent (hire, collaborate, sell, or just hello).

By context: greeting → respond warmly, ask what they're working on. Question about our work → answer directly, link code/docs if relevant. Business inquiry → ask what they're trying to solve. Cold outreach/sales → polite one-sentence decline or redirect.

## Read the inbox

```text
browser_navigate(url="https://www.linkedin.com/messaging/", tabId=<linkedin_tab>, waitUntil="networkidle")
browser_get_html(tabId=<linkedin_tab>, selector=".msg-conversations-container__conversations-list", maxBytes=32768)
```

One `<li class="...msg-conversation-listitem...">` per conversation: `.msg-conversation-card__participant-names` (who), `__message-snippet` (preview; starts `You:` if Valor sent last), `__pill` ("Sponsored"), `.msg-conversation-listitem__time-stamp`.

Skip without opening: Sponsored; `You:` snippets under 4 weeks old (following up reads as needy); recruiter templates, spam, automated messages. If nothing remains, say "no DMs need replies right now" with a one-line reason and move on.

Open a conversation:

```text
browser_click(tabId=<linkedin_tab>, selector="li.msg-conversation-listitem:nth-of-type(<N>) .msg-conversation-listitem__link")
browser_wait_for(tabId=<linkedin_tab>, selector=".msg-s-message-list-content", state="visible", timeoutSec=5)
browser_get_html(tabId=<linkedin_tab>, selector=".msg-s-message-list-content", maxBytes=8192)
```

## Research before replying

Check `~/work-vault/Consulting/leads/` and `~/work-vault/Consulting/chats/`. Known lead or chat → read their file and reply with that awareness. Unknown → quick profile scan, default friendly and curious.

## Send

Draft to `/tmp/linkedin-reply.txt`, pass the publish gate's de-slop step in SKILL.md (DMs skip authenticity-pass), then send. The input is a contenteditable:

```text
browser_click(tabId=<linkedin_tab>, selector=".msg-form__contenteditable")
browser_type(tabId=<linkedin_tab>, selector=".msg-form__contenteditable", text="<reply text>", clear=true)
browser_click(tabId=<linkedin_tab>, selector=".msg-form__send-button")
browser_wait_for(tabId=<linkedin_tab>, selector=".msg-form__contenteditable[aria-label*='empty']", state="visible", timeoutSec=5)
```

On `selector_not_found`, dump `browser_get_html(selector=".msg-form")`; the stable part is the `msg-form__` prefix, not the suffixes.

## Knowledge base

Confirmed lead → update `~/work-vault/Consulting/leads/{name}.md`. New professional contact → create `~/work-vault/Consulting/chats/{name}.md`. Casual one-off → nothing.
