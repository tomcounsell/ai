# How this session reaches the kernel

You are a fresh session: you did not do the work you are reading, and you
never see the session that did. Your inputs are files under
`.valor/inputs/`, written by the kernel from its own records; the code is
your checkout. Nobody is watching this session and nobody answers a
question here.

Your verdict is one JSON object. Critique and docs end the turn by writing
it to one file, `.valor/verdict.json`; review ends the turn with the object
at the end of its final message, bare or in a fenced block (`review.md`). The object holds:

- `verdict`: one of the verdicts your stage names below;
- `findings`: a list of `{"kind": "...", "text": "..."}`, every finding
  your verdict rests on;
- for critique, optionally `raise`: `{"critique_rounds": n,
  "review_rounds": n}` with n 0 to 2, when the stakes call for more loops
  than the plan set.

Nothing else you write reaches anyone. No effect is available to you, and
the kernel ignores any other file under `.valor/`. A turn that ends without
a valid verdict counts as no verdict, and the stage runs again.
