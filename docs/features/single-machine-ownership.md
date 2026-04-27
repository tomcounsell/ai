# Single-Machine Ownership

**The strict rule:** every bridge-contact identifier in `projects.json` resolves to exactly one machine. Two machines must never both pick up the same incoming bridge message (Telegram DM from a given user, message in a given group, email from a given sender, etc.).

This is enforced by the config validator and gated by the update script — the bridge will not be restarted onto a config that breaks the rule.

## What counts as a "bridge-contact identifier"

Every shape that can route an incoming bridge message to a session:

| Identifier shape | Where it lives in `projects.json` | Example |
|-----|-----|-----|
| Telegram DM contact id | `dms.whitelist[].id` | `179144806` (Tom) |
| Telegram group name | `projects.<key>.telegram.groups.<name>` | `"Dev: Valor"` |
| Email contact (explicit address) | `projects.<key>.email.contacts[]` | `alice@example.com` |
| Email domain (wildcard) | `projects.<key>.email.domains[]` | `psyoptimal.com` |

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

## Adding a new machine

1. On the new machine, set `ComputerName` (System Settings → Sharing) to a name like `Valor the {Animal}`.
2. In `~/Desktop/Valor/projects.json`, set `projects.<key>.machine = "Valor the {Animal}"` for each project this machine should own. iCloud propagates the change to every other machine within minutes.
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
