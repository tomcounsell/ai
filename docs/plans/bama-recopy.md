# Re-copy BAMA, and say when the copy is old

Tom, valor team chat, 2026-11-03 04:52 UTC, on the vendored BAMA at 9b98671
(2026-10-27) while upstream is 9 commits ahead: "You can recopy whenever you're
building Mac apps and haven't updated in a while. I haven't noticed any hiccups or
problems with the build; I just don't want it to drift too long."

Governance: none is added. The rule is a sentence in the agent's prompt; nothing
checks, blocks or schedules anything.

## What is built

1. **Re-copy at upstream HEAD** 639893c397278256f1ae204dd8f837eb82bd6527
   (2026-11-01). Replace `skills/valor/bama/{skills,specs,LICENSE}` with upstream's,
   verbatim (delete then copy, so a file upstream removed goes too). Upstream since
   9b98671, in the copied paths: `start/bama` drops its Voice tip (the Glaido line);
   `new-app.sh` keeps going in a folder that already has files (rsync, not `cp -n`,
   which exits 1 on macOS under `set -e`); `agent-cli`'s template finds the app
   binary when its CLI runs from PATH (`AgentBinary.url`); `specs/launcher.md` and
   `specs/screenshots.md` rewritten from upstream's Onyx app. Still 21 skills.
2. **`skills/valor/agents/mac-app.md`**: the pin becomes 639893c…; the bullet
   "Skip the Voice tip in `bama`: no Glaido line goes to anyone." goes, since upstream
   has no Voice tip. Re-read the rest of the prompt against the new text (skill list,
   `window-snapper.md`, `build.sh` signing, `--disable-sandbox`) and fix any line that
   no longer holds. Add, after the first paragraph, in its voice:
   "The copy was taken on 2026-11-03. When it is over about a month old as a build
   starts, say so in your report, so the session that started you starts a task on
   tomcounsell/ai to re-copy it."
3. **`skills/README.md` Imports, `valor/bama/`**: the new pin and date, and the
   closing sentence becomes: a newer BAMA is a task re-copying at a new pin; when the
   copy is over about a month old as a build starts, `valor:mac-app` says so in its
   report and the session that started it starts that task (Tom, 2026-11-03).

## Out of scope

A scheduled sync, a routine, or any check of the copy's age (Tom said re-copy while
building; a scheduled one is what he was offered and declined). Re-building Brew
Status or other apps already made from the old template. Editing
`docs/plans/mac-app.md`, which records the first copy as it was.

## Tests

- `tests/test_orchestration.py::test_the_mac_app_agent_alone_reads_bamas_skills`
  still passes on the new copy: 21 vendored skills, each named in the prompt, the
  MIT notice, `window-snapper.md` present, none listed to a session. Add to it: the
  40-hex pin in the agent prompt equals the one in `skills/README.md`, so a later
  re-copy that updates one and not the other fails.
- Fidelity, by hand: `diff -r` of a fresh clone at 639893c's `skills/`, `specs/`,
  `LICENSE` against `skills/valor/bama/` is empty; `grep -ri glaido skills/valor`
  finds nothing.
- Full suite, under 5 minutes.

Stakes: wording and a vendored copy only read by one agent; one revert undoes it.
Critique 0, review 0.
