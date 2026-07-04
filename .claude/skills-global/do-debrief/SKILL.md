---
name: do-debrief
description: "Send a spoken executive brief to a chat as a voice message. Triggered by 'send a voice debrief', 'speak this update', 'do-debrief', or any request to deliver an audio summary."
argument-hint: "<scope-or-notes> --chat <chat>"
allowed-tools: Bash, Read, Grep
user-invocable: true
---

# /do-debrief — Spoken Executive Brief to a Chat

Construct a 30-second executive brief, speak it, deliver it as a chat voice message. The skill does the construction work — it does **not** just synthesize whatever text you hand it. The output is shaped for **decisions**, not information.

## Repo Context Probe

If `.claude/skill-context/do-debrief.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its **collect-phase context sources** (which commands to pull raw material from beyond `git`/`gh`) and its **chat-delivery surface** (the command that sends a preface line and a voice note to a chat). When the file is absent, the collect phase uses only `git`/`gh`, and delivery requires a repo-provided chat-send command (see Delivery below).

## Why this exists

Executive bandwidth is the scarce resource. A brief earns its 30 seconds only when every item is something the recipient needs to **decide**, **act on**, or **know for a meeting they'll walk into that day**. Anything you're already handling should never appear. Calendars get echoed back — surface only the anomaly.

## Inputs

- **scope** (required, positional) — Either a short framing of what to brief on (`"morning standup"`, `"deploy debrief"`, `"post-merge update"`) or raw notes you want shaped into a brief.
- **--chat** (required) — Target chat name or numeric chat ID.
- **--voice** (optional) — Voice name; the context file may declare a default. Passed through to `/do-voice-recording`.
- **--reply-to** (optional) — Chat message ID to reply to (required for forum-group topics).
- **--no-preface** (optional) — Suppress the one-line text preface that normally precedes the voice note. Default is to send the preface; pass this flag for pure-audio delivery.

## The brief shape (target: ~30s, ~70 words spoken)

1. **Top decision + your recommendation** (~10s, ~25 words). Lead with the ask, not the context. Use **default-and-confirm**, not open questions: "I'm pushing the vendor call to Thursday unless you want it sooner" beats "What should we do about the vendor?" Their job is to veto, not deliberate.
2. **Second decision OR critical heads-up** (~8s, ~20 words). One thing only. Drop this slot if there isn't one — silence beats padding.
3. **Batched FYIs** (~8s, ~20 words). Open with "Three quick FYIs:" then one clause each. No laddering ("Also… and another thing…").
4. **Close**: "I've got the rest." (~2s).

## Construction phases

Do these in order. Skipping phases produces flabby briefs.

### 1. Collect (parallel)

Pull raw material in parallel — single message with multiple Bash calls. The generic sources are:

- `git log --oneline -20 origin/main` — recent commits
- `gh pr list --state all --limit 10` — open + recently merged PRs (when `gh` is available)

If the context file declares additional repo context sources (session activity, chat threads, calendar anomalies), pull those too, in the same parallel batch. If the user passed raw notes as the scope, skip the pulls and treat those notes as the corpus.

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
- **Never recite issue/PR numbers** (or any multi-digit identifier). TTS reads "1195" as either "one thousand one hundred ninety-five" (cumbersome) or "one-one-nine-five" (meaningless) — both waste the listener's attention, and the number isn't actionable in audio anyway. Refer to the work by substance: "the continuation crash," not "issue 1195." If the listener needs traceability, follow the voice message with a written brief that includes the numbers.
- **Proper-noun respelling** for TTS prosody. Product names benefit from hyphenation when they should sound like one word with multiple syllables (e.g., spell "Yudame" as `You-duh-may`). Dictionary-style hints only — never IPA in slashes; the phonemizer reads `/.../` literally and doubles the clip duration.

### 5. Pass B — cut and re-shape

- Count words. If >80, cut. If <55, you're padding — drop the weakest FYI.
- Verify the **first sentence is the ask**, not setup.
- Verify nothing in the brief is something you're handling yourself.
- Read it through once mentally — first pass at filler-word removal almost always frees ~10s for what matters.

### 6. Preface line

Build a one-line text preface that lands in the chat right before the voice bubble. It exists so the recipient can glance at the chat and decide whether to tap play, and so the message is searchable later by something other than waveform.

Format: `Brief update as of <H:MM AM/PM> · <N> items · <Q> questions`

- **items** — total things mentioned in the brief (Decisions + Critical heads-up + FYIs). Already-handled and Noise are dropped during categorization and don't count.
- **questions** — number of Decision items (each one is a default-and-confirm the recipient can veto).
- **timestamp** — the recipient's local hour:minute with am/pm. Use `date +"%-I:%M %p"` (machine localtime is fine for a same-day brief).

If the user passed `--no-preface`, skip this entirely.

### 7. Review gate

Show the final transcript and preface to the user with one line: `Final transcript (~Xs, Y words). Preface: "<preface>". Synthesize and send?` Only proceed on explicit confirmation. This is the cheap moment to catch a bad default-and-confirm before it lands as audio in someone's chat.

## Delivery (only after confirmation)

Synthesize the transcript with **`/do-voice-recording`** — that skill is the canonical TTS step and owns the portable resolution of the repo's TTS CLI. Pass the confirmed transcript and the chosen `--voice`; it returns the path to the audio file. Do not reimplement synthesis here.

Then deliver to the chat:
- If the context file declares a chat-send command, send the preface (unless `--no-preface`) followed by the voice note exactly as it specifies.
- If no context file is present, this skill's delivery dependency is unavailable — report the synthesized audio file path to the user and explain that delivering it as a chat voice note requires a repo-provided chat-send command this repo does not declare.

## Anti-patterns

- **Status-of-status.** "I'm working on the migration" isn't brief-worthy. Either it shipped (FYI) or it's blocked on a decision (Decision).
- **Synthesizing raw notes verbatim.** That's a dictated memo, not a brief. The construction phases above are the value the skill adds.
- **Skipping the review gate.** TTS + chat delivery is one-way; the wrong "I'm pushing the vendor call to Thursday unless you want it sooner" is permanent once it lands.

## Error handling

- **Synthesis (`/do-voice-recording`) fails** → STDERR carries `Error: <message>`. Surface it verbatim. The partial file is already deleted by that skill. Do not deliver.
- **Chat-send fails** → follow the context file's delivery error guidance (e.g. the payload was not enqueued; clean up any temp file so it doesn't leak).

## Relationship to `/do-voice-recording`

`/do-voice-recording` is the canonical raw-synthesis surface ("speak this text → audio file") and owns the portable resolution of the repo's TTS CLI. This skill is the composite: it constructs the decision-shaped brief, defers to `/do-voice-recording` for synthesis, and delivers to a chat. When you just need audio from text, use `/do-voice-recording` directly.
