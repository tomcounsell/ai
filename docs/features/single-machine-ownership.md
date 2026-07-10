# Single-Machine Ownership

**The strict rule:** every bridge-contact identifier in `projects.json` resolves to exactly one machine. Two machines must never both pick up the same incoming bridge message (Telegram DM from a given user, message in a given group, email from a given sender, etc.).

This is enforced by the config validator and gated by the update script — the bridge will not be restarted onto a config that breaks the rule.

## What counts as a "bridge-contact identifier"

Every shape that can route an incoming bridge message to a session:

| Identifier shape | Where it lives in `projects.json` | Example |
|-----|-----|-----|
| Telegram DM contact id | `dms.whitelist[].id` | `179144806` (Tom) |
| Telegram group name | `projects.<key>.telegram.groups.<name>` | `"Eng: Valor"` |
| Email contact (explicit address) | `projects.<key>.email.contacts[]` | `alice@example.com` |
| Email domain (wildcard) | `projects.<key>.email.domains[]` | `psyoptimal.com` |
| Registered bot peer id | `projects.<key>.telegram.bots[].id` | `8837490628` (Bruce) |

Registered bot ids carry an extra rule on top of single-machine ownership: a bot id must **not** also appear in `dms.whitelist[].id` (mutual exclusion), or the bot would resolve a project on the spawn path and its no-`reply_to` replies would loop. See [Bot End-to-End Testing](bot-e2e-testing.md).

For each shape, the validator verifies the identifier resolves to exactly one machine across the *entire* config — not just the per-machine subset. Misconfiguration is caught the same way on every machine, even if the conflicting projects are owned by different machines.

## How ownership is derived

`projects.<key>.machine` is the single source of truth. Every other ownership decision inherits from it:

```
projects.<key>.machine
  │
  ├─ ACTIVE_PROJECTS           # set on each machine where ComputerName matches
  │
  ├─ telegram.groups.<name>    # group inherits its project's machine
  │
  ├─ email.contacts[]          # contact inherits its project's machine
  ├─ email.domains[]           # domain inherits its project's machine
  │
  └─ dms.whitelist[]           # entry's `project` field links to the machine
```

Adding a new machine costs zero edits to existing whitelist entries, group declarations, or email patterns. Move a project from one machine to another by changing one line: the `machine` field on that project. Everything follows.

## Machine identity resolution

