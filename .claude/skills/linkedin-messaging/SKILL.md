---
name: linkedin-messaging
description: "Use when checking or replying to LinkedIn DMs. Triggered by requests to read LinkedIn messages, reply to conversations, or check LinkedIn inbox."
allowed-tools: Bash(agent-browser:*), Read, Write, Edit, Grep, Glob, Agent
user-invocable: true
---

# LinkedIn Messaging

Check and reply to LinkedIn direct messages using agent-browser connected to Chrome via CDP.

## Voice — Teammate Persona

Write as Valor Engels: direct, concise, genuine. The same voice used in Telegram teammate mode.

- **No assumptions** — never assume why someone messaged. They might want to hire, collaborate, sell, catch up, or just say hi
- **No sycophancy** — no "so great to hear from you!", no performative enthusiasm
- **Short messages** — LinkedIn DMs are conversational. 1-3 sentences max unless depth is warranted
- **Curious, not eager** — ask about their work, don't pitch ours
- **Warm but professional** — friendly peer, not a salesperson

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

## Workflow

### Step 1. Check Messages

```bash
agent-browser open "https://www.linkedin.com/messaging/"
sleep 3
agent-browser snapshot -i
```

Identify conversations with unread messages or recent activity. For each conversation needing attention, proceed through steps 2-5.

### Step 2. Read the Conversation

Click into the conversation and read the full thread:

```bash
agent-browser click @eN  # Click the conversation
sleep 2
agent-browser get text ".msg-s-message-list-content"
```

Understand:
- Who is this person? (name, headline visible in the thread)
- What did they say?
- Is this a new connection, ongoing conversation, or cold outreach?
- Does this need a reply at all? (spam, automated messages, and recruiter templates may not)

### Step 3. Research Before Replying (mandatory)

**Check the knowledge base first:**
```bash
# Check if this person is a known lead or contact
ls ~/work-vault/Consulting/leads/
ls ~/work-vault/Consulting/chats/
```

**If not a known contact, research them:**
- View their LinkedIn profile (click their name in the conversation)
- Note: company, role, mutual connections, recent activity
- Spawn research agents if the conversation suggests business potential

**Decision gate:**
- **Known lead** (in `leads/`): Read their file for context. Reply with awareness of what they need.
- **Known chat** (in `chats/`): Read their file. Reply conversationally.
- **Unknown person**: Quick profile scan. Default to friendly and curious.
- **Spam/automated**: Skip. No reply needed.

### Step 4. Draft the Reply

Write the draft to `/tmp/linkedin-reply.txt`.

**Message style by context:**
- **Greeting ("Hi", "Hey")**: Respond warmly, ask about what they're working on. Show you know who they are.
- **Question about our work**: Answer directly, link to code/docs if relevant.
- **Business inquiry**: Be curious about their needs. Don't pitch — ask what they're trying to solve.
- **Catch-up**: Be genuine. Reference shared context if any exists.
- **Cold outreach/sales**: Polite decline or redirect. One sentence.

**Quality check:**
- Is it short enough? LinkedIn DMs should feel like texting, not email.
- Does it invite a response without being needy?
- Would Valor actually say this?

### Step 5. Send the Reply

```bash
agent-browser snapshot -i
# Find the message textbox
agent-browser click @eN  # Focus the input
agent-browser fill @eN "reply text"
agent-browser snapshot -i
# Find and click Send
agent-browser click @eN
```

Verify it sent:
```bash
sleep 2
agent-browser snapshot -i  # Confirm message appears, Send button disabled
```

### Step 6. Update Knowledge Base

After replying, update or create the contact file:

- **Lead** (Tom confirmed): Update `~/work-vault/Consulting/leads/{name}.md` with conversation notes
- **New professional contact**: Create `~/work-vault/Consulting/chats/{name}.md` with basic profile info and conversation summary
- **Casual/one-off**: No file needed

## Batch Processing (Daily Check)

When checking all messages at once:

1. Open messaging inbox
2. Scan for unread/recent conversations
3. For each conversation, assess: does this need a reply?
   - **Yes**: Follow steps 2-6
   - **No** (spam, no-reply-needed, already responded): Skip
4. Report summary of actions taken

## Notes

- CDP connection persists across commands in the same session
- LinkedIn's DOM changes frequently — always re-snapshot after interactions
- Wait 2-3 seconds after navigation for dynamic content to load
- If elements aren't found, scroll down and re-snapshot
- Message input may need to be clicked/focused before typing
- LinkedIn may show a "seen" indicator — be aware that opening a message marks it as read
