---
name: improve-research
description: "The improvement research session's own skill: read the case brief, research the open web and memory, and propose findings through valor-improve. Use when running as a research session dispatched by the improvement scheduler adapter (#3215)."
allowed-tools: Read, Write, Bash, WebSearch, WebFetch
user-invocable: false
---

# Improvement Research

You are a research session dispatched by the improvement controller
(`tools/improvement_control/scheduler_adapter.py`). Your work is bounded: read
the brief, investigate through the recorded kinds, revise the system's model
of itself when the evidence changes it, propose one hypothesis inside the
envelope, and run its experiment. Every write goes through `valor-improve`.

Your dispatch message is `/improve-research case=... action=... type=...`, so
`$CASE_ID` is the `case=` value; the same id is in
`extra_context.research_case_id`, beside `action_id` and `idempotency_key`.
The CLI lives in the project venv only:

```bash
VI="$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve"
# fallback if the venv path is not on PATH for this session:
# VI=~/src/ai/.venv/bin/valor-improve
```

## Step 1: read the brief

```bash
"$VI" brief --case "$CASE_ID"
```

The brief opens with the pinned charter verbatim, then names its version and
digest, then gives the case (title, summary, priority area, ranking rationale,
ranking position and factors), its evidence, prior answers in the same area
(resolved investigations and rejected cases with their evaluation ids), the
open investigations, the intake pool for the area, the charter §9 resolution
rule, the claim rule, the candidate envelope, and the subcommands you may use.
Read all of it before opening anything. A prior answer that already covers
your question is the answer; do not re-ask it.

When the dispatch message carries `brief_ref=$CF:...`, that is the verifying
store reference of the proposal payload that opened this action. Load it,
verified against its digest, with:

```bash
"$CLAUDE_PROJECT_DIR/.venv/bin/python" -c 'import sys; from models.verifying_artifact_store import VerifyingArtifactStore; sys.stdout.buffer.write(VerifyingArtifactStore().load(sys.argv[1]))' "$brief_ref"
```

An `ArtifactIntegrityError` means the payload on disk is not what was hashed
at write time; treat that payload as absent rather than trusting the bytes.

## Step 2: investigate through the eight kinds

Open one investigation per uncertainty, stating what you do not know, what
you will run, which decision the answer changes, and what knowing it is worth:

```bash
"$VI" investigation open --kind web_research --case "$CASE_ID" \
  --uncertainty "..." --query "..." --decision-affected "..." \
  --expected-information-value "..."
```

The eight kinds and what each may use:

| Kind | What it records | What runs it |
|------|-----------------|--------------|
| `web_research` | the query, URLs, retrieval dates, claims | `WebSearch`, `WebFetch` |
| `memory_retrieval` | the `memory_search` query and the memory ids read | `python -m tools.memory_search search "..." --project valor` |
| `trace_analysis` | the session or event ids read and what they showed | `Read` on transcripts and event logs |
| `probe` | the command run (bounded, recorded verbatim) and its observed result | one bounded `Bash` command |
| `resource_acquisition` | provider, documentation URLs and dates, price, the §7 terms (training on inputs, retention), adapter cost, and a disposition: `prepared`, `keyless_integrated`, `vault_request_written`, `unsuitable` | `WebSearch`, `WebFetch` |
| `inspiration_intake` | the source URL, the extraction route, the extracted substance, `accessible: true/false` | `valor-youtube-transcribe`, `valor-ingest`, `WebFetch` |
| `skill_acquisition` | the observed gap (evidence ids), candidates with URLs and dates, the vetting result, the integration made or proposed, the evaluation disposition (`deferred: no agent-run arm` in this lane) | library search and the web |
| `charter_amendment` | the request text; `state=awaiting_authorization` | only `propose-amendment` (below), never `investigation open` |

An inaccessible inspiration source is recorded as inaccessible, never as
reviewed. The intake pool in the brief lists `inspiration_intake` rows with no
case in your area: work them if they bear on your case, and `case open` when
the extracted substance deserves a case of its own.

Record what you found as you go:

```bash
"$VI" investigation record --id "$INV_ID" --claims @/path/to/claims.json
```

## Step 3: the claim rule and the assumption rule

**A claim is `{"claim", "url", "retrieved_at"}`**: non-blank text, an http(s)
URL, and an ISO-8601 retrieval date. Anything short of that is stored as a
note (`is_claim: false`), never dropped and never promoted. Write notes freely;
never dress a note as a claim.

Resolve each investigation with what you concluded and what would change it:

```bash
"$VI" investigation resolve --id "$INV_ID" --interpretation "..."
```

