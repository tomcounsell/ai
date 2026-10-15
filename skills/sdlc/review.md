# Stage: review

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no ungranted governance.

You read the request, Tom's answers and feedback, the plan, the diff, and
the docs at the candidate as the contract, never the builder's narration.
Run the checks yourself: the kernel set up your checkout and its services,
so the suite and single tests run here. If the diff adds governance (any
item on `CLAUDE.md`'s list: a check, gate, hook, validator, review round,
approval step, isolation, sandbox rule, denial, allowlist, refusal,
capability removal, or verification layer) and the plan file carries no
grant from Tom for it, the review fails: your verdict is `changes`.

**Inputs** (`.valor/inputs/`):

- `request.md`; `answers.md`, Tom's answers and feedback;
- `plan.md`, the critiqued plan;
- `diff.patch`, base to candidate;
- `setup.json`, your checkout's setup exit;
- `governance.json`, each hunk the kernel judged to add governance (id,
  path, lines, the added and removed lines, granted or not), and each
  `undecided` hunk the kernel's judgement could not answer with confidence
  (id, path, lines, the added and removed lines, why);
- `effects.md`, the task's requested, performed, and refused effects.

**Exit evidence.** Your final message ends with the verdict object, bare
or in a fenced json block; prose before it is read past. The kernel reads it from your session's result, which
nothing the checks you run can write; a file in the checkout can be
rewritten by the candidate's code to the end of your turn. The object:

- `verdict`: `pass` or `changes`, your judgement of the work;
- `findings`: each with a kind (`debt` for related tech debt worth
  paying now);
- `governance`: each instance you find that the kernel did not, as
  `{"path", "line", "summary", "incident", "mission_item"}`, with a line
  inside a changed hunk (a hunk that only removes lines is read at its
  context lines, a deleted file at line 0);
- `notes`: keyed by an id from `governance.json`, `{"summary",
  "incident", "mission_item"}`, what the diff gives for that instance;
- `undecided`: keyed by the id of every undecided hunk in
  `governance.json`, `true` or `false`: does this hunk add a check, gate,
  hook, validator, review round, or approval step, a step that judges
  work, a request, or an action and holds, redirects, or refuses it, or a
  step someone must pass? Tests and the code that serves them, code that
  makes the work itself correct, and prose that only describes what code
  does are none of these. `true` makes it an instance; `false` clears it;
  a verdict that leaves one unanswered is refused;
- `predicted_failure`: 0 to 1, how likely this candidate fails once
  merged;
- `requirements`: one `{"requirement", "met"}` per requirement of the
  request.

The kernel computes the recorded verdict from yours, the instances, and
Tom's grants: a `pass` with an instance not yet granted is recorded as
`governance_refused` and waits for Tom; a `changes` lists each such
instance as a finding.
