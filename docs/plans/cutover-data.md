---
tracking: none
type: plan
status: draft
---

# Cutover data

What the old system holds that the new kernel needs on its first day, where
each item lives, where it goes in the new system, and how it gets there.
Read from the old system's `main` branch, its live files in `~/src/ai` and
`~/Desktop/Valor` (the iCloud-synced "vault" folder), and its Redis on port
6379 (read only). Read on the new side: `projects/*.toml`, `core/settings.py`,
`docs/data.md`, `docs/bridges/`, `python -m core --help`.

This file names secrets by where they live. It carries no value.

## How data reaches the new system

Four ways exist:

| Way | What it is |
|---|---|
| Existing command | A `python -m core ...` or `python -m bridges.* ...` command that already writes or copies the data |
| Edit a file | A project spec in `projects/`, or an environment variable in a launchd job |
| Import task | A one-off script run once, reading the old side and writing the new side. None is written yet; each is named below |
| By hand | A person does it on the Mac (login, grant, copy a file) |

The new kernel keeps no copy of the old system's configuration. It reads
project specs from `projects/` (setting `projects_dir`) on each wake, and its
settings from `VALOR_*` environment variables read when a process starts
(`core/settings.py`). So most of what a cutover moves is a few files and a
few environment lines, not database rows.

## 1. The fleet and chat ownership

### 1.1 The Macs

The old system's roster (`machines` in `~/Desktop/Valor/projects.json`) has
four Macs, all peers. Each runs its own bridge and worker and owns the
projects whose `machine` field matches its ComputerName.

| Mac (ComputerName) | Tailscale state seen from the build Mac | Projects it owns in the old system |
|---|---|---|
| Valor the Cowboy | this Mac, online | valor, popoto |
| Valor the Captain | not in the Tailscale list | cuttlefish, psyoptimal, royop |
| Valor the Bald | offline, last seen 138 days ago | cyndra |
| Valor the Pirate | online | mondayflowers, gato-os, counsell-home, yudame |
| unassigned (paused) | none | satsol |

The new system runs one kernel and one ledger per Mac. Only the Cowboy has a
kernel, a ledger (`valor_rebuild`), and the key directory
(`~/.config/valor-kernel`) today. A project spec binds its chats to one Mac
with `machine`, and a Mac's bridge receives only the chats its specs list for
that Mac (`core/intake.py`, `owned`).

How the machine name matches: the old system compared `machine` with
`scutil --get ComputerName`. The new kernel compares a spec's `machine` with
the setting `machine`, which is `VALOR_MACHINE` or else the short hostname.
On the Cowboy the hostname is `Mac` and `projects/valor.toml` says
`machine = "Mac"`. Gap G3 covers this.

### 1.2 Telegram groups, by project

Chat ids are the same Telethon ids in both systems (`str(msg.chat_id)`), so
they copy across unchanged. In the new system a spec lists them as
`chats = ["telegram:<id>"]`.

| Project | Group (old name) | Chat id | Old persona | Old owner Mac |
|---|---|---|---|---|
| valor | Eng: Valor | -1003449100931 | engineer | Cowboy |
| valor | Agent Builders Chat | -1003879986445 | teammate | Cowboy |
| valor | Perplexity Build Comp | -1003936769817 | teammate | Cowboy |
| popoto | Eng: Popoto | -5189826365 | engineer | Cowboy |
| cuttlefish | Eng: Cuttlefish | -1003801797780 | engineer | Captain |
| psyoptimal | Eng: PsyOPTIMAL | -1003743854645 | engineer | Captain |
| psyoptimal | PsyOPTIMAL | -1002600253717 | teammate | Captain |
| cyndra | Eng: Cyndra Consulting | -1003900483201 | engineer | Bald |
| cyndra | Cyndra Dev Team | -1003794218389 | teammate | Bald |
| cyndra | Cyndra Devs | -1004385743413 | teammate | Bald |
| royop | none | none | none | Captain |
| mondayflowers | Eng: Monday Flowers | -5302491300 | engineer | Pirate |
| gato-os | Eng: Gato | -1003513124705 | engineer | Pirate |
| counsell-home | Eng: Counsell Home | -5174105183 | engineer | Pirate |
| yudame | Yudame | -4719889199 | teammate | Pirate |
| satsol (paused) | Eng: SATSOL | -5136818964 | engineer | unassigned |
| satsol (paused) | SATSOL | -5095889193 | teammate | unassigned |

