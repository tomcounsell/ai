# Call voice: Charon to Alnilam

Tom, valor team chat, 2026-11-16 03:01 UTC, after hearing Gemini's male
voices: "Alnilam is best for now". A settings change with its test (hard
rule 9); this page is the stage's record, not a design.

## Change (one commit)

1. `core/settings.py:553`: `call_voice` default `"Charon"` becomes
   `"Alnilam"` (`VALOR_CALL_VOICE` still overrides).
2. `tests/test_call.py:760`: the assertion names `"Alnilam"`.
3. `docs/plans/gemini-live.md:27`: the plan is a record, so the line keeps
   Charon and gains a dated amendment in the form that file already uses:
   "(amended 2026-11-16: Tom picked Alnilam)". `docs/bridges/meet.md:42`
   names only `settings.call_voice`, so it stays. No other doc names a voice
   (grep of the repo for Charon: settings, the test, the plan line).

No check, gate, guard or validator is added. No governance.

## Rollout and confirmation

- Before merge: on each of the four Macs, no `VALOR_CALL_VOICE` in
  `~/Desktop/Valor/.env` or the kernel job's launchctl environment (on this
  Mac: none in the `.env`, none in `launchctl print`).
- Full suite (`uv run pytest -q -n auto -p no:cacheprovider`) and ruff.
- After merge: `core update --wait` in each kernel job's launchctl
  environment, detached (the Cowboy via `ssh localhost` with
  PATH=/opt/homebrew/bin); each Mac's head matches main.
- A short Live connect with the key from `settings.google_key_ref` and
  `call.live_config(...)`: the session opens and returns audio, and the
  config it was opened with names Alnilam. No call with real people.

## Tests

- The changed assertion: `live_config` asks Live for Alnilam.
- Beyond it: with `VALOR_CALL_VOICE` set, `Settings().call_voice` takes it
  (the override still works, so a Mac's env would win; that is why the
  rollout checks each Mac's env).

## Out of scope

Female voices, per-project voices, any change to the call's model or
instructions.

**Stakes.** A wrong voice name would stop calls from opening a Live session;
reversible in one line and caught by the live connect. Critique 0, review 0.
