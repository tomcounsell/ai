Pi's JSON event streams (`pi --mode json`, release 0.73.1).

`plain` and `tool_call` were recorded from real Pi turns against the
scripted provider (`tests/scripted_upstream.py`), with the working directory
rewritten. `error`, `aborted`, `compaction`, `compaction_ended`, and `no_session` are those
recordings edited to the shape Pi gives in each case: the last assistant
message's `stopReason`, a `compaction_start` and `compaction_end` pair
(Pi's event shape, read from its source), and the `session` line removed.
`compaction` has the shape a real compaction leaves in print mode (the stream
ends on `compaction_start`; the scripted-provider case in
`tests/test_harness_contract.py` drives a real one). `compaction_ended` is the
start and end pair Pi's source emits.