The new system has one operator group already: "Valor rebuild",
`-1003890616618`, listed in `projects/valor.toml` and set as the operator
chat in the kernel's launchd job.

Where it goes: a `chats` line in each project's spec in `projects/`.
How: edit a file (section 2 gives the specs). The new system has no persona
per chat; the persona is one set of files in `persona/`, rendered into every
turn, so the "engineer", "teammate", and "customer-service" labels have no
column in the new system (gap G1).

### 1.3 Direct messages

The old system's `dms.whitelist` lists fourteen Telegram users, each tied to
a project. Only Tom's message ever starts, answers, steers, approves, or
stops a task in the new system; everything else is recorded and bound as
`none` (`core/intake.py`, `_from_operator`).

| User id | Name | Old project | Meaning in the new system |
|---|---|---|---|
| 179144806 | Tom | valor | The operator. Set as `VALOR_OPERATOR_TELEGRAM_ID` |
| 577036901 | Kevin | valor | Recorded, never acted on |
| 2106858278 | Matt Rideout | valor | Same |
| 7954357506 | Nick Frith | valor | Same |
| 2019570687 | Andy Malkin | valor | Same |
| 7820562338 | Lewis Parrott | psyoptimal | Same |
| 437868058 | Graham D | cuttlefish | Same |
| 469027377 | Mike Berson | gato-os | Same |
| 7312372670 | Charlie | cyndra | Same |
| 8209004019 | Colin Behring | cyndra | Same |
| 8762685703 | Thabiso Epema | cyndra | Same |
| 6084902164 | Johann | cyndra | Same |
| 7388810030 | Jess Mason | cyndra | Same |
| 7178632018 | Hazem | cyndra | Same |

A DM is a chat whose id is the user's id. The new bridge receives a DM only
if that id is in a spec's `chats` or is the operator chat. Gap G2 covers
Tom's own DM; gap G1 covers the other thirteen.

### 1.4 Email contacts and domains

| Project | Old email contacts | Old email domains | Old persona |
|---|---|---|---|
| cuttlefish | tom@yuda.me, nav@krystallabs.io, gcason2@gmail.com, grahamhderry@gmail.com | none | customer-service |
| psyoptimal | none | psyoptimal.com, psyoptimalsports.com | teammate |
| cyndra | charlie@yuda.me | cyndra.ai (also trusted) | teammate |
| gato-os | none | chainstarters.com | teammate |

In the new system a spec lists `email:<exact address>` only, with no domain
form, and no email message ever binds to anything (`docs/bridges/email.md`,
"Nothing binds"). The mailbox bridge records mail from Tom's addresses and
from the addresses a spec lists. Gap G1 covers this.

## 2. Projects

Eleven projects in `projects.json`; the new system has one spec,
`projects/valor.toml`. A spec needs `name`, `repo`, `kind`
(`python-uv`, `django`, `node`, `plain`), and `suite`. The suite is the
kernel's command and never the candidate's. The `branch` and `merge_url`
fields decide where a merge lands.

| Project | Old GitHub repo | Old kind of work | Spec in `projects/` | How it gets there |
|---|---|---|---|---|
| valor | tomcounsell/ai | Python, Claude Agent SDK | Exists: `valor.toml` (branch is the rebuild branch, chat is the operator group) | Edit a file at cutover (G6) |
| popoto | tomcounsell/popoto | Python, Redis ORM | None | Edit a file. Draft below |
| psyoptimal | yudame/psyoptimal | Django, PostgreSQL | None | Edit a file. Draft below |
| cuttlefish | yudame/cuttlefish | Django, Python, MCP | None | Edit a file. Draft below, suite unproven |
| cyndra | Cyndra-AI/cyndra-consulting | TypeScript, Bun monorepo | None | Edit a file, kind `node`, suite not known |
| royop | yudame/royop | Python (Django layout) | None | Edit a file, suite not known |
| mondayflowers | yudame/mondayflowers | Django, HTMX | None | Edit a file, suite not known |
| gato-os | chainstarters/gato-os | Rust, Nix | None | Edit a file; `kind` has no Rust value, so `plain` |
| counsell-home | tomcounsell/counsell-home | Kotlin, Flutter | None | Edit a file, kind `plain` |
| yudame | yudame/yuda.me | Python web, Tailwind | None | Edit a file |
| satsol | yudame/satsol | Python | None | Paused; no spec until Tom unpauses |

