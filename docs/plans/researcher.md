# Researcher: the yudame plugin current on every Mac, and `valor:researcher`

Task d72d19658361. Tom, 2026-11-01 (valor team chat, 16:39 UTC): pull the
yudame plugin's new researcher in and try it; 16:43 UTC: make it Valor's own
agent, improved by the RSI program, while yudame/skills ships a pared-down
version. Done when `valor:researcher` has run for real from a turn on every
Mac it is rolled out to, at least the Cowboy (rule 5, 6).

## Found (2026-11-01)

- Turns load the yudame plugins from `~/.claude/plugins/marketplaces/yudame`
  (`settings.plugin_dirs`). On the Cowboy, Pirate, Captain and Bald that
  clone is at 92bdba3, cloned 2026-10-27 and never moved; main is c906f00.
- Cause: the docs say the marketplace's `autoUpdate` keeps it current, but
  Claude Code runs that only in an interactive session on that config dir.
  Every Valor turn runs with its own `CLAUDE_CONFIG_DIR` (`core/call.py`),
  so nothing ever pulls `~/.claude`'s clone. Measured:
  `claude plugin marketplace update yudame` on a stale copy (own config
  dir) moved it to c906f00.
- The yudame researcher's keys already point at Valor's vault (`m-valor`).

## Build

1. **Bug fix (commit with a test).** The 03:00 update routine
   (`update_runner`, `core/__main__.py`), beside its `uv cache prune`, runs
   `<settings.claude> plugin marketplace update` (all of the operator's
   marketplaces) and puts its last line in the routine's summary, failing
   soft as the prune does. Docs that say "kept current by the marketplace"
   (`skills/README.md`, `docs/harnesses.md`, `core/settings.py` comment,
   `docs/routines.md`) say the 03:00 update pulls it. Then run the same
   command now on all four Macs (ssh), and confirm each clone is at or past
   c906f00 with `plugins/yudame/agents/researcher.md`.
2. **`valor:researcher`.** Copy yudame/skills' research skill verbatim at
   c906f00ea (`run.py`, `keys.env`, `SKILL.md`) to
   `skills/valor/skills/research/`, and its agent to
   `skills/valor/agents/researcher.md`, edited only to name the runner as
   `${CLAUDE_PLUGIN_ROOT}/skills/research/run.py` (the yudame agent searches
   `~/.claude/plugins/cache`, which turns do not load) and to pass
   `--out ~/Desktop/Valor/research/<date>-<slug>`, so every run's report,
   `raw.json` and Method line (time, cost, tools skipped) is kept and
   reaches every Mac. `skills/README.md` Imports names the pin; a newer
   runner is a task re-copying or improving it here. Port yudame's
   `tests/check_research.py` (stand-ins, no network) as
   `tests/test_research.py`.
3. **RSI fit.** One paragraph in `docs/plans/rsi.md`'s abilities: the
   researcher is an ability RSI may improve by ordinary tasks on this
   repo; the kept runs are the comparative evidence rsi.md requires (same
   question, prior runner vs changed, cost, time, claims with sources).
   No check, gate or score is added.
4. **Live trial, the Cowboy, pre-merge.** A fresh `claude -p` turn with
   the kernel's plugin dirs lists `yudame:researcher`; one with
   `--plugin-dir skills/valor` from this workspace lists `valor:researcher`
   and runs it on "What are current best practices for running a product
   backlog in a Google Sheet?" (no podcast). Report which of the six keys
   resolve (names only, never values), summary, cost, time, tools skipped,
   anything broken.
5. **After merge.** The normal update rolls it to every Mac; on each, a
   fresh turn lists `valor:researcher` and its keys resolve. A full run
   on the Pirate, Captain and Bald is a few dollars and 25-45 minutes
   each; whatever is not run is named open in the delivery.

## Out of scope

Stripping or slimming the yudame plugin's researcher (yudame/skills work for
#yudame-dev, named as the follow-up, with the cache-path lookup bug);
podcast episodes; routing research cost through the gateway (`run.py` drops
`ANTHROPIC_BASE_URL`; the report states its own cost).

## Tests

- `tests/test_rollout.py`: the update routine runs `claude plugin
  marketplace update` once per run beside the prune (fake `claude`
  recording argv); its failure lands in the summary and the routine
  still requests the update.
- `tests/test_research.py`: the ported stand-in checks (call order and
  arguments into Cuttlefish, report layout, a missing key skips its tool
  and is named, a key in the environment wins over `keys.env`).
- `tests/test_orchestration.py`: `valor:researcher`'s prompt names its
  runner under `${CLAUDE_PLUGIN_ROOT}` and that file exists; `keys.env`
  holds the six key names, all `op://m-valor/` references.
- Suite stays under 5 minutes.

Governance: none added. No grant needed.
