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
3. `launchctl bootout gui/$(id -u)/<label>` for `~/Library/LaunchAgents/Handy.plist`
   if loaded, then plain `brew uninstall --cask superwhisper handy` where the
   cask is registered (`--force` where the app is gone: Captain's
   superwhisper). Not `--zap`: both casks' zap stanzas are `trash:`, which
   moves the models to `~/.Trash` and frees nothing.
4. `rm -rf` each exact path, taken from the casks' zap lists plus what the
   survey found:
   - superwhisper: `~/Documents/superwhisper`, `~/Library/Application
     Support/superwhisper`, `~/Library/Application Support/com.superduper.superwhisper`,
     `~/Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments/com.superduper.superwhisper.sfl*`,
     and `com.superduper.superwhisper` under `~/Library/{Caches,HTTPStorages,
     WebKit}`, `Preferences/com.superduper.superwhisper.plist`,
     `Saved Application State/com.superduper.superwhisper.savedState`.
   - Handy: `com.pais.handy` under `~/Library/{Application Support,Caches,
     Logs,WebKit}`, `~/Library/LaunchAgents/Handy.plist`, and either app
     bundle left in `/Applications` or `~/Applications`.
   - Containers and Group Containers: only an entry named for either bundle
     id; a deletion refused there over ssh (no Full Disk Access) is reported
     as could-not-remove, not retried.
   - `~/Documents` is checked at the top level only (`-maxdepth 1`), never
     inside the user's project folders.
   - `~/.Trash`: only items whose names contain superwhisper, Handy or the
     two bundle ids; nothing else in the Trash is touched.
   - `defaults delete` both domains so cfprefsd drops cached prefs; login
     items named for either app removed (`sfltool dumpbtm` to read them; a
     GUI-only item ssh cannot remove is reported, not forced).
5. Re-run the survey, now also matching `Handy.plist` and `~/.Trash`:
   nothing matches, and `df` shows the space freed. Record `df -h /` again.
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
