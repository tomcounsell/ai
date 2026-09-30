# do-design-system context — this repo (ai)

The `/do-design-system` body is generic over the `docs/designs/` convention (moodboard scrape,
`.pen` JSON editing, motif tables, gap-audit). This repo adds the **deterministic generator**
that emits downstream artifacts from the `.pen` source, the **PreToolUse safety hooks** that
guard those artifacts, and the reference implementation. Honor these for Steps 5–7.

## The generator — `tools.design_system_sync` (Steps 6 & 7)

CSS files, `design-system.md`, and DTCG / Tailwind exports are GENERATED from
`design-system.pen`. Do NOT hand-edit `brand.css`, `source.css`, or `design-system.md`.

Run the generator:

```bash
python -m tools.design_system_sync --all \
    --pen <consumer-repo>/docs/designs/design-system.pen \
    --css-root <consumer-repo>/<css-root>
```

Or rely on `design-system-sync.toml` adjacent to the `.pen`:

```bash
python -m tools.design_system_sync --all \
    --pen <consumer-repo>/docs/designs/design-system.pen
```

Verify lint exits 0:

```bash
npx --no-install @google/design.md lint \
    <consumer-repo>/docs/designs/design-system.md
```

The generator is deterministic: running twice produces byte-identical output. To force
regeneration (e.g. after a `.pen` pattern change), re-run `--all` and stage the resulting
artifacts.

**Node:** `--all` requires `node` + `npx` (for lint + exports). `--generate` alone auto-falls
back to Python-only emission when Node is absent (pass `--no-node` to be explicit). See
`docs/features/design-system-tooling.md` for the full Node-absent fallback semantics.

## Gap-audit sequence (Step 7)

Run `--audit` BEFORE `git commit`. The audit diffs against `HEAD:<pen-dir>/design-system.md`, so
`HEAD` must still hold the PRIOR pass's `design-system.md`. Running `--audit` after commit
produces an empty diff; the generator emits a stderr `--stale-warn` when it detects that case.

```bash
# 1. Regenerate every artifact from the updated .pen
python -m tools.design_system_sync --all --pen <path-to>/design-system.pen

# 2. Produce the diff table (goes to stdout)
python -m tools.design_system_sync --audit --pen <path-to>/design-system.pen

# 3. Paste the audit output into gap-audit.md under a new dated heading, THEN git add + commit.
```

## Safety gate — two layers (Step 5)

- **Layer A — inline Python assertion (agent-level, runtime).** Pins the write target to
  `design-system.pen` and refuses any other path. Scope: the `.pen` source only.
- **Layer B — `validate_design_system_readonly.py` PreToolUse hook (tool-level, runtime).**
  Registered in `.claude/settings.json` against the `Write`/`Edit` matchers. Scope: the generated
  artifacts (`design-system.md`, `brand.css`, `source.css`) — it blocks any direct Write/Edit
  against them regardless of whether this skill is active.
- Additionally, `validate_design_system_sync.py` blocks the commit if drift between the `.pen`
  and the generated artifacts is detected.

The two layers are complementary, not redundant: Layer A discriminates the correct `.pen` write
path from a wrong `.pen` write path; Layer B discriminates a write to a generated artifact from a
write to anything else. Do NOT remove either.

## Reference implementation

Commit `a702484` on `yudame/cuttlefish` main (moodboard pass, 2026-04-20):

- Moodboard source: `https://www.cosmos.so/tomcounsell/yudame-research`
- Files changed: `docs/designs/pencil-design-system.pen` (legacy name, now `design-system.pen`),
  `static/css/brand.css`, `static/css/source.css`, `docs/designs/pencil-design-gap-audit.md`
  (legacy name, now `gap-audit.md`), `docs/designs/inspiration/` (19 images, flat — now would be
  `inspiration/2026-04-20-research-editorial/`).
- Shape: 3 variable edits + 5 new components, no renames, no deletions.
