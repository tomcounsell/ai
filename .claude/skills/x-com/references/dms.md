# Task 1: DMs

Voice as for LinkedIn DMs (1-3 sentences, curious not eager, warm but professional, no sycophancy), a notch more casual. Zero em-dashes (—), as in all X text.

## Read the inbox

```text
browser_navigate(url="https://x.com/messages", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/messages", reuseTab=true, screens=2)
```

Conversation IEs are named `"<sender name> <preview>"`. Skip threads where Valor sent last recently, spam and mass DMs, and unsolicited sales or crypto pitches. If nothing remains, say "no DMs need replies right now" with a one-line reason and move on.

## Open a thread

```text
browser_click(tabId=<x_tab>, selector="byob:idx=<conversation_idx>")
browser_wait_for(tabId=<x_tab>, selector="[data-testid='dmDrawer'], [aria-label*='Message']", state="visible", timeoutSec=5)
browser_read(url="<current url>", reuseTab=true, screens=2)
```

Before replying, check `~/work-vault/Consulting/leads/` and `chats/`: known contact → read their file; unknown → friendly and curious.

## Send

Draft to `/tmp/x-dm-reply.txt`, pass the publish gate's de-slop step in SKILL.md (DMs skip authenticity-pass), then:

```text
# Input: name "Start a new message" or an unnamed role="textbox"
browser_click(tabId=<x_tab>, selector="byob:idx=<input_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<input_idx>", text="<reply>", clear=true)
browser_read(url="<current url>", reuseTab=true, screens=1)
# Send: name "Send", tag "button"
browser_click(tabId=<x_tab>, selector="byob:idx=<send_idx>")
```

Success: textbox empties and the message appears at the bottom of the thread. Update `~/work-vault/Consulting/leads/{name}.md` for confirmed leads and `chats/{name}.md` for new contacts.
