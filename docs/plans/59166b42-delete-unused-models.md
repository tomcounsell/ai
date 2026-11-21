# Unblock the Cowboy's rollout, then delete the unused local models

Tom, Slack DM 2026-11-21 14:54 UTC: "you should know which ones are used and
can delete the ones that aren't." Task 7b906188 wrote the list
(`docs/local-models.md`, merged at b11a17585) and deleted nothing. Its
rollout then failed (row 146152: "the checkout has uncommitted changes the
merge would overwrite: docs/machine.md"). Steering from Valor: fix the
rollout first, since it blocks every merge going live on the Cowboy, and land
the uncommitted edits as a commit rather than discard them.

## Known at plan time (observed 2026-11-21)

- The live checkout `~/src/ai` (Cowboy, `main` at e601dd093, `tomcounsell/ai`,
  **public**) has two uncommitted edits:
  - `docs/machine.md` (mtime 2026-11-20 09:12 +07): the backup paragraph now
    says the Cowboy keeps dumps on its own disk with `VALOR_BACKUP_KEEP=7`,
    no longer `/Volumes/PINK/valor_temp`. This matches the jobs:
    `com.valor.backup` and `com.valor.kernel` set `VALOR_BACKUP_KEEP=7` and
    no `VALOR_BACKUP_DIR`. Real edit; likely made by hand or by a turn in the
    backup work (task bcfdda8e1251 is a candidate).
  - `CLAUDE.md` (mtime 2026-11-09): a `<!-- yudame-tools:slack -->` block,
    which Yudame Tools writes into a folder's `CLAUDE.md` (Yudame's own
    channels, no client). Not in the failed merge's path set, but it would
    clash with any later merge touching `CLAUDE.md`.
  - e601dd093..b11a17585 changes only `docs/machine.md` of the two.
- Vault `~/Desktop/Valor/.env` lines 79-80 set `OLLAMA_LOCAL_MODEL` and
  `OLLAMA_VISION_MODEL`; no code reads them. `OLLAMA_URL` and
  `OLLAMA_API_KEY` stay.
- Other Macs are reached over ssh from the Cowboy
  (`valorengels@valor-the-captain`, and the Bald and Pirate the same way).

## Build, in this order

1. **Rollout.**
   - Name who made each edit: search the ledger and memory for the 2026-11-20
     backup change, and say so in the commit message (or "unknown" with what
     was searched).
   - Commit both edits on this branch, byte-identical to the live files
     (`git diff` of the live file against the commit is empty), message
     without client names. Push the branch (`push_branch` effect).
   - With the edits safe in a pushed commit, restore the two files in
     `~/src/ai` (`git checkout -- CLAUDE.md docs/machine.md`) so the pending
     fast-forward runs. Before that, save the live diff to
     `~/Desktop/Valor/backups/2026-11-21-live-checkout-edits.patch`.
   - Confirm the rollout: `~/src/ai` HEAD at or past b11a17585, and the
     ledger shows the rollout done for 7b906188's merge, not failed again.
     The edits come back to the live checkout when this task merges.
2. **Free space before**, per Mac: `df -h /` (and `df -k /` for exact).
3. **Delete, per Mac**, only the page's Unused rows, with its commands
   (`ollama rm`, `hf cache rm` from Valor's `.venv`, `rm -rf` for the
   superwhisper folder and `~/.u2net`, Yudame Tools' Disk Cleanup Remove or
   removing the file it names for Canary). Before each one, re-check on that
   Mac:
   - Ollama: `ollama ps` empty of it, and the server log since the inventory
     shows no load of it.
   - Hugging Face / u2net: no access time or new file since 2026-11-21's
     inventory, and no running process holding it (`lsof +D`).
   - superwhisper (Captain, Pirate): `/Applications/superwhisper.app` still
     absent, no process running.
   - Canary (Pirate): `defaults read me.yuda.tools` still selects Accurate
     (Parakeet), not Fast.
   A model that fails its re-check stays, and the report says why.
   Lists: Cowboy gemma4:e2b, mlx whisper-large-v3-turbo, faster-whisper-small.en,
   mlx whisper-base.en, smart-turn-v3. Captain gemma4:e2b, granite3.2-vision,
   superwhisper folder, u2net. Bald gemma4:e2b, mlx whisper-small.en. Pirate
   superwhisper folder, Canary.
4. **Free space after**, per Mac, and each deletion's measured size (`du`
   before removal; for HF, the `blobs/` shrink).
5. **Verify each Mac**: `ollama list` shows granite4.1:3b and (where it was)
   nomic-embed-text; tev1:0.8b still on the Cowboy; superwhisper folders still
   on the Cowboy and Bald; Valor's voice service on 8714 returns a transcript
   for a short clip and speaks (Kokoro synth returns audio).
6. **Docs and vault**: `docs/local-models.md` Unused section becomes
   "Deleted 2026-11-21" with per-Mac sizes, and any row kept after a
   failed re-check stays as Unused with the reason. Remove the two
   `OLLAMA_*_MODEL` lines from the vault `.env`, and the sentence citing them
   in the gemma4:e2b row.
7. **Report** in `.valor/done.md`: deleted per Mac with sizes, free space
   before and after, measured total, rollout state.

## Out of scope

tev1:0.8b; superwhisper app or its folder on the Cowboy and the Bald; every
Required and Kept row; any code change; the old `/Volumes/PINK` backups.

## Tests

No production code changes. Run `tests/test_bloat.py` and the tests that
read docs (`tests/test_kernel.py`, `tests/test_specialist_memory.py`), so the
committed `CLAUDE.md` block breaks no assertion on its contents. The build's
evidence, per Mac: before/after `ollama list`, HF and folder listings, `df`,
voice transcribe and speak round trips. Edge cases: a model loaded since the
inventory (kept); the Pirate set to Fast (Canary kept); a Mac unreachable
over ssh (its deletions open in the report); the `.env` synced over iCloud
(check one other Mac sees the removal); a shared Ollama blob (size counted
once, from `df`).
