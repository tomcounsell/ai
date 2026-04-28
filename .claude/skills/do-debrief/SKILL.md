---
name: do-debrief
description: "Use when sending a spoken debrief to a Telegram chat. Collects context, drafts a 30-second executive brief, synthesizes it via TTS, and delivers it as a native voice message. Triggered by 'send a voice debrief', 'speak this update', 'do-debrief', or any request to deliver an audio summary."
argument-hint: "<scope-or-notes> --chat <chat>"
allowed-tools: Bash, Read, Grep
user-invocable: true
---

# /do-debrief — Spoken Executive Brief to Telegram

Construct a 30-second executive brief, speak it, deliver it as a Telegram voice message. The skill does the construction work — it does **not** just synthesize whatever text you hand it. The output is shaped for **decisions**, not information.

## Why this exists

Executive bandwidth is the scarce resource. A brief earns its 30 seconds only when every item is something the recipient needs to **decide**, **act on**, or **know for a meeting they'll walk into that day**. Anything you're already handling should never appear. Calendars get echoed back — surface only the anomaly.

## Inputs

- **scope** (required, positional) — Either a short framing of what to brief on (`"morning standup"`, `"deploy debrief"`, `"post-merge update for psyoptimal"`) or raw notes you want shaped into a brief.
- **--chat** (required) — Target chat name (e.g. `"Dev: Valor"`) or numeric chat ID.
- **--voice** (optional) — Voice name; defaults to `am_michael` (Kokoro). `bf_alice` is the female alternative. See `tools/tts/README.md`.
- **--reply-to** (optional) — Telegram message ID to reply to (required for forum-group topics).

## The brief shape (target: ~30s, ~70 words spoken)

1. **Top decision + your recommendation** (~10s, ~25 words). Lead with the ask, not the context. Use **default-and-confirm**, not open questions: "I'm pushing the vendor call to Thursday unless you want it sooner" beats "What should we do about the vendor?" Their job is to veto, not deliberate.
2. **Second decision OR critical heads-up** (~8s, ~20 words). One thing only. Drop this slot if there isn't one — silence beats padding.
3. **Batched FYIs** (~8s, ~20 words). Open with "Three quick FYIs:" then one clause each. No laddering ("Also… and another thing…").
4. **Close**: "I've got the rest." (~2s).

## Construction phases

Do these in order. Skipping phases produces flabby briefs.

### 1. Collect (parallel)

Pull raw material in parallel — single message with multiple Bash calls:

- `git log --oneline -20 origin/main` — recent commits
- `gh pr list --state all --limit 10` — open + recently merged PRs
- `python -m tools.valor_session list` — session activity
- `valor-telegram read --chat "<scope-relevant>" --since "24 hours ago"` — outstanding chat threads (only if scope names a chat)
- Calendar anomalies **only** for daily/morning briefs: `gws calendar events list --params '{...}'` — surface only items that **moved**, **conflict**, or are **net-new since yesterday**. Never read the agenda back.

If the user passed raw notes as the scope, skip the pulls and treat those notes as the corpus.

### 2. Categorize

For each candidate item, classify it into exactly one bucket:

- **Decision** — needs the recipient's yes/no.
- **Critical heads-up** — they'll get blindsided in a meeting today if they don't know.
- **FYI** — material but non-urgent; fits in one clause.
- **Already-handled** — drop. You handling it ≠ them needing to know.
- **Noise** — drop.

If Already-handled + Noise together account for >70% of raw material, the brief probably isn't worth sending today. Tell the user that and exit, instead of synthesizing filler.

### 3. Identify gaps

For every **Decision** item, confirm you have:

- the current default course of action,
- the deadline or next checkpoint,
- the cost of being wrong.

If any of those are missing, **consolidate** the unknowns into a single clarifying question to the user before drafting. Do not ask piecemeal. If a Decision item still can't be resolved, demote it to Critical heads-up or drop it.

### 4. Draft pass A

Write the brief in the shape above. Apply these rules verbatim:

- **Default-and-confirm phrasing** on every decision: state your intended action + the unless-clause.
- **Contractions** ("I'm", "don't", "we're") — written prose reads stiff aloud.
- **Proper-noun respelling** for TTS prosody. Product names benefit from hyphenation when they should sound like one word with multiple syllables (e.g., spell "Yudame" as `You-duh-may`). Dictionary-style hints only — never IPA in slashes; the phonemizer reads `/.../` literally and doubles the clip duration.

### 5. Pass B — cut and re-shape

- Count words. If >80, cut. If <55, you're padding — drop the weakest FYI.
- Verify the **first sentence is the ask**, not setup.
- Verify nothing in the brief is something you're handling yourself.
- Read it through once mentally — first pass at filler-word removal almost always frees ~10s for what matters.

### 6. Review gate

Show the final transcript to the user with one line: `Final transcript (~Xs, Y words). Synthesize and send?` Only proceed on explicit confirmation. This is the cheap moment to catch a bad default-and-confirm before it lands as audio in someone's chat.

## Delivery (only after confirmation)

```bash
OUT=$(mktemp -t debrief).ogg

valor-tts --text "$TRANSCRIPT" --output "$OUT" || {
    echo "Synthesis failed"
    rm -f "$OUT"
    exit 1
}

valor-telegram send \
    --chat "Dev: Valor" \
    --voice-note \
    --cleanup-after-send \
    --audio "$OUT"
```

The relay owns the file from the moment the payload is pushed — it deletes on successful send OR after dead-letter placement on retry exhaustion. Synchronous deletion races the relay's retry loop.

## Anti-patterns

- **Reading the calendar back.** They have it. Surface only the anomaly — "Your 2pm moved to 3" — not the full agenda.
- **Open-ended questions.** "What about the vendor?" wastes 30 seconds of attention. State your default; let them veto.
- **Laddered FYIs.** "Also… and another thing… oh and…" — pre-batch into "Three quick FYIs:" with one clause each.
- **Status-of-status.** "I'm working on the migration" isn't brief-worthy. Either it shipped (FYI) or it's blocked on a decision (Decision).
- **Synthesizing raw notes verbatim.** That's a dictated memo, not a brief. The construction phases above are the value the skill adds.
- **Skipping the review gate.** TTS + Telegram is one-way; the wrong "I'm pushing the vendor call to Thursday unless you want it sooner" is permanent once it lands.

## Error handling

- **`valor-tts` exits non-zero** → STDERR carries `Error: <message>`. Surface it verbatim. Delete the partial file. Do not push to the outbox.
- **`valor-telegram send` exits non-zero** → the payload was not enqueued. The temp file is still on disk; remove it manually so it doesn't leak.
- **Bridge relay not running** → the payload sits in Redis until the relay starts. If you need synchronous confirmation, run `./scripts/valor-service.sh status` first.

## Why no `/tts` skill

`tools/transcribe/` has no `/transcribe` skill — its CLI + README is the stable agent-facing surface. `tools/tts/` mirrors that. Agents invoke `valor-tts` via Bash directly for raw synthesis. `/do-debrief` is the one user-invocable composite, and the construction phases above are why it earns its own skill instead of "pipe text to valor-tts."

## Related references

- `tools/tts/README.md` — full TTS API, voice catalog, troubleshooting
- `bridge/telegram_relay.py` — `_send_queued_message` voice-note branch + `cleanup_file` honoring
- `docs/features/tts.md` — feature design + dual-backend rationale
