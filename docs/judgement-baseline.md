# The underspecification classifier against the replay baseline

Part of [judgement-layer.md](judgement-layer.md), "The first task". Governed by [mission.md](mission.md).

The seed set is the seven labelled cases in the two records. A case is labelled by outcome, not by
how the request looks: thin when asking first changed what was built, precise when it did not or
made it worse.

| Case | Request shape | Label | Evidence |
|---|---|---|---|
| psyoptimal #894 | Example standing for the requirement; names an existing chip | `example_as_spec` | Three answers each changed the outcome (rebuild-demonstration.md, "Attention log") |
| popoto #191 | One line | `one_line_ask` | Fidelity 1 to 3, hidden tests 3/11 to 7/11 (rebuild-baseline.md, pop-b) |
| popoto #188 | One line | `one_line_ask` | PM round avoided, fidelity 4 to 5, half the spend (rebuild-baseline.md, pop-c) |
| psyoptimal #872 | Bulleted, names the flag and the reason | `precise` | Clarify changed nothing (rebuild-baseline.md, pso-a) |
| cuttlefish #646 | Bug report with failure scenario and desired behavior | `precise` | Clarify changed nothing (rebuild-baseline.md, cut-a) |
| psyoptimal #893 | Names existing UI, lists three candidate fixes | `precise` | Clarify tied on every score (rebuild-baseline.md, pso-b) |
| popoto #633 | Bug report with measurements and source pointers | `precise` | Clarify hurt: fidelity 5 to 3, correctness 5 to 2 (rebuild-baseline.md, pop-a) |

#893 is the case that keeps the classifier honest. It names existing UI and
says "improve", which on surface features reads as `existing_ui_unscoped`,
yet its listed options carried enough scope that asking changed nothing. A
classifier that keys on "names existing UI" gets it wrong.

What the seed set can and cannot show:

- **It is an entry check, not a calibration.** The classifier must label all
  seven correctly on both legs before it routes anything. Seven cases show
  it is not broken; they cannot fit a floor or support a Brier score worth
  reporting as a measure.
- **Its labels are mostly not Tom's.** The baseline's answers came from a
  Sonnet stand-in for Tom (`role_played`) and its outcomes from a blind
  Sonnet judge, n = 1 per arm, where one judge point is noise
  (rebuild-baseline.md, "Caveats"). #894's first-round label is Tom's own
  words; its second round was role-played. The record reports the split.
- **It grows from attention already spent.** Every clarify answer labelled
  by shape 5 adds a case: an answer that changed the outcome confirms thin,
  "Your call" on every question is evidence of precise, and a bare build
  whose first PM feedback changed the scope is a missed thin label
  (rebuild-baseline.md, "Appendix").
- **The emulator measures the saving.** The demonstration's estimate ($1.40
  to $1.80 of $2.97 saved, plus both review rounds) is an estimate. The
  emulator replays the seed cases with and without the classifier and
  reports the difference in money and in PM rounds (`docs/emulator.md`).

**The record.** Run 5 of 2026-10-02 is the record the declaration names:
both legs right on all seven cases, Brier 0.0114 (Jev) and 0.0040
(fallback), n = 7 each, no abstains or errors, about 33 and 226
micro-dollars per call; label sources Tom 1, role-played 0, judge 6. This
is information, not evidence: the judge's wording was fitted over five
runs. Runs 2 to 4 rewrote it after seeing which cases failed (the listed
fixes in `precise` and the short label in `one_line_ask` exist because
#893 and #188 failed); run 3 also rewrote the fallback's system prompt and
run 4 added a `notes` field its answer writes before the probabilities;
run 5 changed none. Only the emulator and live
labels can show whether it generalizes (`docs/plans/m1-3-judgement.md`).
