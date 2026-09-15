---
name: improve-preflight
description: "Use before `valor-improve experiment freeze`, or when asked whether an improvement experiment can be scored: reads the case lease and reservation state (doctor), the unit-2 and unit-3 budget windows, and the serves-charter judge's calibration floor, and says whether a freeze can reach a scored verdict. Read-only; spends nothing."
allowed-tools: Bash, Read
user-invocable: true
argument-hint: "[case-id]"
---

# Improve Preflight

A research session freezes an experiment by paying for known-item generation
(`tools/improvement_experiment.py`, step 4 of `freeze_experiment`) before the
evaluation runner discovers whether its judge can be calibrated at all
(`tools/improvement_eval/runner.py::default_judges`). In the first real cycle
(case `1ec40086ca1d422e90ef747775ff7f64`) that ordering spent a
`known_item_generation` receipt on an experiment whose only reachable verdict
was `infra_failure` with zero trials. This skill runs the three reads that
would have said so, in one pass, before the freeze.

Every check is read-only. Nothing here reserves, settles, journals, or writes
a row. The CLI lives in the project venv only:

```bash
VI="$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve"
PY="$CLAUDE_PROJECT_DIR/.venv/bin/python"
```

`$CASE_ID` is the argument when one is given, else the `case=` value of the
dispatch message you are running under.

## Check 1: the control namespace is clean

```bash
"$VI" doctor
```

Pass: the single line `no paused heads, no stale intents, no outstanding
reservations` and exit 0. Anything else names a paused head, a wedged intent,
or a reservation that a live intent still holds. A held unit-1 slot means the
freeze's `evaluate` step cannot start; a paused head means the journal will
refuse `experiment_frozen`. Stop here and record what `doctor` printed as a
`probe` investigation on the case; do not `resume --force` from a research
session.

## Check 2: budget headroom in the open windows

```bash
"$VI" --json budget
```

Read `unit2.settled_usd + unit2.reserved_usd` against the daily paid-inference
limit (`settings.improvement.daily_paid_inference_usd`, charter §8: $10 per
day) and `unit3.headroom_usd` for the week. A freeze reserves one
`known_item_generation` amount and an evaluation reserves `evaluation_judges`;
both refuse `UNIT2_UNAVAILABLE` when the window cannot admit them. State the
numbers you read and the day and week boundaries the payload names
(`window_start`, `window_end`, `budget_day_boundary`); charter §8 asks for the
boundaries to be disclosed with every accounting. A non-empty
`unit2_receipted_unknown` is spend with unknown metering and is not zero cost.

## Check 3: the judge can be calibrated

```bash
"$PY" -c '
from tools.improvement_eval.calibration import (
    MIN_REFERENCE_SET_SIZE, collect_architectural_reference)
n = len(collect_architectural_reference("valor"))
print(f"architectural_corrections={n} floor={MIN_REFERENCE_SET_SIZE} "
      f"{"ok" if n >= MIN_REFERENCE_SET_SIZE else "SHORT"}")
'
```

`collect_architectural_reference` reads retained `correction` evidence rows
with `classification == "architectural"` in the project partition; it is the
same read `calibrate()` makes at evaluation time. Below the floor, every
experiment in every envelope ends `infra_failure` with `trials=0` (the note on
the evaluation reads "Calibration reference set holds N architectural
corrections, below the floor of 20"). The floor is lane 4's and is itself
below the published practitioner floors (about 30 in the Hugging Face
LLM-judge cookbook, 50 to 200 in the DEV Community sizing article cited on
investigation `3dbf7e2f7c67457bb768a03050772558`), so read it as a minimum.

## Verdict

Print one of these and act on it:

- **READY**: all three pass. Proceed to `"$VI" experiment freeze --case "$CASE_ID"`.
- **NOT READY: calibration floor**: checks 1 and 2 pass, check 3 is short. Do
  not freeze. Open a `probe` investigation on the case recording the command
  above verbatim and its output, resolve it with the shortfall as the
  interpretation, and let the hypothesis stand as `proposed`. The evidence
  the floor needs is retained architectural corrections from real SDLC
  reviews; a research session cannot manufacture them and must not try.
- **NOT READY: namespace** or **NOT READY: budget**: record what you read as a
  `probe` investigation and stop. Neither condition is yours to change from a
  research session.

Do not print the raw numbers alone. Every verdict names the check that
decided it, the figure it read, and the threshold it read it against.

## What this skill does not do

It does not change the floor, the budget, the lease, or the case state. It
does not run `experiment freeze` or `experiment evaluate`. It does not
establish that a passing preflight yields a better experiment; that
comparison is charter §5 stage 4 and needs the agent-run arm #3311 owns.