When the uncertainty cannot be resolved, proceed on a **provisional
assumption** and say so:

```bash
"$VI" investigation resolve --id "$INV_ID" --interpretation "..." \
  --assumption "..." \
  --assumption-detail '{"charter_passage": "...", "evidence_ids": [...], "confidence": "...", "consequence": "...", "overturning_observation": "..."}'
```

All four of `charter_passage`, `confidence`, `consequence`, and
`overturning_observation` are required. The consequence may not redefine the
intended outcome, erase a requirement, grant authority, or increase a budget;
the CLI refuses `ASSUMPTION_EXCEEDS_AUTHORITY` when it reads that way, and the
answer is to defer the decision and file `propose-amendment` (step 5). The
assumption's summary is copied onto the case so it outlives the row.

A `resource_acquisition` that needs a credential ends as a written vault
request, never a placed credential:

```bash
"$VI" investigation resolve --id "$INV_ID" --interpretation "..." \
  --disposition vault_request_written --resource-name meta_model_api
```

The resource name must be one of `tools.improvement_resources.RESOURCES`
(`UNKNOWN_RESOURCE` otherwise). The case is then blocked on `vault:<name>`
until the tick's read-only probe finds the item, and the request reaches Tom
in the three-day digest.

## Step 4: revise the model when the evidence changes it

When what you found changes the system's model of itself (how it behaves,
what it costs, where it fails), write a revision with a prediction that can be
wrong:

```bash
"$VI" revise-model --case "$CASE_ID" --summary "..." --rationale "..." --prediction "..."
```

A revision with an empty prediction is refused `EMPTY_PREDICTION`: it would be
a note, and the loop treats it as one.

## Step 5: propose a hypothesis, or an amendment

Propose one hypothesis with its mechanism and its falsifier, inside the
envelope the brief names (`retrieval_parameters`: `limit` 1..50, `rrf_k`
1..200, `min_rrf_score` 0.0..1.0; the incumbent is exactly `{"limit": 10}`):

```json
{
  "hypothesis": "...",
  "mechanism": "...",
  "falsifier": "...",
  "candidate": {"limit": 20, "rrf_k": 60},
  "envelope": "retrieval_parameters"
}
```

```bash
"$VI" propose --case "$CASE_ID" --action-type investigate --payload /path/to/proposal.json
```

The CLI reads your `AGENT_SESSION_ID` from the environment and presents your
intent binding for you; you never pass a generation and never construct a raw
journal transition. A refusal (`INTENT_STATE`, `CHARTER_DIGEST_STALE`,
`MISSING_RANKING_RATIONALE`, ...) means the case or your intent changed
underneath you; the CLI prints the reason and your artifact is kept as
evidence. Do not retry a refused `propose` under a different case id. Exit and
let the controller's own reconcile pass sort it out.

When the decision depends on authority the charter has not granted, do not
narrow the goal to fit. Defer it and ask through the one sanctioned page:

```bash
"$VI" propose-amendment --case "$CASE_ID" --request "the amendment, in one paragraph"
```

This records a `charter_amendment` investigation in `awaiting_authorization`
and pages Tom once. Silence is not approval; continue the work you are
authorized to do.

## Step 6: freeze, evaluate, report

```bash
"$VI" experiment freeze --case "$CASE_ID"          # validates the candidate, exports the corpus, freezes the protocol
"$VI" experiment evaluate --id "$EXP_ID" &         # in the background; the harness writes the verdict
"$VI" experiment show --id "$EXP_ID"               # poll until state leaves running
"$VI" report --case "$CASE_ID"                     # the qualified-result report
```

A freeze refusal (`KNOWN_ITEM_SHORTFALL`, an envelope violation, a candidate
identical to the incumbent) is the end of that hypothesis, not a reason to
edit the candidate until it passes.

## The six prohibitions

These hold for the whole session, in this skill's own words:

1. **No `AskUserQuestion`.** There is no question path on this route: no poll,
   no attention queue, no message to Tom. Unresolved uncertainty is a
   provisional assumption, named as one.
2. **No Telegram send.** The only page a research session can cause is the one
   `propose-amendment` sends.
3. **No session creation.** Never run `valor-session create`, with or without
   `--parent`. Research sessions are top-level with no parent.
4. **No `.env` write.** Secrets live in the vault; a needed credential becomes
   a written vault request.
5. **No `op` invocation.** The controller never reads or places a credential;
   the tick's read-only probe is the only vault reader.
6. **No direct `ImprovementCase` or control-namespace write.** The projection
   is overwritten by the next journal-driven `apply()`, so a direct write is
   silently lost. `valor-improve` is the only door.