The old `working_directory` (for example `~/src/popoto`) has no field in the
new spec. The kernel clones from `repo` into `work_dir`
(`~/valor-tasks`) for every task, so the local checkout is not read.
`knowledge_base` (the `~/work-vault/<name>` folder), `linear.team`, and
`context.tech_stack` have no field either (gap G9).

### 2.1 Draft specs for the three projects Tom's backlog names

Taken from the emulator's run specs in `~/src/valor-demo/runs/*/project.toml`
(`pop-b-gate`, `pso-a-gate-c`), which the takeover gate ran. They are drafts
until a task runs under each. The cuttlefish suite comes from its `Makefile`
(`test` target runs `pytest` with `DJANGO_SETTINGS_MODULE=settings`) and has
not run in the kernel.

```toml
# projects/popoto.toml
name = "popoto"
repo = "https://github.com/tomcounsell/popoto.git"
kind = "python-uv"
branch = "main"
merge_url = "https://github.com/tomcounsell/popoto.git"
services = ["redis"]
setup = ["uv sync --frozen --extra dev"]
suite = "uv run pytest -p no:cacheprovider -q -m 'not slow and not benchmark' --junitxml={junit} tests"
chats = ["telegram:-5189826365"]
machine = "Mac"
[env]
UV_PYTHON = "3.12"
```

```toml
# projects/psyoptimal.toml
name = "psyoptimal"
repo = "https://github.com/yudame/psyoptimal.git"
kind = "django"
branch = "prod"
merge_url = "https://github.com/yudame/psyoptimal.git"
services = ["postgres"]
setup = ["PATH=\"$PATH:/opt/homebrew/opt/postgresql@18/bin\" SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk uv sync --frozen"]
suite = "uv run pytest -p no:cacheprovider -q --junitxml={junit} apps/team apps/public apps/api"
chats = ["telegram:-1003743854645"]
machine = "Mac"
[env]
UV_PYTHON = "3.12"
DJANGO_SETTINGS_MODULE = "settings.test"
```

The local psyoptimal checkout is on branch `prod`, the emulator spec used
`main`. Which branch merges land on is Tom's to confirm (question Q1).
The cuttlefish spec follows the same shape: `kind = "django"`,
`services = ["postgres", "redis"]` (its `pyproject.toml` lists
`django-redis`), `chats = ["telegram:-1003801797780"]`, suite from the
`Makefile` test target. Its setup and branch are unconfirmed.

The `machine` value in these drafts is `"Mac"` because that is what the
Cowboy's setting says today. Gap G3 recommends changing it everywhere
together.

## 3. Operator identity

