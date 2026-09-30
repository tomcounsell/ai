# Private-Tag Handling

When the user wraps content in `<private>...</private>` tags, treat that content as **ephemeral**: visible to you in the current turn for context, but do NOT quote it back verbatim in your response, do NOT include it in summaries, and do NOT echo it into tool calls whose output is logged or persisted (`Bash` commands that print it, file writes, GitHub issue/PR bodies, Telegram or email messages, memory saves).

The user has marked it as do-not-persist. Quoting it back risks re-introducing it into stored agent outputs (`TelegramMessage.content`, post-session memory extraction, bridge logs), which defeats the user's opt-out.

If you must reference the wrapped content, describe it abstractly. For example:

- User: `the API key is <private>sk-abc123</private>, can you check it?`
- You: "I see the key starts with `sk-` and is 11 characters — looks like an OpenAI-style key."
- Not: "I see your key `sk-abc123` is 11 characters..."

The tag is case-sensitive (`<private>`, lowercase) and single-level (no nesting).
