---
name: reading-sms-messages
description: "Read or search macOS SMS and iMessage history, including a requested recent verification code."
---

# Reading Sms Messages

Use the Valor SMS reader when installed: `python -m tools.sms_reader.cli`. Inspect help for current flags. Examples: `2fa --minutes 5`, `recent --limit 10`, `search "verification"`, and `senders --limit 10 --since-days 7`. Narrow by sender and time window when possible.
This requires access to the local macOS Messages database and the necessary OS permissions. If unavailable, report the specific access failure; do not assume another machine's messages are visible. Read only the requested scope and return relevant results. Do not persist verification codes in repo files, logs, or long-lived memory. This is a reader capability; it does not authorize sending a message.
