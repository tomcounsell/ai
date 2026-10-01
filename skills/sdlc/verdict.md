# How this session reaches the kernel

You are a fresh session: you did not do the work you are reading, and you
never see the session that did. Your inputs are files under
`.valor/inputs/`, written by the kernel from its own records; the code is
your checkout. Nobody is watching this session and nobody answers a
question here.

End your turn by writing one file, `.valor/verdict.json`, a JSON object:

- `verdict`: one of the verdicts your stage names below;
- `findings`: a list of `{"kind": "...", "text": "..."}`, every finding
  your verdict rests on;
- for critique, optionally `raise`: `{"critique_rounds": n,
  "review_rounds": n}` with n 0 to 2, when the stakes call for more loops
  than the plan set.

Nothing else you write reaches anyone. No effect is available to you, and
the kernel ignores any other file under `.valor/`. A turn that ends without
a valid `verdict.json` counts as no verdict, and the stage runs again.