| Item | Where it lives in the old system | Where it goes | How |
|---|---|---|---|
| Tom's Telegram user id, 179144806 | `dms.whitelist` in `projects.json`; hard-coded as the default DM target in `tools/agent_session_scheduler.py` | Setting `operator_telegram_id`, env `VALOR_OPERATOR_TELEGRAM_ID` | Already set in the Cowboy's `com.valor.kernel` and `com.valor.kernel.telegram` launchd jobs |
| Tom's email, tom@yuda.me | Listed as a cuttlefish contact in `projects.json`; no operator field | Setting `operator_email`, env `VALOR_OPERATOR_EMAIL` (comma list) | Already set in the Cowboy's `com.valor.kernel` and `com.valor.email` jobs |
| Operator chat (where notices and approvals go) | None. The old system answered where it was asked | Setting `operator_chat`, env `VALOR_OPERATOR_CHAT`, with `operator_channel` `telegram` | Already set to `-1003890616618` in the Cowboy's jobs |
| Valor's own identity (name, email, timezone, handles) | `config/identity.json` in the repo: Valor Engels, valor@yuda.me, UTC+7, @valorengels | `persona/identity.toml` (same fields, plus supervisor Tom Counsell) | Exists in the repo; nothing to move |
| Valor's Telegram account | `valor_bridge.session` in `~/src/ai/data`, API id and hash in the vault `.env` | `~/.config/valor-kernel/telegram.session` and `telegram-keys` | Exists on the Cowboy. A second login for the same account, never a copy of the old session file (the old data directory keeps a `.dead-authkeydup` session, the failure a copy causes). Other Macs: `python -m bridges.telegram keys` then `login` |
| Valor's mailbox, valor@yuda.me | Vault `.env`: `IMAP_USER`, `IMAP_PASSWORD`, `SMTP_USER`, `SMTP_PASSWORD`, hosts and ports | `~/.config/valor-kernel/mail-keys`; settings `email_address`, `imap_host`, `smtp_host` | Exists on the Cowboy. Other Macs: `python -m bridges.email keys` |
| Mailbox start date | None | Setting `email_since`, env `VALOR_EMAIL_SINCE` (set to 2026-10-08 in the email job) | Edit the job at cutover (G7) |
| Principal context (`config/PRINCIPAL.md`) | The file does not exist in the old checkout or the vault | The operator record in `memory/` (milestone 6, not built) | Nothing to import. Tom's preferences the old system learned sit in its Memory records (section 7) |

Who Tom is in the new system is exactly two things: a verified Telegram
sender whose id equals `operator_telegram_id`, and the local chat page. His
email never acts (the `From` header is a claim anyone can write).

## 4. Standing settings

New settings are environment variables, read when a process starts. A
launchd job carries the ones it needs (`--plist` prints the job with them).
Values on the Cowboy today come from `python -m core settings`.

| Setting | Old system's equivalent | New value or source | How |
|---|---|---|---|
| Database name and roles | Redis `REDIS_URL`, port 6379 | Postgres `valor_rebuild`, roles `valor_kernel` and the owner; password file `~/.config/valor-kernel/pgpass` | Existing: `python -m core migrate`, `secure-login`. Done on the Cowboy |
| Postgres binaries and data directory | Homebrew Redis and Postgres, both running | `VALOR_PG_BIN`, `VALOR_PG_DATA` | Defaults match the Cowboy; other Macs by hand |
| Model credential | `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` in the vault `.env` | `~/.config/valor-kernel/claude-token` | Exists on the Cowboy. Other Macs by hand (1Password item `CLAUDE_CODE_OAUTH_TOKEN`) |
| Judgement keys | `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` in the vault | `~/.config/valor-kernel/judgement-keys` | Existing: `python -m core judgement-keys` |
| OpenAI key | `OPENAI_API_KEY` in the vault | `~/.config/valor-kernel/openai-key` | Existing: `python -m core openai-key` |
| GitHub push credential | `GITHUB_PUSH_TOKEN` (and `GITHUB_PAT`, `SDLC_AGENT_GH_TOKEN`) in the vault | `~/.config/valor-kernel/github-keys` | Existing: `python -m core github-key` |
| Backup folder | none | `VALOR_BACKUP_DIR` on the external disk | Existing: `python -m core backup`, `backup --plist`. Done on the Cowboy |
| Local chat page port | none | `VALOR_LOCAL_PORT` 8711 | Default |
| Status page port | `SERVER_PORT` (unused by the bridge) | `VALOR_UI_PORT` 8790 | Default |
| Kernel wake period | `IMAP_POLL_INTERVAL` 30 seconds and per-reflection intervals | `VALOR_SERVE_TICK_S` 60 | Default; no change needed |
| Billing mode | `USE_API_BILLING=false` (subscription) | Metered by the gateway on every call | Nothing to move |
| Machine name | ComputerName | `VALOR_MACHINE` | Gap G3 |

