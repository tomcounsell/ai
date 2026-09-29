---
name: officecli
description: "Create, inspect, proofread, and edit Office files (.docx, .xlsx, .pptx) via officecli. Use for creating, checking formatting, finding issues, adding charts, or editing."
argument-hint: "[document path] [task]"
---

# officecli

Create or change .docx, .xlsx, and .pptx files with `officecli` (single binary, no Office
install needed). **Done when** `officecli validate <file>` passes, `officecli view <file>
issues` shows nothing new, a render looks right (`view <file> html`; `view <file> svg` for
decks), and the file is flushed to disk (see Resident mode) before anything else reads it.

Prefer the highest layer that can express the change: L1 read (`view`, `get`, `query`) →
L2 DOM edit (`set`, `add`, `remove`, `move`, `batch`) → L3 raw XML (`raw`, `raw-set`,
`add-part`). Add `--json` for structured output. Full syntax, selectors, value formats,
element types, and examples: `references/commands.md`.

## Pinned binary

Run `officecli --version` first. The fleet pins **v1.0.29** (`PINNED_VERSION` in
`scripts/update/officecli.py`), which lacks: `help`, `swap`, `mark`, `unmark`,
`get-marks`, `dump`, `refresh`, `goto`, `load_skill`, `save`; the `find=` prop on `set`
(`UNSUPPORTED props: find`); and the `screenshot` view mode (`svg` works for .pptx only).
It has `create`, `view`, `get`, `query`, `set`, `add`, `remove`, `move`, `raw`,
`raw-set`, `add-part`, `validate`, `check`, `batch`, `import`, `merge`, `open`/`close`,
`watch`/`unwatch`. Without `help`, discover schemas with `officecli <cmd> --help` and
`officecli pptx set shape`-style navigation.

To upgrade, bump `PINNED_VERSION` and run `/update`. Never use the upstream curl installer
on a fleet machine: `/update` reverts an unpinned upgrade.

## Resident mode

Every command auto-starts a resident process that holds the file in memory; use explicit
`open`/`close` for long sessions. officecli's own reads see in-memory edits, but other
programs see the pre-edit file until `close` (or `save` on newer releases) or the ~10s
idle auto-flush. Opt out with `OFFICECLI_NO_AUTO_RESIDENT=1`. Close the file in
PowerPoint/WPS before modifying it.

## Pitfalls

| Pitfall | Correct approach |
|---------|-----------------|
| `--name "foo"` | All attributes go through `--prop name="foo"` |
| Unquoted `[N]` paths | Quote them: `'/slide[1]'` (shells glob-expand brackets) |
| PPT `shape[1]` for content | `shape[1]` is usually the title placeholder; content starts at `shape[2]` |
| `/shape[myname]` | Name indexing unsupported; use a numeric index or `@name=` (PPT only) |
| `\n` in shell strings | Use `\\n` in `--prop text="..."` |
| `$` in shell text | `--prop text="$15M"` loses `$15`; single-quote it or use a batch heredoc |

Paths are 1-based (`'/body/p[3]'` is the third paragraph); `--index` is 0-based, except
Excel `add --type row`/`col`, where `--index N` is 1-based. Prefer stable-ID paths
(`shape[@id=...]`, `p[@paraId=...]`) in multi-step edits: positional indices shift on
insert and delete.
