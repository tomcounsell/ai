---
name: sentry
description: "Check Sentry for unresolved issues and run triage on demand. Triggered by requests to check Sentry errors, run Sentry triage, or handle Sentry issues."
allowed-tools: Bash
user-invocable: true
---

# Sentry Triage

Run the Sentry triage pipeline on demand. Classifies all unresolved issues (A–E), files GitHub issues for actionable bugs, and sends a Telegram summary.

## Classification

| Class | Label      | Action                                     |
|-------|------------|--------------------------------------------|
| A     | Noise      | Test/mock/harness errors → Sentry ignored  |
| B     | Transient  | Rate limits, network errors → archived     |
| C     | Actionable | Real bugs (≥10 events) → GitHub issue      |
| D     | Review     | Ambiguous → listed for human review        |
| E     | Stale      | No events in 30 days → Sentry resolved     |

## Apply Mode

By default the triage runs **dry-run** — it classifies and reports but does not file GitHub issues or change Sentry state.

Pass `--apply` to enable live writes:

```
/sentry --apply
```

## How to Run

**Dry-run (default):**

```bash
cd /Users/valorengels/src/ai && python -c "
from reflections.sentry_triage import run_sentry_triage
import json, os
result = run_sentry_triage()
print(result['summary'])
if result.get('findings'):
    print()
    for line in result['findings']:
        print(line)
"
```

**Live mode (files GitHub issues + updates Sentry state):**

```bash
cd /Users/valorengels/src/ai && SENTRY_TRIAGE_APPLY=1 python -c "
from reflections.sentry_triage import run_sentry_triage
result = run_sentry_triage()
print(result['summary'])
if result.get('findings'):
    print()
    for line in result['findings']:
        print(line)
"
```

## Argument Handling

Check the user's invocation for `--apply`. If present, use the live mode command. Otherwise use dry-run.

If the user says "apply changes", "file the issues", "do it for real", or similar — use live mode and confirm before running.

## After Running

Report the summary line and the Class C (actionable) items to the user. If there are Class D (review) items, list them too. Omit Class A/B/E details unless the user asks.

If running dry-run and Class C issues exist, offer: "Run `/sentry --apply` to file GitHub issues for the N actionable bugs."