The old vault `.env` holds about ninety names. The new kernel reads these,
each through a copy command except the last: `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, `IMAP_USER`, `IMAP_PASSWORD`, `SMTP_USER`,
`SMTP_PASSWORD`, `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`,
`GITHUB_PUSH_TOKEN`, and `CLAUDE_CODE_OAUTH_TOKEN` (copied by hand into
`claude-token`). Every other name (Notion, Linear, Sentry, Stripe, Render,
Supabase, Cloudflare, Gemini, Perplexity, Grok, ElevenLabs, DeepL, Voyage,
Tavily, Firecrawl, Headscale, the 1Password service account, and the rest)
has no home in the new system, by design: a turn's sandbox denies the vault
and the key directory. Gap G10.

## 5. Merge permission

| Item | Old system | New system | How |
|---|---|---|---|
| Where a merge may land | The old pipeline pushed with Valor's token to each repo's default branch; GitHub ruleset 24370170 on `tomcounsell/ai` `main` restricts updates but is disabled until takeover | One grant per (URL, branch) pair, written to the `merge_targets` stream; `start` refuses a pair without one | Existing: `python -m core merge-target add URL BRANCH --note TEXT`. List with `merge-target list` |
| Pairs the day-one projects need | none | `https://github.com/tomcounsell/ai.git` branch `main` (at cutover), and for each spec with a `merge_url` its repo URL and branch | Existing command, once per pair (question Q2) |
| Valor's write access to each repo | `valorengels` is a collaborator with push on the repos it works | Same account, same token | By hand: check push access on each repo before its spec is added |
| Ruleset 24370170 | Disabled | Set to active at cutover | By hand, Tom's `gh` login (admin only) |

## 6. Recurring work

The old scheduler (`reflections.yaml`, forty-one entries, all enabled)
runs inside the old worker. The new system runs routines
(`routines/<name>/routine.toml`) as launchd jobs; two exist.

