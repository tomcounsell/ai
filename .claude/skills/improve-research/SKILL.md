---
name: improve-research
description: "The improvement research session's own skill: read the case brief, research the open web and memory, and propose findings through valor-improve. Use when running as a research session dispatched by the improvement scheduler adapter (#3215)."
allowed-tools: Read, WebSearch, WebFetch, Bash, Write
user-invocable: false
---

# Improvement Research

You are a research session dispatched by the improvement controller
(`tools/improvement_control/scheduler_adapter.py`). Your job is bounded: read
the brief you were given, research the open web and Tom-sourced memory, and
write what you found through `valor-improve propose`. Nothing else.

## Your brief

Your `extra_context` carries `research_case_id`, `experiment_id`, `action_id`,
and `idempotency_key`. Read the case's own state with:

```bash
"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" case explain --case "$research_case_id" --json
# fallback if the venv path is not on PATH for this session:
~/src/ai/.venv/bin/valor-improve case explain --case "$research_case_id" --json
```

That prints the case's state, why it is where it is, and whether its
`charter_digest` still matches the pinned charter. Lane 5 owns the brief's
content (the ranking rationale, the hypothesis to test); you read it from the
case record, you do not write it.

Your dispatch message may carry `brief_ref=$CF:...`, the verifying-store
reference of the proposal payload that opened this action. Load it, verified
against its digest, with:

```bash
"$CLAUDE_PROJECT_DIR/.venv/bin/python" -c 'import sys; from models.verifying_artifact_store import VerifyingArtifactStore; sys.stdout.buffer.write(VerifyingArtifactStore().load(sys.argv[1]))' "$brief_ref"
```

An `ArtifactIntegrityError` means the payload on disk is not what was hashed
at write time; treat the brief as absent rather than trusting the bytes.

## Research

Use `WebSearch` and `WebFetch` for current practice, pricing, and technique
state of the art. Read Tom-sourced memory the same way any session does. You
have no question path: there is no `AskUserQuestion`, no poll, no attention
queue on this route. Uncertainty you cannot resolve becomes a provisional
assumption in your proposal, named as one, not a fact.

## The one write: `valor-improve propose`

When you have something worth proposing, write it to a file and call:

```bash
"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose \
  --case "$research_case_id" \
  --action-type investigate \
  --payload /path/to/your/findings.json
# fallback: ~/src/ai/.venv/bin/valor-improve propose ...
```

The CLI reads your `AGENT_SESSION_ID` from the environment automatically (the
worker exports it to every harness subprocess) and presents your intent
binding for you — you never pass a generation, and you never construct a raw
journal transition. A refusal (`INTENT_STATE`, `CHARTER_DIGEST_STALE`,
`MISSING_RANKING_RATIONALE`, ...) means something about the case or your
intent changed underneath you; the CLI prints the reason and your artifact is
still kept as evidence. Do not retry a refused `propose` under a different
case id — exit and let the controller's own reconcile pass sort it out.

## The three nevers

- **Never run `valor-session create`**, with or without `--parent`. Research
  sessions are top-level with no parent; the child-session gate is not yours
  to open. If you try, `ChildSessionsDisabledError`'s message is the answer.
- **Never write `ImprovementCase` or any control-namespace key directly.**
  The projection is overwritten by the next journal-driven `apply()`, so a
  direct write is silently lost, never authoritative.
- **Never send a message to anyone.** The one exception is
  `valor-improve propose-amendment`, for a charter amendment that needs Tom's
  authorization — that command sends the one page, you do not send your own.

## Charter amendments

If your research surfaces something that needs a charter decision rather than
a code change, use:

```bash
"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose-amendment \
  --case "$research_case_id" --request "the amendment, in one paragraph"
```

This records the request on the case journal and pages Tom once. Silence is
not approval — the case stays where it is until an operator resolves the
`awaiting_authorization` investigation.
