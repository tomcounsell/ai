# Sentry Triage Auto-Action

The `sentry-issue-triage` reflection (`reflections/sentry_triage.py`, scheduled daily via `config/reflections.yaml`) pulls unresolved Sentry issues, classifies them A–E, and now also updates Sentry state for tiers A, B, and E so they stop polluting future digests.

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

## Telegram digest

In live mode, the digest gets an explicit `[LIVE — Sentry state changes applied]` footer. In dry-run mode, it gets `[dry run — no Sentry state changes]` (mirroring the existing `[dry run — no GitHub issues filed]` line for tier C). The auto-actioned block sits between the per-tier counts and the C-tier highlight rows, separating "what we already handled" from "what still needs you".

## Related files

- `reflections/sentry_triage.py` — the reflection (apply gate, tier map, update helper)
- `tests/unit/test_sentry_triage_apply.py` — coverage for the apply gate, tier mapping, dry-run no-op, failure isolation, and digest rendering
- `config/reflections.yaml` — daily schedule entry (`sentry-issue-triage`, 86400s)
- `~/Desktop/Valor/.env` — `SENTRY_AUTH_TOKEN` (read+write) and the optional `SENTRY_TRIAGE_APPLY=1` flag