| Old reflection or job | New home |
|---|---|
| Session and Redis housekeeping: stall-advisory, agent-session-cleanup, redis-index-cleanup, redis-ttl-cleanup, redis-quality-audit, circuit-health-gate, session-count-throttle, session-recovery-drip, crash-recovery, side-effect-drain, dead-letter-replay, expectation-reconciler, failure-loop-detector | None needed: the ledger has no Redis indexes, and the kernel recovers a stopped turn itself |
| Improvement loop: improvement-evidence-collect, controller-tick, intent-reconcile, planner-tick, assumption-digest, session-intelligence | None. The `emulator` routine is the new system's measurement |
| Memory upkeep: memory-dedup, decay-prune, quality-audit, embedding-backfill, outcome-resolve, distill-backfill, embedding-orphan-sweep | Wait for milestone 6 (memory). Gap G8 |
| Repo upkeep: stale-branch-cleanup, merged-branch-cleanup, do-docs-branch-sweeper, tech-debt-scan, docs-auditor, skills-audit, hooks-audit, principal-staleness | None. The `expiry` routine ends guards; branch cleanup is a gap (G8) |
| sdlc-progress-check, sdlc-upvote-pickup (autonomous start from `upvote`-labelled issues) | None. A new task starts only from a message or `core start` |
| pm-briefings (daily briefings for a project with `pm_briefing` set) | None; no project in `projects.json` sets it today |
| system-health-digest, disk-space-check, analytics-rollup, task-backlog-check | None (G8) |
| `com.valor.backup` | The kernel's backup job carries the same label (`core backup --plist`) |
| `com.valor.update`, `com.valor.bridge-watchdog`, `com.valor.log-rotate`, `com.valor.caffeinate`, `com.valor.brew-nightly`, `com.valor.nightly-tests` | Host jobs of the old system. `update` and `bridge-watchdog` restart the old services and must be disabled at cutover; the rest can stay (E2's runbook orders it) |

## 7. Stored records

Counts are from the Cowboy's Redis (226,553 keys, 225 MB), by scanning only.
Other Macs have their own Redis that this Mac cannot read.

| Records | Count | Where it goes | How |
|---|---|---|---|
| Memory (human-ingested and extracted facts, by project: valor 1,454; company 783; cyndra 572; satsol 360; psyoptimal 116; gato-os about 100; popoto 45; cuttlefish 12) | about 3,450 | `memory/` over popoto on Postgres, milestone 6 | Import task after memory exists (G8). Not needed on day one |
| KnowledgeDocument and DocumentChunk (indexed work-vault notes) | 758 documents, 4,646 chunks | None. The new system reads files from the workspace | None. The `~/work-vault` folders stay where they are |
| RefusalMemory, GenMemory, Link, Chat, Room | about 6,000 | None | Left behind |
| TelegramMessage | 1,161 | None. The ledger records messages from the first one the new bridge receives | Left behind; see Telegram history below |
| AgentSession, Job, PipelineLedger, steering lists, DeadLetter | 51, 454, 132, 9, 14 | None | Drain before cutover (G5) |
| ImprovementEvidence, Case, Investigation, ModelRevision, Charter, ControllerState | about 2,000 | None | Left behind |
| ReflectionRun, CrashSignature, analytics | about 170,000 | None | Left behind |
| Corrections and standing instructions from Tom | None as such in the old system | The `corrections` stream, `python -m core correct TEXT` | Existing command, by Tom or Valor, when a standing instruction is wanted |

**Telegram history and the first connect.** For a chat with no `message.received`
row and no entry in `telegram-seen.json`, the new bridge's first pass records
only the chat's newest message id as the baseline and receives nothing older
(`bridges/telegram/bridge.py`, `_fill_chat`). Today the file has one entry, the
operator group. So a message sent after the old bridge stops and before the new
bridge first connects to a chat is never received. G4 covers the fix.

## 8. Gaps

Each: what the new system lacks, then a recommendation. A question for Tom
carries the answer assumed.

**G1. Other people's messages have no home.** Thirteen DM users and the
members of the teammate and customer-service groups (Cyndra Dev Team, Cyndra
Devs, PsyOPTIMAL, Agent Builders Chat, Yudame, SATSOL) are answered by the old
system and ignored by the new one. Email contacts and domains likewise.
Recommendation: list only the "Eng:" groups and Tom's own chats in specs at
cutover. A chat left out of every spec is not read at all, which is quieter
than reading and ignoring it. Customer-service replies stay on the old system
on whichever Mac still runs it, or wait for a later milestone. No new check
is needed: this is the single-operator design, not a defect.

**G2. Tom's own DM is not received.** Only the operator chat and listed chats
are owned. Recommendation: add `telegram:179144806` to `projects/valor.toml`
`chats`; a message there starts a `valor` task. By file edit.

**G3. Machine names do not match.** Old specs use ComputerName; the new
setting defaults to the short hostname (`Mac` on the Cowboy). Recommendation:
set `VALOR_MACHINE="Valor the Cowboy"` (the ComputerName, which the old fleet
roster already uses) in every Cowboy launchd job (`core serve`, both bridges,
routines) and write `machine = "Valor the Cowboy"` in every spec for Cowboy
chats. One edit to four plist environments and the specs, then `launchctl`
reload. The other Macs set their own ComputerName the same way.

**G4. Handover between the bridges can drop or double a message.** The new
bridge baselines a chat at its first connect, and the old bridge answers until
it is stopped. Recommendation: write `telegram-seen.json` by hand (one
import task) with, for each chat in the specs, the newest message id the old
system processed. The old side has it: `TelegramMessage` records carry the
chat and message id, and `bridge:last_event:<chat id>` holds a time. Run it
after the old bridge stops and before the new bridge starts, so the new
bridge's first pass receives exactly the messages the old one did not. This
is a one-off script, not a recurring check.

**G5. In-flight old work.** 51 sessions, 454 jobs, and open pipeline ledgers
carry no meaning to the new kernel. Recommendation: let the old worker drain
(no running session) before it is disabled, and re-ask Tom's open requests as
messages. Open pull requests and issues live on GitHub, so a new task can
pick them up by number.

**G6. The rebuild lands on a branch, not `main`.** `projects/valor.toml`
names the rebuild branch; the old `ai` repo's `main` still holds the old
system, whose services run from `~/src/ai` on `main`. Recommendation: at
cutover the rebuild branch becomes the repo's `main` (Tom's step, a rename or
a merge), `projects/valor.toml` drops `branch`, the grant in section 5 is
added, and the ruleset is made active. The old checkout in `~/src/ai` is not
touched until the old services are off.

