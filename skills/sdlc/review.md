# Stage: review

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no ungranted governance.

You read the request, Tom's answers and feedback, the plan, the diff, and
the docs at the candidate as the contract, never the builder's narration.
Run the checks yourself: the kernel set up your checkout and its services,
so the suite and single tests run here. Ask over the whole diff: does this
add a check, gate, hook, round, or review step?

**Inputs** (`.valor/inputs/`):

- `request.md`; `answers.md`, Tom's answers and feedback;
- `plan.md`, the critiqued plan;
- `diff.patch`, base to candidate;
- `verify.json`, the kernel's own run of the suite and the lint at base
  and candidate, and your checkout's setup exit;
- `governance.json`, each hunk the kernel judged to add governance (id,
  path, lines, the added lines, granted or not), the abstentions, and any
  hunk left unjudged;
- `effects.md`, the task's held, released, and refused effects.

**Exit evidence.** Your final message ends with the verdict object, bare
or in a fenced json block; prose before it is read past. The kernel reads it from your session's result, which
nothing the checks you run can write; a file in the checkout can be
rewritten by the candidate's code to the end of your turn, so the kernel
reads no verdict file here. The object:

- `verdict`: `pass` or `changes`, your judgement of the work;
- `findings`: each with a kind (`debt` for related tech debt worth
  paying now);
- `governance`: each instance you find that the kernel did not, as
  `{"path", "line", "summary", "incident", "mission_item"}`, with a line
  inside an added hunk;
- `notes`: keyed by an id from `governance.json`, `{"summary",
  "incident", "mission_item"}`, what the diff gives for that instance;
- `predicted_failure`: 0 to 1, how likely this candidate fails once
  merged;
- `requirements`: one `{"requirement", "met"}` per requirement of the
  request.

The kernel computes the recorded verdict from yours, the instances, and
Tom's grants: a `pass` with an instance not yet granted is recorded as
`governance_refused` and waits for Tom; a `changes` lists each such
instance as a finding.
