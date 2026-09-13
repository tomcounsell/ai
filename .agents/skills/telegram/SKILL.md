---
name: telegram
description: "Read or search Valor Telegram history and send specifically authorized messages or media through the correct relay."
---

# Telegram

Use `valor-telegram` for history and chat discovery. Inspect its help, then select one of `--chat`, `--chat-id`, `--user`, or `--project`. For ambiguous names use `read --strict` and resolve the actual chat ID. Check the freshness header before trusting results; an old last-activity timestamp can reveal the wrong chat. `--project` unions that project's chats and applies the limit to the merged result.
Examples: `valor-telegram chats --search dev`, `valor-telegram read --chat-id <ID> --limit 10 --json`, or `valor-telegram read --project valor --limit 20`. Reads use the Popoto history store.
Send only when explicitly authorized, with a verified recipient/topic and exact content. Inside a managed Valor agent session, use `python tools/send_message.py` so the canonical output handler and summarizer tracking run. That path needs a real session context; a standalone Codex task must use an available Telegram connector or the documented operator CLI with an explicit recipient, not fabricate session environment variables. Inspect send help for media and forum reply options.
Verify sent, suppressed, deferred, or queued results accurately; queueing is not delivery. After an uncertain write inspect state before retrying. Return the verified outcome without sending additional status messages elsewhere.
