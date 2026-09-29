---
name: do-debrief
description: "Send a spoken executive brief to a chat as a voice message. Triggers: 'send a voice debrief', 'speak this update', 'do-debrief', any audio summary request."
argument-hint: "<scope-or-notes> --chat <chat>"
allowed-tools: Bash, Read, Grep
user-invocable: true
model: sonnet
effort: medium
---

# /do-debrief — Spoken Executive Brief to a Chat

Construct a ~30-second executive brief, speak it, and deliver it to a chat as a voice message. Executive attention is the scarce resource: every item must be something the recipient needs to **decide**, **act on**, or **know for a meeting today**. Anything you are already handling stays out; a calendar gets only its anomalies. Raw notes are material to shape, not a script to read aloud.

## Repo context

If `.claude/skill-context/do-debrief.md` exists, honor it: it declares extra collect-phase sources, a default voice, and the chat-send command. Without it, collect from `git`/`gh` only, and delivery needs a repo-provided chat-send command (see Delivery).

## Inputs

- **scope** (positional, required): a framing ("morning standup", "deploy debrief") or raw notes to shape.
- **--chat** (required): chat name or numeric ID.
- **--voice**: passed to `/do-voice-recording`; the context file may set a default.
- **--reply-to**: message ID to reply to (required for forum-group topics).
- **--no-preface**: skip the one-line text preface.

## The brief (~70 words spoken, 55-80)

1. **Top decision + your recommendation** (~25 words). Lead with the ask, in default-and-confirm form: "I'm pushing the vendor call to Thursday unless you want it sooner," not "What should we do about the vendor?" Their job is to veto, not deliberate.
2. **Second decision or critical heads-up** (~20 words). One thing; drop the slot if there isn't one.
3. **Batched FYIs** (~20 words): "Three quick FYIs:" then one clause each, no laddering ("Also… and another thing…").
4. **Close**: "I've got the rest."

## Building it

- **Collect** in one parallel batch: `git log --oneline -20 origin/main`, `gh pr list --state all --limit 10`, plus any context-file sources. If the scope is raw notes, those notes are the corpus.
- **Categorize** each item as Decision (needs their yes/no), Critical heads-up (they'd be blindsided today), FYI (one clause), or drop it (already handled, or noise). Status-of-status ("I'm working on the migration") is never brief-worthy: it either shipped (FYI) or is blocked on a decision. If dropped items are over 70% of the material, tell the user a brief isn't worth sending today and stop.
- **Fill gaps.** Each Decision needs its default action, deadline or next checkpoint, and cost of being wrong. Ask for all missing pieces in one question, not piecemeal; a Decision that still can't be resolved becomes a heads-up or is dropped.
- **Write for the ear**: contractions, and the prosody rules in `/do-voice-recording` (no multi-digit identifiers such as issue or PR numbers, since TTS mangles them and they aren't actionable by ear; refer to work by substance; dictionary-style respelling of proper nouns, never IPA). Cut to 55-80 words; the first sentence must be the ask.
- **Preface** (unless `--no-preface`): `Brief update as of <H:MM AM/PM> · <N> items · <Q> questions`, where items = decisions + heads-up + FYIs, questions = decisions, time from `date +"%-I:%M %p"`. It lets the recipient decide whether to tap play and makes the message searchable.

## Review gate

Voice delivery to a chat is one-way. Show the user `Final transcript (~Xs, Y words). Preface: "<preface>". Synthesize and send?` and proceed only on explicit confirmation.

## Delivery (only after confirmation)

Synthesize with `/do-voice-recording` (pass the transcript and `--voice`; it returns the audio path). Then send the preface (unless `--no-preface`) and the voice note using the context file's chat-send command. With no context file, report the audio path and say that chat delivery needs a repo-provided chat-send command this repo does not declare.

## Errors

- Synthesis fails → surface its `Error: <message>` verbatim; `/do-voice-recording` has already removed the partial file. Do not deliver.
- Chat-send fails → follow the context file's delivery error guidance.
