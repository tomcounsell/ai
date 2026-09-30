---
name: email
description: "Read, search, draft, or send email using available mail tools, with thread-aware replies and verified delivery status."
---

# Email

Resolve the account and relevant thread. Prefer a declared project mail CLI (`valor-email` in Valor when available), then authenticated `gws gmail`, then an available mail connector, and browser UI as a fallback. Check actual tool schemas and CLI help; Claude connector names are not Codex capabilities. Fall through on absence or authentication failure, but after an uncertain send check delivery state before retrying through another tool.
Read the full relevant thread, recipients, and attachments before composing. Draft by default; send when the user explicitly authorizes sending. Preserve reply threading and exact recipients. Run $de-slop on substantive external drafts before finalizing or sending.
Valor examples: `valor-email read --limit 5`, `valor-email read --search "deployment" --since "2 hours ago"`; use `--json` to obtain message IDs and inspect `draft`/`send --help` for recipient and reply options. `valor-email send` confirms queueing, not SMTP delivery; check the email relay status when needed.
Return the answer, draft, or verified send outcome. Do not quietly add recipients or send a status message elsewhere.
