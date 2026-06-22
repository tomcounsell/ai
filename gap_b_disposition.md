# Gap B: CRITIQUE Marker Not Flipped to `completed` — Read-Only Investigation Disposition

## 1. Verdict-Recording Code Path (`_verdicts.CRITIQUE`)

The sole writer of `_verdicts.CRITIQUE` is `tools/sdlc_verdict.py::record_verdict()`. It is
called by the critique skill via the CLI alias `sdlc-tool verdict record --stage CRITIQUE
--verdict "$VERDICT_STRING" --issue-number "$ISSUE_NUMBER"` (do-plan-critique/SKILL.md, Step
5.5). Inside `record_verdict`, a dict of the form
`{"verdict": ..., "recorded_at": ..., "artifact_hash": ...}` is atomically written to
`AgentSession.stage_states["_verdicts"]["CRITIQUE"]` via
`tools.stage_states_helpers.update_stage_states`. The router reads this dict back through
`_latest_critique_verdict()` in `agent/sdlc_router.py` (lines 212-221), which prefers
`meta["latest_critique_verdict"]` when already populated by `sdlc_stage_query`, and falls back
to `stage_states["_verdicts"]["CRITIQUE"]` directly. Guards G1, G5, and dispatch rows 2b, 2c,
3, 4a-4c all depend on this field being present and non-empty for correct routing.

## 2. Marker Flip: Who Should Flip CRITIQUE to `completed` (and Why It Stays `in_progress`)

The completion marker is written by `tools/sdlc_stage_marker.py::write_marker()` via
`sdlc-tool stage-marker --stage CRITIQUE --status completed`. The critique skill
(`do-plan-critique/SKILL.md`, Step 5.5) is the sole caller, and it explicitly writes the
`completed` marker **only on a `READY TO BUILD` verdict**. For `NEEDS REVISION` and
`MAJOR REWORK` verdicts, the skill intentionally leaves the marker at `in_progress` so that
router rows 2b and 3 can re-route to `/do-plan`. The Stage Marker section of the SKILL.md
makes this explicit: "On a READY TO BUILD verdict, write the completion marker; on any other
verdict, leave it `in_progress`." This is deliberate design, not a bug. Intermediate
`NEEDS REVISION` verdicts ARE persisted in `_verdicts.CRITIQUE` via the mandatory Step 5.5
`sdlc-tool verdict record` call on every exit path, but the stage marker is intentionally left
at `in_progress` because the critique cycle is not complete until the plan passes. The CRITIQUE
marker being `in_progress` through revision rounds is correct. It transitions to `completed`
only when the critique produces a `READY TO BUILD` verdict, at which point Step 5.5
co-locates the verdict record and the completion marker write in a single mandatory block.

## 3. Why the `in_progress` Marker Through Merge Is Benign (and the Fix Belongs to #1654)

The gap — the CRITIQUE stage marker lingering at `in_progress` after a successful
`READY TO BUILD` verdict has been recorded and `/do-build` dispatched — arises when the
Step 5.5 completion marker write either fails silently (e.g., Redis unreachable at that moment)
or the marker is not written because the session's `CRITIQUE` stage was never explicitly
started before the critique ran. Because the router is never invoked after `/do-merge`
completes, a stale `in_progress` CRITIQUE marker has no runtime effect on routing decisions
post-merge and cannot cause a misroute. The artifact is benign for the same reason that row
10 (`_rule_ready_to_merge`) gates on `_stages_completed(stage_states, needed)` which includes
`CRITIQUE` — if CRITIQUE is still `in_progress` the router would route to critique rather than
merge, which is a visible signal that prompts investigation rather than a silent failure. The
correct fix — ensuring the completion marker and verdict record are always co-located atomically
on the `READY TO BUILD` path, and adding a post-merge audit to surface lingering
`in_progress` markers — is deferred entirely to issue #1654, which owns the
verdict-persistence and marker-lifecycle correctness work. No code changes are made in this
investigation.

References: https://github.com/tomcounsell/ai/issues/1654
