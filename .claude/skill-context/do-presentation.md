# do-presentation context — this repo (ai)

## Narrated deck video (`--video` mode)

This repo powers `--video` with `valor-deck-video`, which owns the whole pipeline (Marp PNG-per-slide export, one `valor-tts` clip per slide whose duration sets the hold time, ffmpeg mux to `deck.mp4`). Author the deck, then run:

```bash
valor-deck-video "<source>.md"
```

Narration schema: one `<!-- narration: <speaker text> -->` comment per slide, anywhere between that slide's `---` separators. Marp ignores HTML comments, so the same source still exports the static deck. A slide with empty or missing narration holds silently for `DECK_VIDEO_DEFAULT_HOLD_SECS` (default 4.0s) and is never dropped.

`valor-deck-video` calls `valor-tts` directly because it needs each clip's measured duration; that is a deliberate exception. A standalone voiceover track still goes through `/do-voice-recording`.

## Deck output location

`docs/presentations/` was deliberately deleted (issue #1900); do not recreate it. Decks are deliverables, not repo docs: author and export in a scratch location (session scratchpad or `/tmp`), never under `docs/`.

- **Business-valuable decks** (client or strategy): move the final PDF (and any `*-report.md` companion) to `~/work-vault/AI Valor Engels System/`, run `valor-ingest` on the PDF, and add a row to that directory's `README.md` index.
- **One-off internal decks**: deliver the file (Telegram or email attachment) and let the scratch copy expire.