**G7. The mailbox backlog.** The email job sets `VALOR_EMAIL_SINCE` to
2026-10-08. At cutover the old email bridge still has unread mail it has not
yet handled. Recommendation: set `VALOR_EMAIL_SINCE` to the cutover date, so
the new bridge never receives old backlog, and disable the old email bridge
first.

**G8. Memory and upkeep jobs.** About 3,450 old Memory records have no
destination until milestone 6. Branch cleanup, disk-space warning, and a
health digest have no routine. Recommendation: before the old Redis is
stopped, export every Memory record to JSON in an archive folder, read only
(an import task over popoto's Redis backend; no live write). Import by
project after milestone 6, starting with `valor` and `company` (about 2,200).
Do not block cutover on it. For upkeep, add nothing; add a routine when an
incident names the need.

**G9. Fields with no home.** `knowledge_base`, `linear.team`,
`context.tech_stack`, `working_directory`, `transport`, `respond_to_*`, and
`mention_triggers`. Recommendation: drop them. The spec carries what the
kernel acts on; the descriptions live in this file's tables.

**G10. Third-party keys.** Linear, Sentry, Notion, Render, Stripe, and the
rest are unreachable from a new-system turn. Recommendation: none at
cutover. When a task needs one, copy that one key into the key directory
with the existing pattern (vault to key file, mode 600) and name which
turns may read it.

**G11. The other three Macs.** The Captain does not appear in Tailscale; the
Bald has been offline 138 days; only the Pirate is reachable besides the
Cowboy. Each needs the whole handoff (`docs/plans/rebuild-handoff.md`) before
its projects can move. Recommendation: cut over the Cowboy only (valor and
popoto). Leave the other Macs on the old system; its single-machine
ownership keeps them from reading the Cowboy's chats. Move psyoptimal,
cuttlefish, and royop to the Cowboy by changing `machine` in their specs once
Tom wants them on the new system, since Tom's backlog work targets
psyoptimal, popoto, and cuttlefish. Cyndra's owner has been offline for 138
days, so its three groups have no reader today; leave it out of every spec.

**G12. The Google Workspace tokens and calendar files** (`google_token*.json`,
`calendar_config.json`) in the vault belong to Tom's and the old system's
tools. The new kernel does not read them. Recommendation: leave them in place.

## 9. Questions with assumed answers

**Q1. Which branch does psyoptimal merge to: `main` or `prod`?** Assumed:
`main`, as the emulator's spec used, because the local checkout's `prod` is
the deploy branch.

**Q2. May Valor add the merge-target grants for the day-one pairs (ai
`main`, popoto `main`, psyoptimal, cuttlefish)?** The command is marked
always Tom's. Assumed: Tom grants all four in one sitting at cutover.

**Q3. Is the operator group "Valor rebuild" the permanent operator chat?**
Assumed: yes. Notices and approvals keep going there; Tom's DM is added as an
ordinary listed chat (G2).

## 10. Day-one checklist, in the order the data needs it

1. Specs written and checked into `projects/` (popoto, psyoptimal,
   cuttlefish; valor edited). Edit a file.
2. `VALOR_MACHINE` set in each launchd job and each spec's `machine` (G3).
3. Merge-target grants added (section 5, Q2). Existing command.
4. Old worker drained (G5), then the old bridge and email bridge disabled.
5. `telegram-seen.json` seeded (G4); `VALOR_EMAIL_SINCE` set (G7).
6. New bridges and routines enabled; one message from Tom in each listed
   chat answers with a notice.
7. Memory export taken (G8) before the old Redis is stopped, last.

E2's runbook takes these steps, adds the order of `launchctl` calls, and
adds the way back.