[`config/machine.py`](../../config/machine.py) is the single source for "what machine am I / what do I own" — the lowest shared layer (stdlib only, plus `config.paths`). Every `scutil --get ComputerName` call and every `projects.json` ownership match resolves through it, so a fix to the resolution logic propagates everywhere instead of drifting across copies (issue #1997 consolidated five drifted copies).

Three functions, each fail-soft (never raises on a read failure):

- **`get_machine_name() -> str`** — stripped `scutil --get ComputerName`, `""` on any failure. It deliberately has **no** `platform.node()` fallback: the `""` is the "unknown host → do not match / skip" signal the ownership and README-check consumers depend on. A fallback here would let an unresolved host silently match a `"machine": ""` entry.
- **`get_machine_slug() -> str`** — filesystem-safe, guaranteed **non-empty** variant (lowercased, spaces→hyphens, with a `platform.node()` fallback) used for per-machine token filenames (`tools/google_workspace/auth.py`), where an empty slug would collapse every host's token onto one path.
- **`get_machine_project_keys(machine=None) -> list[str]`** — case-insensitive match of each `projects.<key>.machine`, `[]` on any read failure. Applies the **empty-machine fail-to-development guard** (#1834): an unresolved `machine` (`""`) returns `[]` before any file read, so it can never mis-tag a dev/misconfigured host as an owner. `monitoring/sentry_config.py::_owned_project_key` is a thin first-or-`None` adapter over it.

Note: `scripts/update/readme_check.py` reads the **repo-local** `config/projects.json` (different shape, with `working_directory`), so it borrows only `get_machine_name()`, not the project-key logic.

## What the validator catches

[`bridge/config_validation.py`](../../bridge/config_validation.py) — `validate_projects_config(cfg)` aggregates errors from every shape into one report:

1. **DM whitelist conflicts** — same Telegram contact id mapped to projects on different machines
2. **Group name conflicts** — same group declared on two machines (case-insensitive)
3. **Email contact conflicts** — same explicit address on two machines (case-insensitive)
4. **Email domain conflicts** — same domain on two machines (case-insensitive; `*.foo.com`, `@foo.com`, and `foo.com` collapse to the same key)
5. **Cross-shape email conflicts** — an explicit contact `alice@psy.com` on machine A while machine B owns the `psy.com` domain wildcard (the wildcard would steal the message)
6. **Project missing `machine`** — a project that declares any bridge contact but no `machine` field

Plus structural checks: every whitelist entry must declare a `project`, every referenced project must exist, and every machine field must be non-empty.

## Where the validator runs

**The update script — green-light gate.** [`scripts/update/run.py`](../../scripts/update/run.py) Step 4.6 calls [`scripts/update/verify.py::check_projects_json`](../../scripts/update/verify.py) before bouncing services. On failure:

- The full validator error is logged.
- `do_service_restart` is set to False for the rest of the run via `dataclasses.replace`.
- The currently-running bridge keeps serving on the previously-validated config.
- The operator sees every problem at once (errors are aggregated, not one-at-a-time).

This is the only place validation runs. The bridge does not validate on its own startup — that would crash the live process when a bad config lands. Gating at the update layer means a bad push fails *forward*: the new config is rejected, the old config keeps working.

A bad config never reaches a bridge restart, so the rule cannot be violated at runtime.

## What this guarantees

- **No racing bridges.** Two machines cannot both initiate a session for the same incoming Telegram DM, Telegram group message, or email — each identifier has a single owner.
- **No mystery responses.** When a contact stops getting replies after a config change, the answer is in one place: which machine owns their project?
- **No drift on multi-machine deployments.** Adding/moving/retiring a machine is a one-field edit on each affected project. There is no per-contact deny-list to maintain.
- **No outage from a bad push.** The gate prevents a malformed `projects.json` from rolling out.

## Memory persistence inherits the same boundary

The single-machine-ownership rule applies to memory persistence as well as response routing. Following PR #1173 (the bridge `dm`-namespace writer leak), the Telegram bridge now treats an incoming message in three layers:

1. **Conversation history** (`store_message`, `register_chat`): always recorded, even when the chat doesn't resolve to any declared project. Pass `project_key=None` — both functions accept it. This is what `valor-telegram read` reads from.
2. **Canonical Memory partition write** (`Memory.safe_save`): gated on a resolved project. If no project resolves (sender not whitelisted, group title not declared on this machine), the canonical memory write is skipped entirely. Unowned messages no longer pollute any partition.
3. **Session creation**: hard early-return if no project resolves. The `:1003` guard in `bridge/telegram_bridge.py` makes this explicit and prevents the latent NameError class problem from the previous `else "dm"` fallback.

A bridge startup invariant enforces that `set(DM_WHITELIST) == set(DM_USER_TO_PROJECT.keys())` — every `dms.whitelist[]` entry must reference an active project on the current machine. If the sets ever decouple (for example, a `dms.whitelist[]` entry referencing a project that this machine doesn't own), the bridge raises `RuntimeError` at startup rather than letting a whitelisted sender_id pass routing checks while having no project mapping.

The retired `"dm"` literal has been removed from `bridge/telegram_bridge.py`. `Memory.safe_save` includes a `_warn_if_legacy_namespace` regression detector that logs a WARNING with stack trace if any caller still attempts a `"dm"` write, and a DEBUG-level audit on `"default"` writes (the latter is still legitimate during single-machine bootstrap and test fixtures).

See [Subconscious Memory — Project Key Partitioning](subconscious-memory.md#project-key-partitioning) for the writer matrix and [#1232](https://github.com/tomcounsell/ai/issues/1232) for the follow-up to consolidate every Memory writer onto a single `project_key` resolver.

## Adding a new machine

1. On the new machine, set `ComputerName` (System Settings → Sharing) to a name like `Valor the {Animal}`.
2. In `<vault>/projects.json` (where `<vault>` is your configured vault directory — see `VALOR_VAULT_DIR`; default `~/Desktop/Valor/`), set `projects.<key>.machine = "Valor the {Animal}"` for each project this machine should own. Propagate the change to every other machine using whatever sync mechanism your vault uses (iCloud for Desktop/Documents vaults, manual copy for `~/.valor/`, etc.).
3. Run `/update` (or wait for the launchd cron). The update script validates `projects.json` before bouncing the bridge. On the new machine, the bridge starts owning the moved projects; on the old machine, the next update cycle drops them from `ACTIVE_PROJECTS` and the bridge stops responding to those groups/contacts.
4. There is nothing to edit in `dms.whitelist`, `telegram.groups`, `email.contacts`, or `email.domains` — they all inherit from `projects.<key>.machine`.

## Failure modes & responses

| Failure | Symptom | Operator response |
|-----|-----|-----|
| Same Telegram contact in two projects on different machines | `/update` logs `contact id=N maps to multiple machines [...]`; restart is skipped | Decide which project owns the contact; remove the duplicate `dms.whitelist` entry |
| Same group on two projects on different machines | `/update` logs `telegram group 'name' is declared on multiple machines [...]` | Move the group to a single project, or change one project's `machine` field |
| Email contact + overlapping domain wildcard on different machines | `/update` logs `email contact 'x@y.com' (machines [...]) overlaps with domain 'y.com' wildcard (machines [...])` | Move the explicit contact onto the domain owner's project, or split the domain |
| Project declares bridge contacts but no `machine` | `/update` logs `project 'X' declares ... but has no 'machine' field` | Set the `machine` field on the project |

In every case, the running bridge keeps serving until the operator fixes the config and re-runs the update.

## Test coverage

`tests/unit/test_dm_whitelist_validation.py` covers all four identifier shapes, case-insensitivity, the cross-shape email-vs-domain check, the aggregated `validate_projects_config` report, and the live-config validation against `~/Desktop/Valor/projects.json`. 25 cases, run on every CI cycle.

## Related

- [Multi-Instance Deployment](deployment.md) — how machines come up and what they do
- [Remote Update](remote-update.md) — the update flow that runs the green-light gate
- [Email Bridge](email-bridge.md) — sender → project routing
- [Config Architecture](config-architecture.md) — config file layout and source-of-truth
- [Reflections](reflections.md#repo-specific-reflections-single-machine-ownership) — repo-specific reflections (`project_key`) inherit this same one-owner-per-project boundary so repo audits don't file duplicate issues from N machines
