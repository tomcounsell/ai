# Sentry Triage Auto-Action

The `sentry-issue-triage` reflection (`reflections/sentry_triage.py`, scheduled daily via `config/reflections.yaml`) pulls unresolved Sentry issues, classifies them A–E, and now also updates Sentry state for tiers A, B, and E so they stop polluting future digests.

> **Trigger migration in progress (pilot).** `sentry-issue-triage`'s daily *trigger* is being
> migrated off the local reflection scheduler onto a Claude Code Routine ("Cowork") — see
> [`docs/infra/cowork-sentry-triage.md`](../infra/cowork-sentry-triage.md) for the pilot spec and
> [Cowork Tasks](cowork-tasks.md) for the reusable pattern. The A–E classification rubric and the
> apply-gate mechanics described below (`reflections/sentry_triage.py::run_sentry_triage`) are
> **unchanged** by that migration — only the scheduling trigger moves. The local reflection entry
> is removed from the registry only after an operator confirms the live routine is verified; until
> then this doc's "scheduled daily via `config/reflections.yaml`" description remains accurate.

## Why this exists

Before this feature, the reflection labeled tiers A (noise), B (transient), and E (stale) as "ignore / archive / resolve" but performed no state change in Sentry. The same issues reappeared in every daily digest, drowning out the C (actionable) and D (needs investigation) issues that actually require human attention. On the live baseline run, 244 unresolved issues produced only ~90 items needing human eyes — the other ~150 were repeat-offender noise that the classifier already knew how to dismiss.

Now A/B/E get auto-actioned on the same run that classifies them. The Telegram digest shrinks to the human-review pile (C + D) plus a separate "Auto-actioned" counter so the operator can audit what the reflection did.

## The apply gate

A single env var, `SENTRY_TRIAGE_APPLY`, controls **all** Sentry writes:

| Value | Behavior |
|-------|----------|
| unset or `"0"` (default) | Dry-run. No Sentry `PUT`s. No GitHub issues filed. Digest reports "would auto-action" counts. |
| `"1"` | Live. Tier A/B/E Sentry state changes applied; tier C GitHub issues filed. |

The gate is read at call time via `_apply_enabled()` — no module-level constant. There is **no separate flag for the C-tier GitHub filing path**: a single env var keeps both paths in sync so an operator can never end up in a partial-live state.

Set it per-machine in `~/Desktop/Valor/.env`:

```
SENTRY_TRIAGE_APPLY=1
```

Remove or set to `0` to revert to dry-run. All target states are reversible from the Sentry UI, so a misclassification is recoverable by hand.

## Tier → Sentry state map

| Tier | Meaning | PUT payload |
|------|---------|-------------|
| A | Test/mock/harness noise | `{"status": "ignored"}` (permanent ignore) |
| B | Known transient (rate limit, network, auth) | `{"status": "ignored", "statusDetails": {"ignoreUntilEscalating": true}}` |
| C | Actionable bug | (no Sentry change — GitHub issue filed instead) |
| D | Needs investigation | (no Sentry change — listed in digest) |
| E | Stale (no events in 30 days) | `{"status": "resolved"}` |

### The tier B `ignoreUntilEscalating` quirk

This is a Sentry API gotcha worth calling out. A naive `PUT {"status": "ignored"}` defaults the substatus to **`archived_forever`**, not `archived_until_escalating`. To get the UI-default behavior — where the issue auto-unarchives the next time it escalates — the payload **must** explicitly include `statusDetails.ignoreUntilEscalating: true`.

