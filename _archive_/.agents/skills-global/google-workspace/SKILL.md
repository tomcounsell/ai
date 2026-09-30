---
name: google-workspace
description: "Work with Google Drive, Docs, Sheets, Calendar, and Gmail through authenticated Workspace CLI or available connectors."
---

# Google Workspace

Resolve the service, account, and exact target file or calendar from the user's request. Prefer installed authenticated `gws` commands or a connected service-specific tool; inspect `gws --help`, relevant service help, and `gws auth status` rather than assuming command schemas. Fall back to the available browser surface if neither API path can reach the resource.
Search by title and context, then use stable IDs. Read the target and its permissions before changing it. Use the installed documents, spreadsheets, or presentations skill for artifact-specific creation and validation; Markdown remains the default for an unspecified doc. Distinguish local file creation from upload, sharing, or edits to a live Google artifact.
Verify changes by reading the saved object. Do not grant sharing permissions or send email merely because a read or draft was requested. For ambiguous writes, inspect the target's state before retrying. Report auth failures plainly and continue work that does not require the unavailable account.
