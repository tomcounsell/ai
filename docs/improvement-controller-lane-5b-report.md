# Lane 5b report: paired agent-run arm and the deferred evaluations (#3311)

## What was built

The `agent_run` arm job mode behind the same subprocess boundary as lane 4's
retrieval arm: restore the frozen corpus, arm the writer guard, run one bounded
agent session per trial under the arm's child env, assert the frozen digest
unchanged at teardown, return per-trial outcomes plus the candidate manifest.
The runner branches by protocol mode (retrieval untouched), scores agent trials
through the `JudgeFn` roster, scans the serialized envelope at the pre-judge
scan (`runner.py:486`) with the candidate manifest in the identity dict, and
runs Gate 1 per-task agreement against the recorded baseline. Spend settles
through lane 3's meter against unit 2 with the `arm:<arm_run_id>:` prefix:
pre-trial reserve plus post-trial settle per task budget, over-budget as a
harness error toward `infra_failure_cap`. An `openrouter:` manifest-model
prefix routes sessions to OpenRouter chat-completions; `is_open_source` gates
`_run_agent_arm` so client-keyed projects refuse before reserve or spawn.

## What was measured

**Skill-acquisition evaluation** (record:
`skill-acquisition-eval-3311-record.json`). Lane 5's `skill_acquisition`
investigation supplied the gap and the integrated `improve-preflight` skill;
the prior capability (no checklist) was the incumbent. Four frozen readiness
tasks, one rubric endpoint, real `claude -p` sessions, real metered spend.
Gate 1 baseline agreement held 4/4. Verdict: **inconclusive**, effect 0.0,
adjusted p 1.0. The reuse observation is recorded on the case and the
`deferred: no agent-run arm` disposition is cleared.

**Cheap-inference experiment** (record:
`cheap-inference-eval-3311-record.json`). Gated on the recorded
`keyless_integrated` disposition, no vault wait. Incumbent
`claude-subscription` vs candidate `openrouter:meta/muse-spark-1.3` on two
frozen tasks, real sessions, real metered spend ($0.16 estimated over 8
trials, wall 244s). Gate 1 agree/agree. Verdict: **inconclusive**, effect
-0.5, CI [-1.0, 0.0], adjusted p 1.0, `blinded=True`. The candidate's one miss
(HOLD on t1) matched a miss the incumbent itself made at baseline.

## What this does not establish

Neither verdict overturns or confirms lane 5's provisional no-gain
assumption. Inconclusive is the stopping rule speaking (short batches: 4 and
2 trials), not a finding of equivalence. The charter leg is unmeasured in
both runs (serves-charter floor refusal, 0 of 20 reference items), so both
verdicts rest on rubrics alone.

## Known limitation: skill names reach judges

The skill-acquisition run recorded a genuine blinding hit: the string
`improve-preflight` reached a judge (`blinded=False`, evaluation continued
per record-never-suppress semantics). A skill-guided candidate self-identifies
through its output. The cheap-inference run stayed blinded. Any future
skill comparison that needs blinding must reckon with this: behavior reveals
the skill, and stripping skill names from judge inputs would distort exactly
the outcomes under test.

## What would change the answer

- More trials per evaluation (the stopping rule, not the arm, forced both
  inconclusive verdicts).
- A calibrated serves-charter judge (20 reference items) so the charter leg
  can vote instead of refusing.
- A blinding strategy for skill identity, if arm attribution must stay hidden
  from judges.
- Tighter candidate misses: the cheap-inference gap is one HOLD on one task,
  shared with the incumbent's baseline behavior.
