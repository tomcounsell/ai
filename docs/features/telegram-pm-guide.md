# Telegram PM Guide

How to interact with the Valor AI system from Telegram as a project manager.

## Message Patterns

These patterns are recognized by the bridge routing system and fast-pathed to the appropriate handler.

### Work Requests (SDLC Pipeline)

| Pattern | Example | What Happens |
|---------|---------|-------------|
| `issue N` | `issue 365` | Fetches GitHub issue #365, assesses pipeline state, dispatches next stage |
| `issue #N` | `issue #365` | Same as above (optional `#` prefix on the number) |
| `pr N` | `pr 363` | Fetches PR #363, checks review/CI state, resumes pipeline from current stage |
| `PR N` | `PR 363` | Case-insensitive -- same as `pr N` |
| `pr #N` | `pr #363` | Same as above (optional `#` prefix on the number) |

### Patterns That Do NOT Work

| Pattern | Why |
|---------|-----|
| `#363` | Telegram consumes `#` as a hashtag/topic marker -- the bot never receives it |
| `pull request 363` | Not matched by the fast path; falls through to LLM classification (may still work but is slower) |

### Slash Commands

Slash commands are passed through directly and are not classified by the LLM router.

| Command | Purpose |
|---------|---------|
| `/sdlc` | Invoke the SDLC single-stage router |
| `/do-plan {slug}` | Create a plan document for a new feature |
| `/do-build {plan or issue}` | Execute a plan (build phase) |
| `/do-test` | Run test suite |
| `/do-patch` | Fix test failures or review blockers |
| `/do-pr-review` | Trigger PR review |
| `/do-docs` | Run documentation cascade |
| `/update` | Pull latest code and sync dependencies |

### Conversational Messages

Any message that does not match a fast-path pattern or slash command is classified by the LLM router:

- **Work requests** (feature descriptions, bug reports) are routed to the SDLC pipeline
- **Conversational messages** (questions, status checks) get direct responses
- **Acknowledgments** (`ok`, `thanks`, `got it`) are treated as passthroughs

## Session Resumption

### Reply-to Threading

Reply to a previous bot message to resume that session's context. The bridge uses Telegram's reply-to threading to associate your message with the original session.

- **New message** (no reply): Creates a fresh session
- **Reply to bot message**: Resumes the session that produced that message
- **Reply to your own message**: Creates a new session (only replies to bot messages resume)

### Session States

| State | Meaning | What to Do |
|-------|---------|------------|
| Active | Agent is working | Wait for response |
| Dormant | Agent paused on a question | Reply with an answer |
| Complete | Work finished | Start new work or react with thumbs-up |

## Signals

### Emoji Reactions

| Reaction | Meaning |
|----------|---------|
| Thumbs-up | "Done for now" -- marks the session as complete |
| Any other reaction | No special meaning (ignored by the system) |

### Status Updates vs Questions

The system distinguishes between status updates and genuine questions:

- **Status updates** (no question mark, no open items): The agent auto-continues working
- **Questions** (asking for your input): The agent pauses and waits for your reply
- If you see a status update and want to intervene, reply directly to steer the agent

## Common Workflows

### Kicking Off New Work

1. Describe the feature or bug in a message
2. The system classifies it as a work request and creates a GitHub issue
3. The SDLC pipeline starts: Plan -> Build -> Test -> Review -> Docs -> Merge
4. You receive updates at each stage transition

### Checking on a PR

1. Send `pr 363` (or whatever the PR number is)
2. The system fetches the PR, checks its state (CI, reviews, etc.)
3. It dispatches the appropriate next action (fix tests, address review, update docs)

### Resuming Stalled Work

1. Send `issue 365` to check on a stalled issue
2. The system assesses what stage the work is at
3. It picks up from where it left off

### Providing Feedback on a PR

1. Leave review comments on the GitHub PR
2. Send `pr 363` in Telegram
3. The system reads your review comments and dispatches `/do-patch` to address them