Reference: [sentry-mcp issue #878](https://github.com/getsentry/sentry-mcp/issues/878) and the Sentry "Update an Issue" API docs.

If this detail were missed for tier B, a real regression that we expect to auto-unarchive would stay archived forever and mask the bug. A dedicated unit test asserts the payload structure.

## Failure isolation

Each tier-A/B/E update is an independent `PUT /api/0/issues/{id}/`. A single failure (network error, non-2xx response, missing issue id) does **not** abort the run — the loop continues, the failure is logged at WARN level, and the failure count surfaces in the digest. The summary line looks like:

```
sentry-issue-triage: 244 issues across 3 project(s) (A=58 B=87 C=12 D=78 E=9), auto-actioned: A=58/58 B=86/87 E=9/9 (1 failed)
```

## Duplicate-issue dedup (tier C)

Before filing a tier-C GitHub issue, `_issue_already_filed(title, cwd)` checks
whether an open issue with that exact title already exists. It lists open
issues via `gh issue list --state open --limit 200 --json title` — a
strongly-consistent read, not `gh issue list --search`, whose search index can
lag fresh issues by minutes and would let back-to-back triage runs both see
"no existing issue" and file duplicates. Titles are compared for full exact
equality (whitespace-normalized only, no substring matching).

The check **fails closed**: any subprocess error, non-zero `gh` exit, timeout,
or JSON-parse failure returns `True` ("assume filed," skip creating the
issue) and logs a warning. A skipped filing self-heals on the next daily
run; a duplicate does not, so the failure mode defaults to under-filing
rather than over-filing.

The `--limit 200` listing assumes the repo's open-issue count stays well
under 200 — `gh` silently truncates beyond that, so a genuinely-filed issue
past position 200 would be missed and refiled. Raise the limit if the open
backlog approaches it.

## Telegram digest

In live mode, the digest gets an explicit `[LIVE — Sentry state changes applied]` footer. In dry-run mode, it gets `[dry run — no Sentry state changes]` (mirroring the existing `[dry run — no GitHub issues filed]` line for tier C). The auto-actioned block sits between the per-tier counts and the C-tier highlight rows, separating "what we already handled" from "what still needs you".

## Environment gating (init side)

Triage is the read/dismiss side. The **init** side — deciding whether an event is even captured, and under which `environment` tag — lives in `monitoring/sentry_config.py::configure_sentry()`, called once at startup by both the bridge (`bridge/telegram_bridge.py`) and the worker (`worker/__main__.py`). Two gates run in order:

1. **Test/CI suppression (#1948).** `configure_sentry()` returns early (no `sentry_sdk.init`) whenever `PYTEST_CURRENT_TEST` or `CI` is set. A local `pytest` run therefore never reports to Sentry at all — synthetic test errors can't leak into the production project.

2. **Dev-vs-prod environment resolution (#1834).** When init does proceed, `_resolve_environment()` picks the `environment` tag with this precedence:
   - An explicit `SENTRY_ENVIRONMENT` env var always wins (escape hatch; can force e.g. `staging`).
   - Otherwise, a **designated bridge machine** — one that owns ≥1 project in `~/Desktop/Valor/projects.json` (a `projects.<key>.machine` field matching the local `scutil --get ComputerName`, case-insensitive) — reports as `production`.
   - Every other machine reports as `development`.

   The ownership predicate is a self-contained copy of the one in `ui/data/machine.py::get_machine_project_keys` and enforced by `bridge/config_validation.py::validate_projects_config`; `monitoring/` deliberately does not import `ui/` (layer direction). See [`single-machine-ownership.md`](single-machine-ownership.md) for the ownership model.

   **Fail-to-development is deliberate.** Any failure (unreadable `projects.json`, `scutil` error, or an empty/unresolved ComputerName) resolves to `development`. An empty ComputerName is explicitly short-circuited to "not owned" so it can never accidentally match a config entry with an empty `machine` field (`"" == ""`). A real production bridge machine always resolves a non-empty name and a readable config (it cannot route messages otherwise), so only dev/misconfigured hosts hit the fallback — exactly the ones that should not report as `production`.

`configure_sentry()` logs the resolved environment plus its inputs (ComputerName, matched project key) at INFO on init, so a wrong tag is diagnosable from `logs/bridge.log` / `logs/worker.log` without needing Sentry itself.

## Related files

- `reflections/sentry_triage.py` — the reflection (apply gate, tier map, update helper)
- `monitoring/sentry_config.py` — init-side gating: test/CI suppression + dev-vs-prod `environment` resolution
- `tests/unit/test_sentry_triage_apply.py` — coverage for the apply gate, tier mapping, dry-run no-op, failure isolation, and digest rendering
- `tests/unit/test_worker_sentry_init.py` — coverage for the init guards and environment resolution
- `config/reflections.yaml` — daily schedule entry (`sentry-issue-triage`, 86400s)
- `~/Desktop/Valor/.env` — `SENTRY_AUTH_TOKEN` (read+write) and the optional `SENTRY_TRIAGE_APPLY=1` flag; `SENTRY_ENVIRONMENT` (optional) overrides the resolved environment
- [`docs/infra/cowork-sentry-triage.md`](../infra/cowork-sentry-triage.md) — pilot spec migrating this reflection's *trigger* to a Claude Code Routine (rubric/apply-gate logic above is unchanged)
