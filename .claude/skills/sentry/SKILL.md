---
name: sentry
description: "Check Sentry for unresolved issues and run triage on demand. Triggered by requests to check Sentry errors, run Sentry triage, or handle Sentry issues."
allowed-tools: Bash
user-invocable: true
argument-hint: "[--apply]"
model: sonnet
effort: low
---

# Sentry Triage

Run the Sentry triage pipeline (`reflections.sentry_triage`) on demand. Classifies all unresolved issues (A–E), files GitHub issues for actionable bugs, and sends a Telegram summary.

## Classification

| Class | Label      | Action                                     |
|-------|------------|--------------------------------------------|
| A     | Noise      | Test/mock/harness errors → Sentry ignored  |
| B     | Transient  | Rate limits, network errors → archived     |
| C     | Actionable | Real bugs (≥10 events) → GitHub issue      |
| D     | Review     | Ambiguous → listed for human review        |
| E     | Stale      | No events in 30 days → Sentry resolved     |

## How to Run

The default is a **dry-run**: it classifies and reports, filing nothing and changing no Sentry state. `SENTRY_TRIAGE_APPLY=1` enables live writes (files GitHub issues, updates Sentry state).

```bash
cd ~/src/ai && python -c "
from reflections.sentry_triage import run_sentry_triage
result = run_sentry_triage()
print(result['summary'])
if result['status'] != 'disabled':
    show = False
    for line in result.get('findings', []):
        if line.startswith('Class C') or line.startswith('Class D'):
            show = True
        elif line.startswith('Class '):
            show = False
        if show:
            print(line)
"
```

For live mode, run the same command prefixed with `SENTRY_TRIAGE_APPLY=1`.

## Live writes are gated

Run live mode only when the invocation carries `--apply`. If the user asks for live action in words ("file the issues", "do it for real"), run the dry-run first, state the count ("This will file N GitHub issues and update M Sentry issues"), and get confirmation before running live.

## After Running

- Show the summary line (counts by class).
- Show all Class C items — these are the actionable bugs.
- Show Class D items — note that only the top 5 are listed even if more exist.
- Omit Class A/B/E details unless the user asks.

If running dry-run and Class C issues exist, offer: "Run `/sentry --apply` to file GitHub issues for the N actionable bugs."
