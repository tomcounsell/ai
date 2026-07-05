# Task 1: Check DMs

## Voice

Same as LinkedIn DMs: short (1-3 sentences), curious not eager, warm but professional. No sycophancy. X DMs skew more casual than LinkedIn. Match the platform.

## Read the inbox

```text
browser_navigate(url="https://x.com/messages", tabId=<x_tab>, waitUntil="networkidle")
browser_read(url="https://x.com/messages", reuseTab=true, screens=2)
```

The conversation list IEs have names like `"<sender name> <preview>"`. Skip:
- Snippets where the last sender appears to be Valor (recent. You're already waiting on them)
- Obvious spam / mass DMs
- Unsolicited sales/crypto pitches

If nothing remains, say "no DMs need replies right now" with a one-line reason and move on.

## Open a thread

```text
browser_click(tabId=<x_tab>, selector="byob:idx=<conversation_idx>")
browser_wait_for(tabId=<x_tab>, selector="[data-testid='dmDrawer'], [aria-label*='Message']", state="visible", timeoutSec=5)
browser_read(url="<current url>", reuseTab=true, screens=2)
```

Read the thread to understand context. Then check the work vault before replying:

```bash
ls ~/work-vault/Consulting/leads/
ls ~/work-vault/Consulting/chats/
```

- Known lead/chat → read their file, reply with awareness
- Unknown → friendly and curious
- Spam → skip

## Send the reply

Write draft to `/tmp/x-dm-reply.txt`. Then:

```text
# Find the message-input textbox in the latest read. Name typically "Start a new message" or empty role="textbox"
browser_click(tabId=<x_tab>, selector="byob:idx=<input_idx>")
browser_type(tabId=<x_tab>, selector="byob:idx=<input_idx>", text="<reply>", clear=true)
# Re-read for the send button. Name "Send" with tag "button"
browser_read(url="<current url>", reuseTab=true, screens=1)
browser_click(tabId=<x_tab>, selector="byob:idx=<send_idx>")
```

Confirm: textbox empties, your message appears at the bottom of the thread.

## Update knowledge base

Same conventions as `/linkedin` - `~/work-vault/Consulting/leads/{name}.md` for confirmed leads, `chats/{name}.md` for new contacts.
