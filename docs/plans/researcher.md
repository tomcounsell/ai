# Researcher: the yudame plugin pulled in, and `valor:researcher`

Task d72d19658361. Tom, valor team chat on Slack, thread
#accidentally-asked-via, 2026-11-01:

- 16:39 UTC (1793551196.951399): "If that skills repo already pushed it, go
  ahead and pull it in as an update to the plugin and try using it."
- 16:43 UTC (1793551410.328599), agreeing that the researcher become Valor's
  own agent: "Yeah, I think that makes sense. Your own researcher can be much
  more powerful and comprehensive and it can also be improved with our RSI
  program. I'll have the Yudmae skills ship a paired down, simpler version
  for general availability." (Delivered to this task as steering.)

Done when `valor:researcher` has run for real from a turn on every Mac it is
rolled out to, at least the Cowboy (rule 5, 6).

## Found (2026-11-01)

- Turns load the yudame plugins from `~/.claude/plugins/marketplaces/yudame`
  (`settings.plugin_dirs`); a turn's own config dir is
  `work_state/claude` (`core/workspace.py:818`).
- The vault's nightly job (`com.valor.brew-nightly`, 04:00,
  `~/.local/bin/brew-nightly-update.sh`) already runs
  `claude plugin marketplace update yudame` and `claude plugin update` for
  both plugins; its log shows it succeeding on all four Macs through
  2026-11-01 04:04. The clone sat at 92bdba3 because yudame/skills had no
  commit between 92bdba3 (10-27) and 6a5c0d5 (11-01 19:31 +0700); the
  researcher landed after the last nightly. No defect; nothing to fix in code.
- The yudame researcher's keys already point at Valor's vault (`m-valor`).

## Build

1. **Pull now.** On the Cowboy, Pirate, Captain and Bald (ssh), run the
   vault script's plugin lines: `claude plugin marketplace update yudame`,
   then `claude plugin update yudame@yudame` and `yudame-dev@yudame`.
   Confirm each clone is at or past c906f00 with
   `plugins/yudame/agents/researcher.md`.
2. **Docs.** `skills/README.md:10`, `docs/harnesses.md:94` and the
   `core/settings.py` `plugin_dirs` comment name the vault's nightly job as
   what keeps the clone current, not Claude Code's autoUpdate.
3. **`valor:researcher`.** Copy yudame/skills' research skill verbatim at
   c906f00ea (`run.py`, `keys.env`, `SKILL.md`) to
   `skills/valor/skills/deep-research/` (named `deep-research`, not `research`: BAMA's `plan/research` has that name inside `valor:mac-app`), and its agent to
   `skills/valor/agents/researcher.md`, edited only to: name the runner
   `${CLAUDE_PLUGIN_ROOT}/skills/deep-research/run.py` (yudame's agent searches
   `~/.claude/plugins/cache`, which turns do not load); pass
   `--out ~/Desktop/Valor/research/<date>-<slug>` so every run's report,
   `raw.json` and Method line (time, cost, tools skipped) is kept and
   reaches every Mac; and say in its description that it is the one to
   start in Valor's turns (runner under the loaded plugin, reports kept),
   since `yudame:researcher` is listed beside it until yudame/skills pares
   its version down. `skills/README.md` Imports names the pin. Port
   yudame's `tests/check_research.py` (stand-ins, no network) as
   `tests/test_research.py`.
4. **RSI fit.** One paragraph in `docs/plans/rsi.md`: the researcher is an
   ability RSI improves by ordinary tasks on this repo, and the kept runs
   are the comparative evidence rsi.md requires (same question, prior
   runner vs changed: cost, time, claims with sources). No check, gate or
   score is added.
5. **Live trial, the Cowboy, pre-merge, in a turn's environment.** From
   this task's turn (its config dir, sandbox and `OP_SERVICE_ACCOUNT_TOKEN`,
   not the operator's shell), a fresh `claude -p` with the kernel's plugin
   dirs lists `yudame:researcher`; one with `--plugin-dir skills/valor`
   first lists `valor:researcher` and runs it on "What are current best
   practices for running a product backlog in a Google Sheet?" (no
   podcast). Key resolution is checked in that same environment. Report
   which of the six keys resolve (names only), summary, cost, time, tools
   skipped, anything broken.
6. **After merge.** The normal update rolls it to every Mac; on each, a
   fresh turn lists `valor:researcher`. A full run on the Pirate, Captain
   and Bald is a few dollars and 25-45 minutes each; any not run is named
   open in the delivery.

## Out of scope

Any change to the update routine or the nightly job. Slimming the yudame
plugin's researcher, and its cache-path lookup (yudame/skills work for
#yudame-dev, named as the follow-up). Podcast episodes. Routing research cost
through the gateway (`run.py` drops `ANTHROPIC_BASE_URL`; the report states
its own cost).

## Tests

- `tests/test_research.py`: the ported stand-in checks (call order and
  arguments into Cuttlefish, report layout, a missing key skips its tool and
  is named, a key in the environment wins over `keys.env`).
- `tests/test_orchestration.py`: `valor:researcher`'s prompt names its
  runner under `${CLAUDE_PLUGIN_ROOT}` and that file exists; `keys.env`
  holds the six key names, all `op://m-valor/` references.
- Suite stays under 5 minutes.

Governance: none added. No grant needed.
