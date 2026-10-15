# Stage: review

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no governance without Tom's grant.

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
- `effects.md`, the task's requested, performed, and refused effects.

**Exit evidence.** Your final message ends with the verdict object, bare
or in a fenced json block; prose before it is read past. The kernel reads it from your session's result, which
nothing the checks you run can write; a file in the checkout can be
rewritten by the candidate's code to the end of your turn. The object:

- `verdict`: `pass` or `changes`, your judgement of the work;
- `findings`: each with a kind (`debt` for related tech debt worth
  paying now);
- `predicted_failure`: 0 to 1, how likely this candidate fails once
  merged;
- `requirements`: one `{"requirement", "met"}` per requirement of the
  request.
