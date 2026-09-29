# do-design-system context — this repo (ai)

This repo adds a deterministic generator for downstream artifacts, hooks guarding them, and a reference pass. Honor these for Steps 5-7.

## The generator — `tools.design_system_sync` (Steps 6 and 7)

`brand.css`, `source.css`, `design-system.md`, and the DTCG / Tailwind exports are generated from `design-system.pen`; never hand-edit them.

```bash
python -m tools.design_system_sync --all \
    --pen <consumer-repo>/docs/designs/design-system.pen \
    --css-root <consumer-repo>/<css-root>   # optional when design-system-sync.toml sits next to the .pen
npx --no-install @google/design.md lint <consumer-repo>/docs/designs/design-system.md   # must exit 0
```

Output is byte-identical across runs; to force regeneration, re-run `--all` and stage the results. `--all` needs `node` and `npx`; `--generate` alone falls back to Python-only emission without Node (`--no-node` makes it explicit). See `docs/features/design-system-tooling.md`.

## Gap-audit ordering (Step 7)

Run `python -m tools.design_system_sync --audit --pen <path>/design-system.pen` after `--all` and BEFORE `git commit`, then paste its stdout into `gap-audit.md`. The audit diffs against `HEAD:<pen-dir>/design-system.md`, so after the commit it produces an empty diff.

## Hooks (Step 5)

- `validate_design_system_readonly.py` (PreToolUse on Write/Edit) blocks direct writes to the generated artifacts whether or not this skill is active.
- `validate_design_system_sync.py` blocks a commit when the generated artifacts drift from the `.pen`.

Both complement the skill's inline `.pen` assertion; keep all of them.

## Reference pass

Commit `a702484` on `yudame/cuttlefish` main: moodboard `https://www.cosmos.so/tomcounsell/yudame-research`; 3 variable edits and 5 new components, no renames or deletions; touched the `.pen`, `static/css/brand.css`, `static/css/source.css`, the gap audit, and `docs/designs/inspiration/`.
