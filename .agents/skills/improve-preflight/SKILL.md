---
name: improve-preflight
description: "Decide read-only whether an improvement experiment can reach a scored verdict before a freeze spends anything on it."
---

# Improve Preflight

Run this before any `experiment freeze`, or whenever asked whether an improvement case can be scored. A freeze pays for known-item generation before the evaluation runner discovers whether its judge can be calibrated at all, so the first real cycle spent a receipt on an experiment whose only reachable verdict was an infrastructure failure with zero trials. These are the reads that would have said so, taken in one pass beforehand.

Every check is read-only: reserve nothing, settle nothing, journal nothing, write no row. The case id is the argument when one is given, otherwise the `case=` value of the dispatch message you are running under. Use the project environment's `valor-improve`; it is not installed outside it.

Check the control namespace with `doctor`: a clean result is the single line naming no paused heads, no stale intents, and no outstanding reservations. Anything else names a held slot that would stop the freeze's evaluation step or a paused head that would refuse the journal transition. Stop there, record what it printed as a `probe` investigation, and never force a resume from a research session. Check budget headroom with the JSON `budget` read, comparing the settled plus reserved amount in the daily paid-inference window against the charter's daily limit and the weekly headroom. State the figures you read together with the window and day boundaries the payload names; the charter asks for the boundaries to be disclosed with every accounting, and a non-empty receipted-unknown bucket is spend with unknown metering, not zero cost. Check the calibration floor by reading how many retained architectural `correction` evidence rows exist in the project partition against the module's declared minimum reference-set size, which is the same read the evaluation makes. Below the floor every experiment in every envelope ends as an infrastructure failure with zero trials. That floor sits below published practitioner floors, so read it as a minimum rather than a target.

Print one verdict and act on it. Ready means all three pass and the freeze may proceed. Not ready on the calibration floor means do not freeze: open a `probe` investigation recording the command verbatim with its output, resolve it with the shortfall as the interpretation, and leave the hypothesis proposed. The evidence the floor needs is retained architectural corrections from real reviews; a research session cannot manufacture them and must not try. Not ready on namespace or on budget means record what you read and stop, because neither condition is yours to change from here. Never print the raw numbers alone: every verdict names the check that decided it, the figure it read, and the threshold it read it against.

This skill changes no floor, budget, lease, or case state, and runs neither freeze nor evaluate. A passing preflight is not evidence that the experiment is better; that comparison belongs to the charter's staged evaluation and needs an agent-run arm this skill does not have.
