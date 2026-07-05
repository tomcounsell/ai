---
name: google-workspace
description: "Use when accessing Google Workspace services including Gmail, Calendar, Docs, Sheets, Slides, Drive, and Chat. Triggered by requests for email, scheduling, document creation, or file management."
allowed-tools: Read, Write, Edit, Bash, WebFetch
user-invocable: false
---

# Google Workspace

Goal: complete Workspace tasks with the lightest tool available, in the user's
timezone, previewing every write before it happens. Success = the requested
operation done via the highest-priority working tier, with no unconfirmed writes
and no auto-sent mail.

## Repo Context Probe

If `.claude/skill-context/google-workspace.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares a faster project-local mail CLI to try *above* the generic Gmail ladder. When the file is absent (the common case in a foreign repo), start the mail ladder at `gws gmail` — the generic tiers below need nothing beyond a Google Workspace login or an interactive MCP session.

## Tool Selection

Walk the priority order per service top to bottom: `gws` CLI first, MCP second,
BYOB browser automation only as a last resort when nothing above can do the task
at all. Fall through on tool **absence OR auth failure** — a present-but-
unauthenticated `gws` (it needs a one-time `gws auth setup` / `gws auth login`
human OAuth step) must hand off to the next tier, not stall.

| Service | 1st: `gws` CLI | 2nd: MCP (where it exists) | 3rd (last resort) |
|---------|----------------|----------------------------|-------------------|
| Gmail | `gws gmail` | `mcp__claude_ai_Gmail__*` | BYOB |
| Calendar | `gws calendar` | `mcp__claude_ai_Google_Calendar__*` | BYOB |
| Drive | `gws drive` | `mcp__claude_ai_Google_Drive__*` | BYOB |
| Docs / Sheets / Slides / People / Chat / Forms / Keep | `gws <service>` | (no MCP) | BYOB |

For reading/sending mail specifically, prefer the `/email` skill's ladder. If the
repo context file declares a faster project-local mail CLI, `/email` puts it
ahead of `gws` as Tier 1; otherwise `gws gmail` is the top of the mail ladder.

## Core Rules

1. **User context first.** Establish who the user is
   (`gws people people get --params '{"resourceName": "people/me"}'`) and their
   timezone (`gws calendar settings get --params '{"setting": "timezone"}'`) at
   the start, then apply that context throughout. Display all times in the
   user's timezone with the abbreviation (EST, PST, ...).
2. **Preview every write.** Show complete details of any create/update/delete in
   readable form and wait for approval before executing. Deletions especially:
   deleting a calendar event as organizer cancels it for all attendees; as
   attendee it only removes it from their calendar.
3. **Pass URLs directly.** Tools handle URL-to-ID conversion — don't extract IDs
   manually. Use pagination for large result sets; batch related calls.
4. **Absolute paths for downloads.** Relative output paths are rejected for
   security.
5. **Number multi-item results.** Format lists and search results as numbered
   lists.

## Composing on Behalf of the User

Three constraints, no exceptions:

- **Async CTAs only** — never offer calls, meetings, or any synchronous contact;
  the agent cannot participate in real-time conversations. Use "happy to share
  more over email", "feel free to reply with questions", or no CTA.
- **Draft-first** — all outbound composition produces a draft
  (`gws gmail users drafts create`, or `mcp__claude_ai_Gmail__create_draft`).
  Never call a send tool without an explicit user instruction to send — even
  "write a reply" means a draft the user reviews and sends manually.
- **Honest representation** — if the product or operation is automated or
  AI-assisted, say so. Don't overclaim capabilities, team size, or operational
  structure.

## Operational Recipes

**Email search and reply**: search with `gws gmail users messages list` using
Gmail query syntax (`from:a@b.com is:unread`); fetch full content with
`gws gmail users messages get`; reply within the thread, draft-first. Include
SPAM/TRASH only when explicitly requested.

**Labels**: system labels ("INBOX", "SPAM", "TRASH", "UNREAD", "STARRED",
"IMPORTANT") use the name as the ID; custom labels need
`gws gmail users labels list` first. Apply/remove with
`gws gmail users messages modify` in a single call.

**Attachments**: `gws gmail users messages get` with `format=full` exposes
attachment IDs and filenames; download with
`gws gmail users messages attachments get` and an absolute output path.

**"Next meeting" / "today's schedule"**: fetch the full day (00:00:00–23:59:59),
filter to accepted + not-yet-responded (exclude declined unless asked —
`attendeeResponseStatus`), compare with the current time. Mention an in-progress
meeting first; "next" is the first meeting after now. Keep the day's context for
follow-ups.

**Documents in folders**: create the document first, then move it to the folder.

**Sheets output format**: `text` for human review, `csv` for export, `json` for
programmatic processing.

## Error Handling

- `{"error":"invalid_request"}` or a `gws` auth error usually means an expired
  session. For `gws`, re-run the human OAuth step (`gws auth setup` /
  `gws auth login`); for MCP, reset credentials and force re-login. If the tool
  stays unauthenticated, fall through to the next tier — tell the user why.
- Degrade gracefully: offer to create a missing folder, suggest alternatives on
  empty searches, explain permission failures plainly.
