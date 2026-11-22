# Uninstall superwhisper and Handy from the four Macs

Tom, Slack DM 2026-11-22 05:26 UTC: "Uninstall Superwhisper and handy from
all macs". His message is the yes for both apps on all four Macs.

## Found on 2026-11-22 (read-only survey, before any change)

| Mac | superwhisper | Handy (`com.pais.handy`) | Free |
|---|---|---|---|
| Cowboy | app, cask, `Application Support/superwhisper` 978M, `~/Documents/superwhisper`, caches, prefs, HTTPStorages, WebKit | app, cask, `Application Support/com.pais.handy` 620K, caches, WebKit | 31Gi |
| Captain | cask entry (no app), leftover caches 5.5M, prefs, HTTPStorages, WebKit, `com.superduper.superwhisper` | app, cask, Application Support 248K, caches, WebKit | 31Gi |
| Bald | app, cask, `Application Support/superwhisper` 727M, caches, prefs, HTTPStorages, WebKit | app, cask, no data folders | 47Gi |
| Pirate | no app, no cask; leftover caches, prefs, HTTPStorages, WebKit | app, cask, Application Support 532K, caches, WebKit | 27Gi |

Handy's Application Support folders are under 1 MB, so it holds no
downloaded model on any Mac; the builder confirms by listing them.

## Build

Other Macs are reached over ssh from the Cowboy
(`valorengels@valor-the-captain`, `-bald`, `-pirate`). On each Mac:

1. Record `df -h /` and `du -sh` of every path above.
2. Quit both apps (`osascript -e 'quit app "…"'`, then `pkill -x` if still
   running).
3. `brew uninstall --cask --zap superwhisper handy` where the cask is
   registered; `--force` where the cask is registered but the app is gone
   (Captain's superwhisper).
4. Remove what the zap left, by name only: any path matching
   `*superwhisper*`, `com.superduper.superwhisper*`, `com.pais.handy*` or
   `Handy.app` under `/Applications`, `~/Applications`, `~/Documents`, and
   `~/Library/{Application Support,Caches,Containers,Group Containers,
   Preferences,LaunchAgents,HTTPStorages,WebKit,Saved Application State,
   Logs,Application Scripts}`; `defaults delete` for their domains so
   cfprefsd drops the cached prefs; their login items (System Events login
   items, `sfltool dumpbtm` to read background items; a GUI-only item that
   ssh cannot remove is reported, not forced).
5. Re-run the survey: nothing matches. Record `df -h /` again.
6. Confirm Yudame Tools still has its models
   (`~/Library/Application Support/YudameTools/Models/transcribe` and
   `speakers` present with their files and sizes unchanged).

Then update `docs/local-models.md`: drop superwhisper from "Kept, not
proven unused" and from the inventory command's path list; add a
"Removed 2026-11-22" entry for both apps with the measured sizes per Mac,
naming Handy's models only if step 1 found any.

## Out of scope

Any other app or model: Yudame Tools and its models, Valor's voice models,
Ollama's models (tev1:0.8b on the Cowboy stays), u2net and anything in the
doc's Required or Kept lists. No sudo; a path that needs it is reported.

## Tests

No production code changes. Run `tests/test_bloat.py` and the tests that
read docs (`tests/test_kernel.py`, `tests/test_specialist_memory.py`). The
real check is step 5 on each Mac, including the cases beyond the obvious
one: the Captain's orphan cask with no app, the Pirate's leftovers with no
app and no cask, and the Bald's Handy with no data folders.

## Stakes and loops

Deleting the wrong folder on a Mac is not undoable, so review may send the
work back once; the scope is two named apps by bundle id with no design
choices, so no critique round. No governance added.
