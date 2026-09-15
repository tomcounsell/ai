---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-15
tracking: https://github.com/tomcounsell/ai/issues/3330
last_comment_id: none
---

# Voice Interview for Open Questions

## Problem

Unanswered architect questions are the current throttle on delivery velocity.
Work sits in `docs/plans/` waiting on a decision only the owner can make, and the
only channels for getting those decisions are typing in a local Claude Code
session or answering a Telegram poll. The queue drains slower than it fills, so
lanes stall on questions rather than on code.

The target scenario: the owner is away from a keyboard, has fifteen minutes, and
would rather talk. Valor plays each outstanding question as a voice note, the
owner answers by voice, and the answers land in the plan docs that asked them.
Intended cadence is a few calls per week.

Two things block that today.

**First, the corpus is not machine-readable.** No tool enumerates open questions
across plan docs and issues. The one extractor that exists,
`bridge/message_drafter.py:100` `_extract_open_questions`, reads agent stdout
rather than files, and its naive item filter would emit false positives on the
real corpus — the "defaulted" items in `improvement-controller-lane-3` and
`lane-5` live under `## Open Questions` but are explicitly not asks. Knowing what
is outstanding currently requires a human to remember which plan asked what.

**Second, there is no spoken channel at all.** Outbound synthesis exists
(`tools/tts/`, `/do-debrief`) and inbound transcription exists
(`tools/transcribe/`, wired into inbound voice notes at `bridge/media.py:461`),
but they have never been joined into a question-and-answer loop, and every
synthesized clip is deleted immediately after send — `bridge/telegram_relay.py`
unlinks the file on successful delivery when `cleanup_file` is set (verified at
`bridge/telegram_relay.py:690`).

**Scope boundary, load-bearing.** This feature is per-project and per-machine.
Each project in `projects.json` is owned by exactly one machine, and this tooling
operates on **one repo on the machine that owns it**, with its own calling link
tied to that project. Other machines get the capability by the normal
global-tooling sync and harvest their own repo. Cross-project harvesting is an
explicit non-goal.

**Current behavior:**

- No command answers "what open questions exist right now, and which block the
  most work?"
- `/ask-me` drafts its blocker list from model context per invocation; nothing is
  harvested from disk, so the list is not reproducible between runs.
- `tools/tts.synthesize(text, output_path, ...)` writes a fresh OGG/Opus file per
  call; the documented send path passes `--cleanup-after-send` and the clip is
  unlinked after delivery. There is no clip library and no content-keyed audio
  cache anywhere in the repo.
- Answers arrive as chat prose. Getting them back into the plan doc that asked
  the question is manual.

**Desired outcome:**

- `python -m tools.open_questions list` prints every genuinely open question
  across `docs/plans/*.md` and this repo's open issues, each with a stable
  content-hash ID, source path and line, and an unblock-leverage score.
- The owner receives questions as Telegram voice notes, one at a time,
  highest-leverage first, and answers by replying with a voice note. Answering one
  question can prune others its answer moots, so the session ends early rather
  than reading the whole list.
- Recorded answers land in the `## Open Questions` section of the plan doc that
  asked them, attributed and dated, committed to `main`.
- Re-running the harvester after a session shows the answered questions resolved
  and does not re-ask them.

## Freshness Check

**Baseline commit:** `b13dc8ad3078381bce5fb37fc3502def37c7e654`
**Issue filed at:** 2026-09-15T02:34:29Z
**Disposition:** Unchanged

The issue was filed within the hour and `git log --since` on `origin/main` shows
zero commits since. The skill permits skipping this step under exactly those
conditions, but #3330's file:line citations came from parallel recon subagents
rather than from direct reads, so every load-bearing one was re-verified by hand
against the baseline commit.

**File:line references re-verified:**

- `bridge/message_drafter.py:100` — `_extract_open_questions`, heading regex
  excluding Resolved/Answered/Closed/Done at `:123` — still holds, exact.
- `bridge/telegram_relay.py:651-700` — voice-note branch gated on
  `message.get("voice_note") and len(available) == 1` at `:656`,
  `DocumentAttributeAudio(..., voice=True)`, and `_safe_unlink(voice_path)` under
  `if message.get("cleanup_file")` — still holds. The unlink is opt-in, which is
  what makes a persistent clip library possible without touching the relay.
- `tools/plan_doc_scope.py:19` — `NON_LANE_PLANS` frozenset with the two named
  exclusions (`session-recovery-observation-audit.md`,
  `resilience-simplification-three-tier.md`) — still holds.
- `scripts/check_issue_disposition.py:86` — `EXEMPT_PREFIXES = ("docs/plans/",
  "docs/archive/plans-completed/")`, applied at `:183` — still holds.
- `.claude/hooks/manifest.toml:133-167` — the four PostToolUse plan validators
  (`validate_documentation_section`, `validate_test_impact_section`,
  `validate_verification_section`, `validate_no_gos_justification`), each
  `matcher = "Write"` with `exit_policy = "propagate"` — still holds, and was
  confirmed live: the first Write of this very plan was blocked by
  `validate_verification_section` for using a checklist instead of a table.
- `tools/tts/__init__.py:23-38` — `SUPPORTED_FORMATS = {"opus"}`,
  `MAX_TEXT_LENGTH = 4096`, and `KOKORO_VOICES` — still holds, and the catalog
  still does not contain `bf_alice` (see Prerequisites).
- `tools/tts/__init__.py:360` — `synthesize(text, output_path, ...)`: the **caller
  controls the output path**. Not stated in the issue; it is the fact that makes a
  content-addressed clip library a pure add-on with no change to `tools/tts`.

**Cited sibling issues/PRs re-checked:** #2701/#3080 (poll channel, shipped),
#3095 (open poll backlog), #2650 (closed, protocol never built), #1136/PR #1183
(TTS module, shipped), #2902 (open, text-out-only), #1688/PR #1847 (shipped).
Dispositions unchanged from the issue's Prior Context.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none. No plan mentions a
voice interview, a question harvester, or a clip cache. The three
`improvement-controller-lane-*` plans are the largest live docs and are the
richest source of *input* questions for this harvester, but they do not overlap
its implementation.

**Notes:** `timeout` is not available on this macOS shell; the blast-radius tool
was run without it.

## Prior Art

Searched closed issues and merged PRs for voice, TTS, transcription, interview,
and open-question keywords. One direct hit on the audio primitives, several on the
surrounding question-channel work.

| Work | State | Relevance |
|---|---|---|
| PR #1183 / #1136 — TTS module + `/do-debrief` | Merged | The only prior audio-synthesis work. Supplies `tools/tts.synthesize()`, Kokoro ONNX primary with OpenAI `tts-1` fallback, OGG/Opus only. This plan consumes it unchanged. |
| #2701 / PR #3080 — `/ask-me` questions as native Telegram polls | Shipped | The existing asynchronous decision channel and this work's closest sibling. Feature doc: `docs/features/telegram-poll-questions.md`. The voice channel is a second transport over the same need, not a replacement. |
| #3095 — poll-feature deferrals | Open, one item already shipped | Live backlog on the poll channel, including owner decisions. Load-bearing: this plan must not leave a second half-finished question channel standing next to it. Its `FloodWaitError`-swallowing item landed in `24f9ad507` (PR #3138, merged 2026-09-05) with `Refs #3095`, verified against `main` and noted on the issue; the `validate_poll_question` two-enforcement-points item is the one that reaches M3 (see Open Question P2). |
| #2650 — plan-doc writes in the shared `main` checkout have no single-writer protocol | Closed, protocol never built | **The governing hazard for writeback.** Catalogues seven collision shapes with SHAs, including an unrecoverable 2026-08-13 incident where a plan-doc agent's `git add -A` swallowed another lane's staged code onto `main` under an unrelated issue reference. |
| #1688 / PR #1847 — hook-driven turn returns | Shipped | The `AskUserQuestion` PreToolUse `needs_human` edge and the `pause_open_question` router verdict. A voice session must wait on this existing edge rather than invent a mechanism. |
| #2902 — meeting participant, joins invited Google Meets | Open, explicitly text-out-only | Nearest in-flight audio work. No overlap: it never speaks, and this never joins a call. |

### Why Previous Fixes Failed

Not applicable in the usual sense — no prior attempt at a voice question channel
exists. But one prior fix is *relevant by its absence*: #2650 correctly diagnosed
the plan-doc concurrency hazard and was closed without the mitigation being built,
and the plan that would have built it
(`docs/archive/plans-completed/plan-doc-single-writer-lease.md`) sits in the
completed-plans archive despite never being built, because
`scripts/migrate_completed_plan.py:384-412` reads GitHub's `state` and never
`stateReason` (filed as #3332). The lesson this plan takes from that: do not stake the writeback
step on a protocol that does not exist. Writeback is sequential by construction so
that it needs no lock.

## Research

Two searches, both aimed at assumptions the codebase cannot answer on its own.

**Query 1: "Telegram Bot API voice note reply_to_message_id binding inbound audio
reply to specific message"**

Finding: a reply target is a first-class field on every voice send and survives
onto the inbound reply. Legacy Bot API exposed `reply_to_message_id`; current
versions moved it into a `ReplyParameters` object with `message_id`. The inbound
update carries the replied-to message id, so pairing an answer to the question it
answers needs no audio timestamp alignment. Telegram also allows re-sending an
already-uploaded file by `file_id`, avoiding re-upload — with the caveat that a
`file_id` obtained via `sendAudio` is not interchangeable with `sendVoice`.
Source: <https://core.telegram.org/bots/api>,
<https://core.telegram.org/bots/api-changelog>.

How it informs the approach: this repo speaks MTProto through Telethon, not the
Bot API, so the field names differ (`reply_to` on send, `reply_to.reply_to_msg_id`
inbound), but the capability is the same and the conclusion carries. The plan
therefore treats reply-to as **corroborating** evidence rather than the primary
binding: the session tracks exactly one outstanding clip at a time, so the next
inbound voice note is the answer positionally, and a present `reply_to_msg_id`
that disagrees with the outstanding clip is a hard mismatch signal rather than the
only way to bind. The `file_id` reuse path is noted as a future optimization and
explicitly out of scope — the clip library is a local-file cache, not a Telegram
file-id cache.

**Query 2: "kokoro-onnx deterministic output same text same voice reproducible
audio"**

Finding: Kokoro ONNX inference has no sampling or temperature randomness and is
bit-exact across runs for a fixed execution provider, dtype, and model file —
incidentally established by
<https://github.com/Microsoft/onnxruntime/issues/29807>, which reports
byte-identical corrupt output across independent sessions on a WebGPU EP.
Determinism does **not** hold across execution providers, quantization dtypes,
model/voice file versions, or espeak-ng / G2P versions; the phonemizer runs
*before* the ONNX graph and is the single largest drift source. No official
determinism guarantee exists from the project.
Sources: <https://github.com/Microsoft/onnxruntime/issues/29807>,
<https://github.com/thewh1teagle/kokoro-onnx>,
<https://pypi.org/project/kokoro-onnx/>.

How it informs the approach: the clip cache does not actually require
determinism — it is a reuse cache, not a verification cache. What it requires is
that the **key covers every input that affects output**, so a config change
invalidates rather than silently serving a stale clip in the wrong voice. The
cache key is therefore `sha256(spoken_text) + voice + speed + backend identity +
model/voice file fingerprint`, not the text hash alone, and the backend identity
must be recorded *after* dispatch because `tools/tts` falls back from Kokoro to
OpenAI `tts-1` without the caller choosing. Both findings are saved to the agent
memory store for reuse.

## Spike Results

Four read-only spikes, dispatched in parallel, all returned. Three of the issue's
five Open Questions are resolved by evidence and do not go to the human. The
footprint of the plan shrank as a result: no new skill, and the delivery half
becomes a branch inside machinery that already ships.

### spike-1: Does an answer bind to the question it answers without timestamp alignment?

- **Assumption:** "An inbound voice-note reply carries enough metadata to bind the
  answer to the specific question clip it answers."
- **Method:** code-read
- **Result:** **PARTIALLY CONFIRMED.** The inbound half is free; the outbound half
  is not. `store_message(..., reply_to_msg_id=message.reply_to_msg_id, ...)` at
  `bridge/telegram_bridge.py:1579` persists the reply target durably
  (`models/telegram.py:47`), and `bridge/context.py:745-833` already walks it for
  session rooting. But **nothing in the repo maps a clip's message id to the
  question that clip asked.** The outbound history write
  (`bridge/telegram_relay.py:1499-1515`) passes `content=message.get("text","")`
  and neither a message id nor a reply target; for a voice note the caption is
  empty, so the row is contentless. `format_reply_chain`
  (`bridge/context.py:632-690`) drops `message_id` at format time, so the clip
  reaches the agent as an anonymous attachment marker.
  **The seam that closes it already exists:** `--ack-sent-id`
  (`tools/valor_telegram.py:1437-1443`) sets `payload["ack_sent_id"]`, the relay
  calls `publish_sent_message_id` (`bridge/telegram_relay.py:1463-1467`) writing
  `telegram:sent:{session_id}`, and `await_sent_message_id`
  (`bridge/outbox_ack.py:65-95`) reads it back — single-consumer, delete-on-read,
  120s TTL (`bridge/outbox_ack.py:48`).
- **Confidence:** high
- **Impact:** the plan builds a **session-owned clip registry**: send with
  `--ack-sent-id`, read the ack immediately, record `{telegram_msg_id → qid}` in
  the interview state file. This needs **no relay change**. The spike's alternative
  — teaching `store_message` to persist the outbound voice note's question text —
  is rejected as a larger change to a hot path for a benefit one caller needs.
  Positional binding (exactly one clip outstanding) is the fallback when the ack is
  missed or the owner does not use reply; a disagreement between the two stops the
  traversal rather than guessing. No timestamp alignment anywhere.
  Side effect worth naming: because clips are sent **without** `cleanup_file`, the
  file survives, so the reply-chain hydration renders a readable attachment marker
  instead of `[unreadable attachment: ... reason: file_missing]`
  (`bridge/context.py:429-431`).

### spike-2: Can a content-addressed clip library sit on top of `tools/tts` unchanged?

- **Assumption:** "A persistent content-addressed clip library needs no change to
  the synthesis API, and clips can be sent over Telegram without being deleted."
- **Method:** code-read
- **Result:** **PARTIALLY CONFIRMED.** Persistence and send paths are clean; the
  cache key has one real hole.
  - `synthesize(text, output_path, voice="default", format="opus",
    force_cloud=False)` at `tools/tts/__init__.py:360` — `output_path` is required
    and positional, no tempdir default. Returns
    `{path, duration, backend, voice, format}`, or **`{"error": ...}` alone** with
    none of the other keys on failure (`:389, :391, :393, :411`). No audio cache
    exists; the only cache is a 60s TTL on the Kokoro *availability probe*
    (`:82-87`).
  - Cleanup is opt-in and already regression-tested: `payload["cleanup_file"]` is
    set only inside the `--cleanup-after-send` branch
    (`tools/valor_telegram.py:1079-1092`), all three relay unlink sites are gated
    on it (`bridge/telegram_relay.py:690-691, :739, :965-970`), and
    `tests/unit/test_telegram_relay_voice_note.py:117-132` already asserts "file
    must survive when cleanup_file is not set."
  - Re-sending the same local file is safe: Telethon opens it read-only, nothing
    transcodes in place, no unique-filename requirement, and duration is recomputed
    by a read-only `ffprobe`.
  - **The hole:** the backend that actually produces the audio is **not knowable
    before the call.** `use_kokoro = (not force_cloud) and _is_kokoro_available()`
    (`:397`) is a stateful, time-windowed probe that runs a live one-character
    synthesis and can flip between two identical calls; and even after Kokoro is
    picked, a synthesis-time error falls back to cloud mid-call and re-resolves the
    voice, so a requested `af_bella` silently becomes `nova` via
    `_VOICE_FALLBACK_MAP` (`:47-66`). The result dict reports ground truth, but
    only after the fact.
- **Confidence:** high
- **Impact:** the earlier design of putting backend identity *in* the key is wrong
  — it would require predicting `_is_kokoro_available()`. Corrected design: key on
  requested inputs plus a recipe-version salt, and validate the recorded actual
  backend **on read**:
  `sha256("v1|{text}|{requested_voice}|{force_cloud}|opus")` →
  `data/clips/<hash>.ogg` with a `<hash>.json` sidecar recording actual backend,
  actual voice, and duration. A hit whose sidecar backend disagrees with what the
  caller now wants is treated as a miss. The `v1` salt covers the constants a
  caller cannot see (`speed=1.0` at `:219`, `-b:a 24k` at `:253`,
  `kokoro-v1.0.onnx` at `:78`, `tts-1` at `:297`) and is bumped when any change.
  Location `data/clips/` needs no gitignore work: `data/` is ignored at
  `.gitignore:181` and `*.ogg` is ignored globally as well.

### spike-3: What does normalizing the resolution convention actually cost?

- **Assumption:** "Normalizing the corpus to one resolution convention is cheaper
  than teaching a harvester every existing shape."
- **Method:** code-read (full corpus pass)
- **Result:** **FALSE.** Normalizing costs **~50 item edits across 15 of 24 docs**
  (plus ~5 more inside two `Decisions Recorded` sections); 6 of the 15 need a
  heading rename and all 15 need item-level marker work. Seven of the 24 docs have
  no question section at all. Six distinct heading strings are in use
  (`## Open Questions` ×11, `## Resolved Questions`, `## Resolved Decisions` ×2,
  `## Decisions (Owner, …)`, `## Decisions Recorded`, and one H3
  `### Schema Gate — Open Decisions from Spikes`). No doc uses a `RETIRED`
  disposition.
  Two findings flip the decision:
  1. **Four of the nine shapes are already mechanically distinguishable** —
     the blockquote `> **RESOLVED …**` banner, strikethrough
     (`sdlc-control-plane-asserted-facts.md:1259` item 4), the `Default:` preamble,
     and the `**Disposition:** … Overturned by:` pair. The defaulted items this
     plan most feared are the *easiest* to detect: `lane-3:787` says verbatim
     "Silence keeps the default" and every item carries `Default:`;
     `lane-5:1882` pairs `**Disposition:**` with `Overturned by:`. **No edit is
     needed for a harvester to classify them as not-asks.**
  2. **The root cause is `/do-plan` itself.**
     `.claude/skills-global/do-plan/SKILL.md:399` Phase 4 Finalize says
     "**remove Open Questions section**". `PLAN_TEMPLATE.md:495` mandates the
     heading but says nothing about resolution marking. Every retained-and-marked
     shape in the corpus is a deviation authors invented *because deletion destroys
     the decision record*. Nine shapes exist because the official instruction is to
     delete.
  Also confirmed: **no hook or validator parses the section.** Zero occurrences of
  "question" across the 22 validators in `.claude/hooks/validators/`. The
  convention is entirely unenforced.
- **Confidence:** high
- **Impact:** resolves Open Question 1 against normalizing, **and resolves Open
  Question 4 as a side effect.** The plan teaches the harvester the existing shapes
  and adds a small fourth milestone that fixes the cause forward: amend
  `SKILL.md:399` to retain-and-mark instead of delete, and give
  `PLAN_TEMPLATE.md` one named convention. Retro-editing 15 settled docs on the
  shared `main` checkout — a known lane collision surface — for zero current
  mechanical consumer stays a No-Go.

### spike-4: Should the voice channel reuse `/ask-me`'s transport or sit beside it?

- **Assumption:** "A voice question channel needs its own skill and its own wait
  mechanism."
- **Method:** code-read
- **Result:** **FALSE — reuse.** The shipped poll architecture splits into three
  layers and only the middle one is Telegram-coupled:
  - **Rendering** (poll vs prose) is Telegram-coupled, and crucially it has
    **exactly one decision point**: `tools/ask_poll.py:141-164`.
    `.claude/skill-context/ask-me.md:76` forbids the skill from detecting the
    surface itself — "Always call `valor-ask-poll`; it degrades... Never try to
    detect the surface and branch by hand." So a voice surface is an edit to that
    CLI, **not** to any skill body.
  - **Ending the turn** is coupled to the *tool name*, not to polls:
    `_ASK_USER_MATCHER = "AskUserQuestion"` at
    `agent/session_runner/hook_edge.py:114`, the only PreToolUse → `needs_human`
    path at `:363-368`. A voice flow ends its turn with the identical trick
    (render via Bash, then `AskUserQuestion` as the turn's final act). Teaching the
    matcher about `Bash` is a **rejected** alternative per
    `docs/features/telegram-poll-questions.md:170-172` — do not revive it.
  - **Staying stopped** is already generic: `agent/output_router.py:180-181` is a
    pure function over a plain `has_open_question: bool`, and the Redis read lives
    in the caller (`agent/session_executor.py:1735-1741` calling
    `session_has_open_poll`). **This is the one seam to widen.**
  - **Resuming** is already transport-independent *on purpose*:
    `bridge/answer_routing.py:43-60, :94` (`AnswerTargetKind`, frozen
    `AnswerTarget`, `resolve_answer_target`), landed as its own revertible commit
    and documented as "poll-independent on purpose"
    (`docs/features/telegram-poll-questions.md:318-320`). An inbound voice message
    is already an ordinary steering message on this path — **no new plumbing.**
  - **There is no reusable question value object.** Option validation lives in
    `tools/ask_poll.py:52-88` `normalize_options`, the escape-hatch literal at
    `:39`, the text cap in `POLL_QUESTION_MAX_CHARS` imported from
    `bridge/message_drafter.py` at `:132-135`, and text rendering in
    `agent/output_handler.py:375-386`. A voice renderer without a shared model
    would re-implement the escape hatch and both caps.
  - `#3095` contains **nothing** about a second question mode or transport; it is
    hardening plus owner decisions on the shipped poll path. One of those decisions
    bears directly here: `validate_poll_question` violations are
    logged-and-discarded at `agent/output_handler.py:1560` but hard-exit at
    `tools/ask_poll.py:134` — **two enforcement points for one invariant**, which a
    second renderer would inherit unresolved.
- **Confidence:** high, with one stated gap: the spike did not read
  `bridge/poll_reconcile.py` or `bridge/poll_vote.py` directly, so its note that
  #3095's FloodWait item looks stale against source is doc-vs-issue inference, not
  source-verified. That does not affect this plan's conclusion.
- **Impact:** resolves Open Question 3 in favor of reuse and largely dissolves
  Open Question 5. **No new skill.** The generic `ask-me/SKILL.md` body does not
  change at all — it is already surface-agnostic. The plan instead: extract a
  shared frozen question model, add a voice branch inside the single degradation
  point in `tools/ask_poll.py` (never a second CLI), generalize
  `session_has_open_poll` → `session_has_open_question` while leaving
  `output_router`'s pure-function contract untouched, and add a voice row to
  `.claude/skill-context/ask-me.md`. Ranking is **not** reused: the harvester's
  disk-based leverage scoring replaces `/ask-me`'s context-derived ranking, which
  is the whole point of the harvester.

### What the spikes did not resolve

Open Question 2 (how defaulted questions are marked *going forward*) survives, but
much narrower than filed: the existing shapes are already greppable, so this is now
a convention choice bundled into the M4 template amendment rather than a blocker.
One new question is raised by spike-4 and is carried below: whether #3095's
two-enforcement-points item must be settled before a second renderer inherits it.

## Data Flow

Four stages. Stages 1-2 run before the call with no latency budget; stage 3 is the
only thing that runs while the owner is on the line; stage 4 runs after.

```
1. HARVEST  (offline, cheap, deterministic)
   docs/plans/*.md  ──┐
                      ├─► tools.open_questions.extract
   gh issue list ─────┘        │  heading discovery → item parse → disposition classify
   (this repo only)            ▼
                        list[OpenQuestion]  {qid, text, path, line, tracking, disposition}
                               │
                               ▼  tools.open_questions.score
                        leverage per question  (blocked plans + blocked issues + prunes)

2. PREPARE  (offline, expensive, LLM, generous by design)
   open questions ──► run_typed()  per question:
                        · spoken question text (no multi-digit identifiers)
                        · deeper-context clip text ("what's this about?")
                        · readback line
                        · plausible answer dispositions
                        · for each disposition: which other qids it moots
                        · draft writeback per disposition
                               │
                               ▼  tree build: pick root maximizing expected pruning,
                                  bound by target session length
                        QuestionTree {nodes, pruning_edges, order}
                               │
                               ▼  tools.question_tree.clips
                        for each clip text: key = sha256(text)+voice+speed+backend+model_fp
                        cache hit → reuse   miss → tools.tts.synthesize(text, path)
                        data/question_clips/{key}.ogg  +  index.json

3. DELIVER  (online, dumb, no LLM in the loop)
   state file: exactly ONE outstanding clip at a time
        │
        ├─► valor-telegram send --voice-note <clip> --ack-sent-id
        │     (NO --cleanup-after-send: that would delete the library)
        │        └─► Redis outbox ─► telegram_relay voice branch ─► owner's phone
        │        └─► relay publishes the sent message id to telegram:sent:{session_id}
        │              (opt-in, single-consumer, delete-on-read, 120s TTL)
        ▼
   read the ack ─► write {telegram_msg_id → qid} into the session state file
                   THIS is the registry. Nothing in the repo maps a clip's
                   message id to the question it asked (see Spike Results).
        ▼
   owner replies with a voice note
        └─► bridge/media.py downloads + transcribes (SuperWhisper → whisper-1)
                └─► store_message persists reply_to_msg_id
                    (bridge/telegram_bridge.py:1579, TelegramMessage field at
                    models/telegram.py:47)
                        │
                        ▼  bind answer → outstanding clip
                           primary: reply_to_msg_id → registry lookup → qid
                           fallback: positional (exactly one clip outstanding),
                             used when the ack was missed or the owner did not
                             use reply
                           mismatch between the two: stop, do not guess
                        ▼
                   classify against the node's pre-computed dispositions
                     on-script  → follow pruning edges → next node
                     off-script → record verbatim, stop traversal
                     pruned-out → never asked

4. WRITEBACK  (offline; drafts in parallel, applies strictly sequentially)
   answers ──► fan out ONE drafting subagent per plan doc  (read-only, parallel)
                   each returns: the exact Edit it wants, re-verified against
                   current main (the pre-drafted text was authored against an
                   older tree)
                        │
                        ▼  ONE writer, one doc at a time:
                           Edit (never Write — four PostToolUse validators match
                           Write on docs/plans with a propagating exit policy)
                           then ONE atomic shell invocation:
                           git add <explicit paths> && git commit -m "..."
                           (never git add -A — #2650's stated mitigation)
                        ▼
                   docs/plans/*.md on main, answers attributed and dated
```

**Where the intelligence lives, and why.** Every model call is in stage 2. Stage 3
holds no LLM at all: it plays a file, reads a transcript, and follows a
pre-computed edge. That is what makes the owner's "spend extra to be overly
prepared" trade cash out — the call never waits on inference, and the measured
`granite4.1:3b` in-loop latency (~1.1s median / ~1.4s p95, `config/settings.py:383`)
never enters the picture.

**The layer each failure belongs to.** A misclassified disposition is a stage-1
bug and shows up as a resolved question being asked. A bad spoken rendering is a
stage-2 bug and shows up as an unintelligible clip. A lost answer is a stage-3
bug. A clobbered plan doc is a stage-4 bug. Keeping these in separate packages with
separate CLIs is what makes a bad call diagnosable after the fact instead of
mysterious.

## Architectural Impact

**Mostly additive, with three narrow incisions into shipped code.** Three new
packages under `tools/`, one new gitignored artifact directory, and **no new
skill** (spike-4). No change to the bridge, the relay, the worker, the session
model, or any Popoto schema — which also means **no migration**, so the repo's
Popoto migration requirement does not apply to this plan.

| Surface | Impact |
|---|---|
| `tools/open_questions/` | **New package.** Harvester, disposition classifier, leverage scoring, writeback. |
| `tools/question_tree/` | **New package.** Tree construction, spoken-text authoring, clip cache. |
| `tools/voice_interview/` | **New package.** The interview driver, the clip registry, and the session state file. |
| `data/clips/` | **New**, already gitignored twice over (`data/` at `.gitignore:181`, plus a global `*.ogg` rule). Clips and their metadata sidecars. |
| `tools/ask_poll.py` | **Incision 1.** A voice branch inside the existing single degradation point at `:141-164`, plus a shared frozen question model extracted from `normalize_options` (`:52-88`), the escape-hatch literal (`:39`), and the imported `POLL_QUESTION_MAX_CHARS` (`:132-135`). Never a second CLI — `.claude/skill-context/ask-me.md:76` forbids the skill from choosing a surface. |
| `agent/session_executor.py` | **Incision 2.** `session_has_open_poll` (`:1739`) widens to `session_has_open_question`, consulting a voice registry alongside the poll registry. |
| `agent/output_router.py` | **Unchanged.** `:180-181` stays a pure function over a plain `has_open_question: bool` defaulting to `False`, which is what bounds the blast radius of incision 2. |
| `agent/session_runner/hook_edge.py` | **Unchanged.** The voice flow ends its turn the same way the poll flow does: render via `Bash`, then `AskUserQuestion` as the turn's final act. Teaching `_ASK_USER_MATCHER` (`:114`) about `Bash` is a rejected alternative per `docs/features/telegram-poll-questions.md:170-172`. |
| `bridge/answer_routing.py` | **Unchanged.** Already transport-independent on purpose (`:43-60, :94`); an inbound voice message is an ordinary steering message on this path. |
| `tools/tts/` | **Unchanged.** `synthesize(text, output_path, ...)` at `:360` already lets the caller own the destination, so the clip library is purely caller-side. |
| `tools/transcribe/` | **Unchanged.** Inbound voice notes are already transcribed into the agent's message text at `bridge/media.py:461-467`. |
| `bridge/telegram_relay.py` | **Unchanged.** The unlink is opt-in on `cleanup_file` and all three sites are gated on it; the clip sender simply never sets it. `--ack-sent-id` and `publish_sent_message_id` (`:1463-1467`) already exist and are used as-is. |
| `bridge/message_drafter.py` | **Unchanged** — see the deliberate non-refactor below. |
| `.claude/skills-global/ask-me/SKILL.md` | **Unchanged.** Already surface-agnostic; it owns ranking and altitude, not transport. |
| `.claude/skill-context/ask-me.md` | **Incision 3.** A voice row in the branch table (`:9-19`) and the degradation matrix (`:57-79`). Project-only, which is where repo-specific behavior belongs. |
| `.claude/skills-global/do-plan/` | **M4 only.** `SKILL.md:399` (Phase 4 "remove Open Questions section") and `PLAN_TEMPLATE.md:495` gain one retain-and-mark convention. |
| `docs/features/`, `docs/tools-reference.md` | New feature doc and CLI entries. |

**What ranking is and is not reused.** `/ask-me` owns which questions to ask and
at what altitude, derived from model context per invocation. The harvester replaces
exactly that with disk-based leverage scoring — reproducible between runs — which is
the whole point of M1. What gets reused is the *transport and wait* machinery below
it. Confusing the two would either duplicate the wait mechanism or throw away the
harvester.

**The deliberate non-refactor.** `bridge/message_drafter.py:100`
`_extract_open_questions` looks like the natural thing to lift and share. It has
exactly one production caller (`:1373`) and its output feeds the `needs_human` /
`pause_open_question` SDLC gate — a false negative there silently disables the
nudge pause for a session with a real outstanding question, which is the failure
shape `tests/unit/test_poll_prose_answer_closeout.py` exists to guard. Its input is
also a different thing: one well-formed section in agent stdout, not 24
heterogeneous files under seven resolution conventions.

Coupling an SDLC-gate-critical path to the much more speculative multi-convention
classifier trades a real risk for a cosmetic win, so this plan **leaves
`message_drafter` alone** and gives the harvester its own classifier. The
duplication is bounded to the heading-detection regex and is made honest by a test
asserting the two agree on the narrow single-section case. If the harvester's
classifier proves stable over a few months, unifying then is a cheap follow-up
with evidence behind it; doing it now is a guess.

**Direction of dependency.** `tools/question_tree` imports `tools/open_questions`;
`tools/voice_interview` imports both. Nothing in `bridge/` or `worker/` imports any
of the three. The single inbound edge from shipped code is incision 2, where
`agent/session_executor.py` consults the voice registry — and that edge is a bool
that defaults to `False`, so a bug there degrades to today's behavior rather than
breaking the nudge loop.

**Replication.** Other machines run this against their own project, each with its
own calling link tied to that project. Anything encoding *this* repo's plan-doc
dialect (heading synonyms, disposition markers, the `NON_LANE_PLANS` exclusion)
must be overridable per repo rather than baked into a global body. The mechanisms
already exist and this plan uses both: `.claude/skill-context/{skill}.md` for the
skill layer, and named env-overridable constants for the tool layer. Spike-4
makes this cheap — the only skill file that changes is the project-only
`.claude/skill-context/ask-me.md`, and the global `ask-me/SKILL.md` body stays
untouched, so nothing repo-specific rides the hardlink sync to other machines.

## Appetite

**Large**, split into three independently shippable milestones. Each one ends
green and useful on its own, so the appetite can be spent down and stopped at any
boundary without leaving a half-built channel standing.

| Milestone | Deliverable | Standalone value if the plan stops here |
|---|---|---|
| **M1 — Harvester** | `tools/open_questions`: enumerate, classify disposition, score unblock leverage, CLI with `--json` | The corpus becomes machine-readable. `/ask-me` and the existing poll channel can consume it immediately, which is most of the velocity win without any audio. |
| **M2 — Preparation and clip library** | `tools/question_tree`: leverage ranking, answer dispositions, pruning edges, spoken-text authoring, content-addressed OGG/Opus clip cache | A prepared, inspectable question tree with audio. Usable manually (send clips by hand) and reusable by any future transport including the deferred live call. |
| **M3 — Delivery and writeback** | A voice branch in `tools/ask_poll.py`'s single degradation point, a widened pause predicate, the interview driver, and parallel-draft / sequential-apply writeback | The full loop the issue asks for. |
| **M4 — Fix the cause forward** | Amend `/do-plan` Phase 4 to retain-and-mark the Open Questions section instead of deleting it, and name one convention in `PLAN_TEMPLATE.md` | Stops the shape set growing. Small, and the only milestone that makes every *future* harvest cheap rather than every future harvest a parsing problem. |

**Why Large and not Medium.** Three genuine gaps have to be built, not wired:
there is no clip library (the current send path actively deletes clips), no
cross-document question harvester, and no mapping from a sent clip to the question
it asked (spike-1). The classifier in M1 alone has to handle nine resolution shapes
across six distinct heading strings, one of them an H3. Medium would force dropping
either the classifier's fidelity — which produces a channel that asks resolved
questions and loses owner trust on its first call — or the writeback, which leaves
the answers in chat where they already are.

**Where the spikes bought appetite back.** M3 got materially cheaper than filed:
there is no new skill, no new wait mechanism, and no new resume path, because
`/ask-me`'s turn-ending and resume seams were deliberately built
transport-independent and can be reused as-is (spike-4). M2 got cheaper too:
`tools/tts.synthesize()` already lets the caller own the output path, so the clip
library needs no change to the synthesis API (spike-2). That headroom is what pays
for M4.

**What the appetite buys generously, by design.** All intelligence runs in M2,
before the session starts, where it has full context and no latency budget. The
owner's explicit trade: spend heavily on preparation so the delivery loop is dumb
and the call traverses the shortest path to unblocking work. Preparation cost is
therefore not a thing to optimize in this plan.

**What the appetite does not buy.** Everything in No-Gos, most importantly the
live phone call and any plan-doc locking protocol.

## Prerequisites

Nothing here blocks starting M1. Two items must be settled before M3 lands, and two
are side findings that belong in their own issues rather than in this plan's scope.

| # | Prerequisite | Blocks | Disposition |
|---|---|---|---|
| P1 | **A resolution-marking convention for the future.** The nine existing shapes are teachable (spike-3), so this is not a blocker for the harvester — but M4 has to name one convention, and which marker it names is Open Question 2. | M4 only | Decide during M4. M1 ships against the existing shapes regardless. |
| P2 | **`validate_poll_question` has two enforcement points for one invariant** — logged-and-discarded at `agent/output_handler.py:1560`, hard-exit at `tools/ask_poll.py:134`. It is an open owner decision on #3095. A voice renderer added to the same seam inherits it unresolved, which means an invalid question either hard-exits the interview or ships degraded, and nobody has ruled which. | M3 | Raised as an Open Question below. Not this plan's to settle unilaterally. |
| P3 | **The single-writer lease does not exist.** Confirmed: `models/plan_doc_lease.py` and `.claude/hooks/validators/validate_plan_doc_lease.py` are absent, and #2650 closed `NOT_PLANNED` into the still-open #3065. Its plan reads as completed only because plan migration never checks `stateReason` (filed as **#3332**, with 17 sibling mislabels). | Nothing | **This plan does not depend on it, but it is now the sole protection.** Sequential single-writer writeback sidesteps the hazard with no lock. Task 15's four rules are load-bearing rather than redundant. |
| P4 | **`bf_alice` is named in `.claude/skill-context/do-debrief.md:21` but absent from `KOKORO_VOICES`** (`tools/tts/__init__.py:27-38`), so that documented invocation hits the unknown-voice path. Re-verified at the baseline commit. | Nothing | File separately. Relevant only as a warning: this plan pins its clip voice to a name verified present in the catalog, and records the *actual* voice from the result dict rather than trusting the request. |

**Not prerequisites, explicitly.** No new external dependency, no vendor key beyond
`OPENAI_API_KEY` (already declared), no PBX, no streaming STT, no Popoto migration,
no plan-doc lock.

## Solution

### Key Elements

1. **`tools/open_questions` — the harvester.** Walks `docs/plans/*.md` (minus
   `NON_LANE_PLANS`) and this repo's open issues, emits typed `OpenQuestion`
   records, classifies each one's disposition against the nine shapes the corpus
   actually uses, and scores unblock leverage. Standalone CLI with `--json`.
2. **`tools/question_tree` — the preparation pass.** Ranks by leverage, enumerates
   plausible answer dispositions per question, computes which questions each
   disposition moots, picks the root that maximizes expected pruning, and bounds the
   tree by a target session length. Authors the spoken text for every node and
   synthesizes it into a content-addressed clip library.
3. **`tools/voice_interview` — the delivery driver.** Holds exactly one outstanding
   clip, owns the `{telegram_msg_id → qid}` registry that spike-1 showed does not
   exist anywhere else, binds each inbound answer, and walks the pre-computed
   pruning edges. No model call in this loop.
4. **A voice branch in `tools/ask_poll.py`.** One more arm on the existing single
   degradation point, fed by a shared frozen question model extracted from the
   pieces currently scattered across `ask_poll.py` and `output_handler.py`.
5. **`tools/open_questions.writeback` — parallel draft, sequential apply.**
   One drafting subagent per plan doc, read-only and concurrent; one writer,
   `Edit`-only, one doc at a time, one atomic stage-and-commit per doc.
6. **The M4 convention amendment.** `/do-plan` stops deleting the Open Questions
   section on finalize and marks it instead.

### Flow

The end-to-end path is drawn in Data Flow above. The load-bearing sequencing
decisions, restated:

- **All intelligence runs before the call.** Every model call is in element 2. The
  delivery loop plays a file, reads a transcript, and follows an edge.
- **One clip outstanding at a time.** This is what makes binding tractable and what
  lets the whole interview reuse the poll flow's one-question-per-turn shape: each
  clip is one render, one turn-end, one wait, one answer, one resume. The traversal
  is N of those in sequence, not a new interaction model.
- **Pruning happens between turns, from pre-computed edges.** Nothing is inferred
  live.
- **Writeback is a separate invocation after the call ends**, never interleaved with
  delivery. An interview that dies mid-traversal loses no answers: they are in the
  Telegram history and in the state file.

### Technical Approach

**Records and identity.** `OpenQuestion` is a frozen dataclass — matching the
`PollEligibility` style at `bridge/poll_gating.py:36-37` — carrying `qid`, `text`,
`source_path`, `source_line`, `source_kind` (`plan` | `issue`), `tracking_issue`,
`disposition` (`open` | `resolved` | `defaulted` | `deferred`), and the leverage
inputs. `qid` is `sha256` of the *normalized* question text truncated to 12 hex
chars: stable across line-number drift, which matters because plan docs are edited
constantly and a qid keyed on position would churn every commit.

**Disposition classification.** A table of named, env-overridable rules rather than
one regex — this is the part that has to be right, and the part that must be
overridable per repo. Ordered most-specific-first:

| Shape | Signal |
|---|---|
| Section-level empty | body is `None.` / `**None.**` / `None blocking.` / `None outstanding.` |
| Heading renamed | `## Resolved Questions`, `## Resolved Decisions`, `## Decisions Recorded`, `## Decisions (Owner, …)` |
| Prose all-clear | "All open questions resolved", "nothing remains open", "Nothing is open" |
| Blockquote banner | `> **RESOLVED <date> (owner).**` preceding retained original text |
| Strikethrough | `~~**…**~~` followed by `**Resolved …**` |
| Per-item inline marker | `Resolved:`, `— DECIDED`, `(resolved <date>, …)`, `RATIFIED`, `**D1 —**` |
| **Defaulted (not an ask)** | section preamble contains "Silence keeps the default", or the item carries `Default:`, or the item pairs `**Disposition:**` with `Overturned by:` |
| Genuinely open | none of the above |

Spike-3 verified every one of these against the corpus, including that the
defaulted items in `lane-3:787` and `lane-5:1882` are greppable without any edit.
The H3 heading at `durability-room-job-agentrun.md:512` is why heading discovery
scans `^#{2,4}\s` rather than `^## `.

**The deliberate non-refactor, restated.** `bridge/message_drafter.py:100` stays as
it is. See Architectural Impact for the reasoning; the practical contract is a test
asserting the harvester's classifier and `_extract_open_questions` agree on the
narrow single-well-formed-section case, so the duplication cannot drift silently.

**Leverage scoring.** `leverage = w_plans · (plan docs blocked) + w_issues · (open
issues blocked) + w_prune · (questions this answer would moot)`. The weights are
named env-overridable constants with an explicit grain-of-salt comment — they are
guesses until a few real calls produce evidence, and pretending otherwise in code
would be worse than saying so.

**Authoring and tree construction.** `run_typed()` (PydanticAI), not `claude -p`:
this is a non-harness call, and the repo's pattern for structured classification is
the Literal-typed Pydantic output shape at `tools/classifier.py:305-440`. Per
question the model returns spoken text, a deeper-context clip text, a readback line,
the plausible dispositions, the qids each disposition moots, and a draft writeback
per disposition. Prompts carry no specific numbers or identifiers — partly because
prompts with baked-in numerals invite hallucination, and partly because the
`/do-voice-recording` prosody rules forbid reciting multi-digit identifiers aloud,
so clips reference work by name. A validator rejects any spoken text containing a
multi-digit run or exceeding `MAX_TEXT_LENGTH` (4096, `tools/tts/__init__.py:24`)
before synthesis is attempted.

**Clip cache.** Key: `sha256("v1|{text}|{requested_voice}|{force_cloud}|opus")` →
`data/clips/<hash>.ogg`, with `<hash>.json` recording the **actual** `backend`,
**actual** `voice`, and `duration` from the result dict. Validation happens on read,
not in the key: a hit whose sidecar backend disagrees with what the caller now wants
is a miss. Backend cannot go in the key because `_is_kokoro_available()`
(`tools/tts/__init__.py:397`) is a stateful time-windowed live probe and the
Kokoro→cloud fallback re-resolves the voice mid-call (`:426-437`). The `v1` salt
covers the constants a caller cannot observe (`speed=1.0`, `-b:a 24k`,
`kokoro-v1.0.onnx`, `tts-1`) and is bumped when any of them change. Every call site
must handle the `{"error": ...}`-only return shape (`:389-411`), which carries none
of the other keys.

**Sending.** `valor-telegram send --voice-note <clip> --ack-sent-id`, and
**never** `--cleanup-after-send` — that sets `payload["cleanup_file"]`, which all
three relay unlink sites are gated on, and would destroy the library. The
already-shipped negative test at
`tests/unit/test_telegram_relay_voice_note.py:117-132` pins the survival behavior,
so the plan adds no new test for it. `--ack-sent-id` is single-consumer,
delete-on-read, with a 120s TTL (`bridge/outbox_ack.py:48`), so the ack is read
immediately after send and recorded in the registry; a missed ack degrades to
positional binding rather than failing.

**Binding.** Primary: inbound `reply_to_msg_id` (persisted at
`bridge/telegram_bridge.py:1579`) looked up in the registry. Fallback: positional,
since exactly one clip is outstanding. If both are available and disagree, the
traversal stops and reports — a wrong binding writes an answer into the wrong plan
doc, which is strictly worse than stopping.

**Waiting and resuming.** Unchanged mechanisms. Render via `Bash`, then
`AskUserQuestion` as the turn's final act to fire the `needs_human` edge
(`agent/session_runner/hook_edge.py:363-368`); stay stopped via the widened
`session_has_open_question` feeding `agent/output_router.py:180`'s existing pure
bool; resume through `bridge/answer_routing.py`, which is already
transport-independent.

**Writeback.** Drafting fans out one read-only subagent per plan doc, in parallel,
each returning the exact `Edit` it wants plus a re-verification against current
`main` (the drafts were authored against an older tree, so a drifted question is
reported, not overwritten). Applying is one writer, one doc at a time:

- `Edit`, never `Write` — four PostToolUse validators match `Write` on
  `docs/plans` with `exit_policy = "propagate"` (`.claude/hooks/manifest.toml:133-167`).
  This was confirmed live during this plan's own authoring.
- Staging and committing are **one** shell invocation with explicit paths, never
  `git add -A`. This is #2650's stated mitigation and the direct lesson of the
  2026-08-13 incident.
- A plan-only commit carries **no** closing keyword: `scripts/check_issue_disposition.py`
  exempts `docs/plans/` (`:86`, applied at `:183`) but blocks
  `Closes|Fixes|Resolves #N` on plan-only commits (#2890). Anything reaching outside
  that prefix needs `Closes #N`, `Refs #N`, or `No-issue: <reason>`.
- `--dry-run` prints every `Edit` and the exact `git` invocation before anything is
  written. Given what #2650 catalogues, an unreviewable writeback is not acceptable.

## Failure Path Test Strategy

The failure modes that matter here are not crashes. They are a channel that asks a
question already answered, a clip that says nothing useful, an answer bound to the
wrong plan doc, and a writeback that clobbers a lane's work. Each gets an explicit
test.

### Exception Handling Coverage

- `tools/tts.synthesize()` returning **`{"error": ...}` alone** — no `path`, no
  `backend`, no `duration` (`tools/tts/__init__.py:389-411`). Every call site is
  tested against this shape; a preparation pass that hits it records the node as
  unsynthesized and continues rather than dying with a `KeyError` on `path`.
- `tools/transcribe.transcribe()` returning `{"error": ...}` instead of raising. An
  untranscribable answer leaves the question **open** and records that the audio
  arrived but could not be read. It must never be silently dropped and never
  guessed at.
- `await_sent_message_id` timing out or returning nothing (single-consumer,
  delete-on-read, 120s TTL). Degrades to positional binding; tested.
- A plan doc that has moved or been deleted between preparation and writeback.
  Reported as drift, not created.
- Malformed frontmatter: `tracking: null`, `last_comment_id: none`, a bare
  `last_comment_id:`, and the two plan docs with no frontmatter at all. The
  harvester parses all of them without raising.

### Empty/Invalid Input Handling

- Empty corpus: no plan docs, or every question resolved. `list` prints nothing and
  exits 0; `prepare` refuses to build a tree rather than emitting an empty one; the
  interview reports "nothing to ask" instead of opening a session.
- A question whose spoken text exceeds 4096 chars, or contains a multi-digit run.
  Rejected **before** synthesis with the offending text named.
- A section with a heading but no list items, and a section containing only
  template placeholder text (`[Question about scope/approach]` from
  `PLAN_TEMPLATE.md:495`). Both yield zero questions.
- Zero options / a single option on the shared question model: whatever
  `normalize_options` (`tools/ask_poll.py:52-88`) enforces today, the voice branch
  enforces identically by construction, since it consumes the same model.
- An answer transcript that is empty, or is pure filler. Question stays open.

### Error State Rendering

- **Off-script answer**: traversal stops, the transcript is recorded verbatim, and
  no disposition is fabricated. Tested as a first-class path, not an edge case —
  this is the one the owner will actually hit.
- **Partial answer**: the question stays open and the partial steer is recorded
  alongside it. An answer that does not resolve must not read as resolved.
- **Binding disagreement** between `reply_to_msg_id` and the outstanding clip:
  stop and report, naming both candidates. Never pick one.
- **Drifted question at writeback time**: reported with the old and current text
  side by side, and skipped. The answer is preserved for a human to place.
- **A disposition the tree did not anticipate**: treated as off-script, not coerced
  into the nearest pre-computed edge.

## Test Impact

Mostly new files. Two existing suites are regression guards that must pass
**unchanged**, and two existing suites cover behavior this plan deliberately does
not re-test because it is already pinned.

**Must pass unchanged (regression guards):**

- [ ] `tests/unit/test_open_question_gate.py` — the contract on
      `_extract_open_questions`. This plan leaves `bridge/message_drafter.py`
      alone precisely so this suite does not have to move; if it needs an edit,
      the non-refactor decision was violated.
- [ ] `tests/unit/test_output_router.py` — pins `pause_open_question` at
      `:232` and `:333`. Widening `session_has_open_poll` →
      `session_has_open_question` must not touch `output_router`'s pure-function
      contract, and this suite is how that is proven.
- [ ] `tests/unit/test_poll_prose_answer_closeout.py` — the failure shape the
      pause branch exists to prevent. Green after incision 2 or the incision is
      wrong.

**Already pinned, no new test needed:**

- `tests/unit/test_telegram_relay_voice_note.py:117-132` already asserts a clip
  survives when `cleanup_file` is not set. Cite it; do not duplicate it.

**New:**

- [ ] `tests/unit/test_open_questions_extract.py` — corpus-fixture tests for every
      resolution shape in the classification table, including the defaulted items
      from `lane-3` and `lane-5`, the H3 heading, and all four frontmatter
      irregularities. Also asserts agreement with `_extract_open_questions` on the
      narrow single-well-formed-section case, so the deliberate duplication cannot
      drift silently.
- [ ] `tests/unit/test_open_questions_score.py` — leverage scoring and
      root-selection-by-expected-pruning on synthetic question sets.
- [ ] `tests/unit/test_question_clip_cache.py` — key coverage, reuse on unchanged
      input, miss on sidecar-backend disagreement, `v1` salt invalidation, and the
      `{"error": ...}`-only return shape.
- [ ] `tests/unit/test_question_clip_text_guard.py` — rejection of multi-digit runs
      and over-4096-char spoken text before synthesis is attempted.
- [ ] `tests/unit/test_voice_interview_binding.py` — registry lookup via
      `reply_to_msg_id`, positional fallback on a missed ack, and the stop-on-
      disagreement path.
- [ ] `tests/unit/test_voice_interview_traversal.py` — pruning-edge traversal,
      early session end when remaining nodes are pruned, off-script stop, partial
      answer leaves the question open.
- [ ] `tests/unit/test_ask_poll_voice_branch.py` — the third arm of the degradation
      point, and that the shared question model enforces the same option rules and
      caps the poll branch enforces.
- [ ] `tests/unit/test_open_questions_writeback.py` — sequential apply, `Edit`-only
      on plan docs, drift detection, and the exact commit-message shape including
      the no-closing-keyword rule for plan-only commits.

Run scope: name each file explicitly, e.g.
`scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py`. Never a full
`tests/unit/` sweep for this work — it takes about 20 minutes and leaks xdist
orphans that other agents on this machine pay for.

## Rabbit Holes

- **Chasing 100% disposition-classification accuracy.** The corpus has nine shapes
  and will grow new ones until M4 lands. Aim for zero false positives on *defaulted
  and resolved* items (asking a settled question is what costs owner trust) and
  accept false negatives (a genuinely open question missed this week gets asked next
  week). Do not build a general markdown-semantics engine.
- **Making the leverage score "correct."** The weights are guesses. Resist tuning
  them before a real call has produced evidence about which ordering actually
  unblocks work. Ship named constants with a grain-of-salt comment and move on.
- **Perfecting the pruning DAG.** Expected-pruning root selection is a heuristic over
  a model's guess about which answers moot which questions. A tree that prunes
  nothing is still a correctly ordered list — which is the worst case, not a failure.
- **Byte-level clip determinism.** Tempting after the Kokoro research, and
  irrelevant: the cache is a reuse cache, not a verification cache. Key coverage
  plus read-time sidecar validation is the whole requirement.
- **Unifying the two question extractors.** Explicitly deferred. See Architectural
  Impact. Revisit with months of evidence, not now.
- **Fixing #3095's poll backlog while in the neighborhood.** Only P2 (the two
  enforcement points) touches this work. The FloodWait items, the reconcile coverage
  gap, and the orphan-adoption RPC burn are someone else's lane.
- **Building the live call because the pipeline now exists.** It is a No-Go for
  reasons that are unchanged by anything this plan ships: licensing, a PBX, and no
  streaming STT.
- **Retro-normalizing the corpus once M4 defines the convention.** M4 fixes the
  cause forward. The 15 settled docs stay as they are.

## Risks

### Risk 1: The first call asks a question the owner already answered

The single failure that kills adoption. If the classifier reads a
`> **RESOLVED 2026-07-27 (owner).**` banner as an open question, the owner spends
their fifteen minutes re-deciding settled things and does not take a second call.

**Mitigation:** the classification table is verified against the real corpus with
the audited counts as a fixture assertion, ordered most-specific-first so resolution
markers win over the mere presence of a question heading. Before the first real call,
`list --json` output is reviewed by eye against the corpus — cheap, once.

### Risk 2: An answer is written into the wrong plan doc

A mis-bound answer is worse than a lost one: it silently corrupts a decision record
and there is no signal that it happened.

**Mitigation:** two independent binding sources (registry lookup via
`reply_to_msg_id`, positional fallback), and a hard stop when they disagree rather
than a tie-break. Plus `--dry-run` on writeback showing every `Edit` before
anything is written.

### Risk 3: Writeback damages a concurrent lane's work

#2650's 2026-08-13 incident was unrecoverable: a plan-doc agent's `git add -A`
swallowed another lane's staged code onto `main` under an unrelated issue reference.
This plan writes to `main` on the shared checkout, so it is exposed to exactly that.

**Mitigation:** sequential single-writer, `Edit` never `Write`, explicit paths never
`-A`, one atomic stage-and-commit per doc. No lock is built — that is a No-Go — so
the mitigation is entirely in discipline enforced by the writeback tool rather than
by convention, which is why it is a tool and not an instruction to an agent.

### Risk 4: Incision 2 breaks the nudge loop for poll sessions

Widening `session_has_open_poll` → `session_has_open_question` sits on the path that
decides whether a session gets nudged. A bug here does not break the voice channel;
it breaks polls and ordinary sessions.

**Mitigation:** `agent/output_router.py:180`'s pure-function contract and its
`has_open_question: bool = False` default are untouched, so a failure in the new
registry read degrades to today's behavior. Three existing suites
(`test_output_router.py`, `test_poll_prose_answer_closeout.py`, plus the drafter
gate) are the proof and must pass unchanged.

### Risk 5: The prepared tree is stale by the time the call happens

Preparation is expensive and generous by design, which means it happens ahead of
time — and plan docs change constantly on this repo.

**Mitigation:** qids are content-hashed, not position-keyed, so ordinary edits
elsewhere in a doc do not churn them. Every pre-drafted writeback is re-verified
against current `main` before being applied, and a question whose text changed since
preparation is reported as drift rather than answered. Re-preparation re-cuts only
the clips whose keys changed.

### Risk 6: The interview inherits an unresolved invariant from the poll path

P2: `validate_poll_question` is enforced two different ways. A voice renderer on the
same seam either hard-exits mid-interview or ships a degraded question, and nobody
has ruled which.

**Mitigation:** raised as an Open Question rather than absorbed. If it is not settled
before M3, the voice branch fails closed (hard-exit, matching
`tools/ask_poll.py:134`, the stricter of the two) and says so in its output.

## Race Conditions

### Race 1: Two writeback appliers on the shared `main` checkout

**Hazard:** the whole of #2650. Two agents staging and committing plan-doc edits
concurrently can interleave, and one `git add -A` can swallow the other's staged work.

**Prevention:** writeback has exactly one applier by construction — drafting is the
only parallel phase and it is read-only. Each doc's stage-and-commit is a single
shell invocation with explicit paths. This plan does **not** guard against a
*different* lane committing concurrently; no lock exists and building one is a
No-Go. What it guarantees is that this tool is never itself the second writer.

### Race 2: The sent-id ack is consumed or expires before it is read

**Hazard:** `telegram:sent:{session_id}` is single-consumer, delete-on-read, 120s TTL
(`bridge/outbox_ack.py:48`). If anything else reads it first, or the read is slow, the
clip's message id is gone and the registry entry is never written.

**Prevention:** the ack is read immediately after the send returns, in the same
function, before anything else happens. A miss is not an error — it degrades to
positional binding, which is correct as long as exactly one clip is outstanding.

### Race 3: An answer arrives while the next clip is being sent

**Hazard:** the owner answers fast, or answers a clip two questions back. Two clips
transiently outstanding would break the positional fallback.

**Prevention:** the driver sends the next clip only after the current answer is bound
and recorded, and the state file holds at most one outstanding clip as an invariant
rather than a convention. A second inbound voice note while nothing is outstanding is
recorded as unsolicited and does not advance the traversal.

### Race 4: A plan doc changes between preparation and writeback

**Hazard:** the pre-drafted writeback was authored against an older tree. Applying it
blind would overwrite an edit made in between.

**Prevention:** re-verification against current `main` before every apply, comparing
the question's current text against the text the draft was authored from. A mismatch
is reported as drift and skipped.

### Race 5: Two preparation passes writing the same clip file

**Hazard:** content-addressed paths mean two concurrent preparation passes can target
the identical `data/clips/<hash>.ogg`, producing a torn file.

**Prevention:** synthesize to a temp path in the same directory and `os.replace()`
into place — atomic on the same filesystem. Identical content makes the write
idempotent, so the loser of the race is harmless. The sidecar JSON is written the same
way, after the audio.

## No-Gos (Out of Scope)

- **A live phone call, and any Tel-Agent dependency.** Justification:
  [Tel-Agent](https://github.com/Dpro-at/Tel-Agent) is AGPL-3.0 with network
  copyleft, speaks only SIP (needs a PBX on the LAN), and documents no
  pre-recorded clip playback. Independently this repo has no streaming STT —
  `tools/transcribe` is batch and file-based over a macOS GUI app. A duplex leg
  is greenfield across licensing, infrastructure, and transport at once, so it
  belongs in its own issue taken only after the Telegram leg proves the pipeline.
- **A live intent classifier in the delivery loop.** Justification: only duplex
  traversal needs it, and the nearest measured precedent (`run_typed_local` on
  `granite4.1:3b`, ~1.1s median / ~1.4s p95 per `config/settings.py:383`) is
  marginal inside a call's turn-taking. Deferred with the call itself.
- **Cross-project harvesting.** Justification: owner ruling. Client projects are
  deliberately separate and each is owned by exactly one machine, so a cross-repo
  harvest would violate project isolation and put one project's calling link in
  front of another project's questions. Each machine harvests its own repo.
- **A plan-doc lock or single-writer lease.** Justification: sequential
  single-writer writeback sidesteps the hazard without new infrastructure, and
  #3330's downstream constraints state that concluding a lock is required is a
  scope change to raise, not absorb.
- **Retro-editing the existing plan-doc corpus to one resolution convention.**
  Justification: spike-3 measured it at ~50 item edits across 15 of 24 docs, 6 of
  them needing heading renames, on the shared `main` checkout — a known lane
  collision surface — bought for **zero current mechanical consumer**, since no hook
  or validator parses the section today. Four of the nine shapes are already
  greppable, including the defaulted items this plan most needed to exclude. M4
  fixes the cause forward instead of rewriting history.
- **A second question-rendering CLI alongside `valor-ask-poll`.** Justification:
  `.claude/skill-context/ask-me.md:76` forbids the skill from detecting the surface
  and branching by hand, and `tools/ask_poll.py:141-164` is deliberately the single
  decision point. A second CLI would put surface detection back into the skill body.
- **Teaching the `AskUserQuestion` PreToolUse matcher about `Bash`.**
  Justification: already considered and rejected in
  `docs/features/telegram-poll-questions.md:170-172`. The voice flow uses the same
  render-then-`AskUserQuestion` sequence the poll flow uses.
- **Persisting the outbound voice note's question text in `store_message`.**
  Justification: spike-1's alternative fix. It changes a hot relay path
  (`bridge/telegram_relay.py:1499-1515`) for a benefit exactly one caller needs,
  when `--ack-sent-id` already exposes the message id and a session-owned registry
  closes the gap with no shipped-code change.
- **Telegram `file_id` caching to avoid re-uploading clips.** Justification: a real
  optimization — re-sending re-uploads the bytes — but the clip library's purpose is
  to save *synthesis* cost, and `file_id` handling differs between voice and audio
  sends in ways that would need their own verification. Out of scope, noted for
  later.
- **Timestamped transcript alignment.** Justification: unnecessary on the
  Telegram transport, where one inbound voice note answers one outbound clip, and
  it would force the cloud STT path since the local SuperWhisper backend returns
  no timestamps.

## Update System

**Minimal, but not zero.** The feature is per-project and per-machine by design, and
"each machine harvests its own repo, with its own calling link tied to that project"
is a deployment property, not just a code property — so the update path matters more
here than the small diff suggests.

- **`scripts/remote-update.sh`: no change.** No new dependency, no new service, no new
  launchd plist, no new secret. `OPENAI_API_KEY` is already declared and is only the
  fallback path for both TTS and STT.
- **Hardlink sync: nothing new to register.** Spike-4 removed the new skill from this
  plan, so `scripts/update/hardlinks.py` needs no entry and no `RENAMED_REMOVALS`
  row. The only skill file that changes is the project-only
  `.claude/skill-context/ask-me.md`, which is never synced.
- **M4 does ride the sync.** `.claude/skills-global/do-plan/SKILL.md` and
  `PLAN_TEMPLATE.md` are hardlinked to `~/.claude/skills/`, so the convention
  amendment propagates fleet-wide on the next `/update`. That is the intent — the
  convention is only worth having if every machine's `/do-plan` follows it — but it
  means M4 changes planning behavior on machines that will never run this harvester.
  Land it as its own commit so it is independently revertible.
- **No Popoto migration.** No model changes, so `scripts/update/migrations.py` is
  untouched. Interview state is a JSON file under `data/`, deliberately: the answers
  themselves are durable in Telegram history, so state loss costs a re-read rather
  than the answers, and that is not worth a schema migration.
- **`data/clips/` is created on demand** by the preparation pass and needs no
  provisioning step. It is gitignored twice over and grows slowly (one small OGG per
  distinct question wording); no reaper is built, and if the directory ever needs
  pruning that is a follow-up with evidence behind it.
- **Existing installations need nothing.** First run of the harvester on a machine
  creates what it needs.

## Agent Integration

**Who invokes what.**

- **The harvester and preparation pass are operator-invoked CLIs**, not autonomous.
  An Eng session (or the owner directly) runs `list` to see the queue and `prepare`
  before a call. Nothing schedules them; nothing runs them on a hook.
- **The interview runs inside an existing session** and uses the existing wait
  machinery: render via `Bash`, then `AskUserQuestion` as the turn's final act to fire
  the `needs_human` edge. It invents no session type, no new persona, and no new
  permission surface.
- **Writeback drafting is the one place subagents fan out.** One read-only drafting
  agent per plan doc, spawned with the plan doc's path and the bound answer as
  explicit context — context gets passed explicitly, never assumed inherited. Each
  returns the exact `Edit` it proposes. They write nothing.
- **Writeback applying is not delegated.** One writer, in the invoking session, one
  doc at a time. Handing the apply phase to a subagent is how #2650 happened.

**Persona and voice.** Clips are spoken output to the owner, so they are subject to
the same rule as every other outbound channel: no raw errors, no internal narration,
no issue numbers read aloud. A synthesis failure or a binding stop is reported to the
operator in the session, not narrated into the owner's ear mid-call.

**Session type.** `eng` (full permissions; writeback commits to `main`). A `teammate`
session cannot run writeback — its writes are restricted to docs/meta paths and,
regardless, it should not be committing plan docs. The harvester's `list` is safe
from any session since it is read-only.

**Task-list and lane identity.** This plan's build work is ordinary lane work:
durable slug-scoped task list, branch `session/{slug}`, worktree
`.worktrees/{slug}/`. The plan doc itself commits directly on `main`, as all plans and
markdown docs do.

## Documentation

- [ ] Create `docs/features/voice-interview-open-questions.md` describing the
      harvester, the question tree, the clip library, the delivery loop, and the
      sequential writeback contract.
- [ ] Add a row to `docs/features/README.md` index table.
- [ ] Add the `tools.open_questions` CLI to `docs/tools-reference.md`.
- [ ] Update `.claude/skill-context/ask-me.md` if the voice mode shares that
      skill's transport (depends on Open Question 3).

## Success Criteria

Carried from #3330's acceptance criteria, with the audited figures pinned as
assertions and the spike findings folded in.

**M1 — Harvester**

- [ ] `python -m tools.open_questions list` prints every genuinely open question
      across `docs/plans/*.md` and this repo's open issues, each with a stable
      content-hash `qid`, source path and line, and the owning plan's `tracking:`
      issue.
- [ ] Classification matches the audited corpus: **15 genuinely unresolved** (13
      under the documented strict reading), **11 defaulted**, **46 resolved**. All
      nine shapes handled, including the H3 heading at
      `durability-room-job-agentrun.md:512` and the six distinct heading strings.
- [ ] Zero false positives on defaulted and resolved items. Specifically, the
      `lane-3` and `lane-5` defaulted items are excluded, which the naive extractor
      would have emitted as nine false positives.
- [ ] `NON_LANE_PLANS` excluded; no crash on `tracking: null`,
      `last_comment_id: none`, a bare `last_comment_id:`, or the two plan docs with
      no frontmatter.
- [ ] Issues are treated as a best-effort secondary source, not a peer of plan docs
      (16 of ~126 carry a question section across 14 distinct heading strings).
- [ ] Every question carries an unblock-leverage score, and the weights are named
      env-overridable constants.
- [ ] **Isolation:** no code path reads another project's checkout, another repo's
      issues, or a remote repo's contents API. Provable by inspection — there is no
      such call to find.
- [ ] `tests/unit/test_open_question_gate.py` passes unchanged.

**M2 — Preparation and clip library**

- [ ] A preparation pass emits a tree with ordered nodes, pruning edges, and a
      leverage score per node, bounded by a target session length.
- [ ] Every node has an OGG/Opus clip at `data/clips/<hash>.ogg` with a sidecar
      recording the **actual** backend and voice from the result dict.
- [ ] Re-preparation with unchanged questions synthesizes nothing; changing one
      question re-cuts only that clip; bumping the `v1` salt re-cuts everything.
- [ ] A sidecar whose recorded backend disagrees with the requested backend is
      treated as a miss.
- [ ] No spoken text contains a multi-digit run; no clip text exceeds 4096
      characters. Both rejected before synthesis, not after.
- [ ] Every `synthesize()` call site handles the `{"error": ...}`-only return shape.

**M3 — Delivery and writeback**

- [ ] Clips are delivered as Telegram voice notes one at a time, sent with
      `--ack-sent-id` and **without** `--cleanup-after-send`; the clip file still
      exists after delivery.
- [ ] An inbound voice-note reply binds to the clip it answers via the
      `{msg_id → qid}` registry, with positional fallback on a missed ack, and **no
      timestamp alignment anywhere**.
- [ ] A disagreement between the two binding sources stops the traversal and names
      both candidates.
- [ ] Answering follows the pre-computed pruning edges; a session whose remaining
      nodes are all pruned ends without asking them.
- [ ] An off-script answer stops traversal, is recorded verbatim, and produces no
      fabricated resolution. A partial answer leaves the question open with the
      partial steer recorded.
- [ ] The voice branch lives inside `tools/ask_poll.py`'s single degradation point;
      no second CLI exists and `.claude/skills-global/ask-me/SKILL.md` is unchanged.
- [ ] `session_has_open_question` gates the pause branch; `test_output_router.py`
      and `test_poll_prose_answer_closeout.py` pass unchanged.
- [ ] Writeback drafts in parallel and applies sequentially: one writer, `Edit` only
      on plan docs, one atomic stage-and-commit per doc with explicit paths, never
      `git add -A`.
- [ ] Plan-only writeback commits carry no closing keyword and pass
      `scripts/check_issue_disposition.py`.
- [ ] Every pre-drafted writeback is re-verified against current `main`; a drifted
      question is reported, not overwritten.
- [ ] `--dry-run` prints every `Edit` and the exact `git` invocation before anything
      is written.
- [ ] Re-running the harvester after a session shows the answered questions resolved
      and does not re-ask them. **This is the end-to-end proof.**

**M4 — Convention forward**

- [ ] `/do-plan` Phase 4 marks the Open Questions section instead of removing it
      (`SKILL.md:399`), and `PLAN_TEMPLATE.md` names one resolution convention
      including how a defaulted item is marked.
- [ ] Landed as its own revertible commit, since it changes planning behavior on
      every machine.
- [ ] No existing plan doc is retro-edited.

## Team Orchestration

Three of the four milestones are single-builder work. Only writeback drafting fans
out, and only in the read-only direction.

### Team Members

| Role | Agent type | Scope | Concurrency |
|---|---|---|---|
| Builder | `builder` | One milestone at a time, one task at a time. M1 → M2 → M3, with M4 landable at any point after M1. | 1 |
| Test engineer | `test-engineer` | The new suites in Test Impact. Real fixtures from the actual corpus, no mocks on the extraction path. | 1, after each milestone |
| Plan critic | `plan-reviewer` | This document, before build starts. | 1 |
| Reviewer | `code-reviewer` | Each milestone's diff. Incision 2 gets particular attention — it is the only change that can break shipped behavior. | 1 per milestone |
| Writeback drafters | `general-purpose`, read-only | One per plan doc at writeback time, **runtime** fan-out inside the feature itself, not build-time orchestration. Each gets the plan doc path and the bound answer as explicit context. | N, parallel, read-only |

### Sequencing and why

- **M1 before everything.** M2's tree is meaningless without reliable dispositions,
  and M1 is the only milestone with standalone value if the appetite runs out.
- **M2 before M3.** The delivery loop plays clips; there is nothing to play until the
  library exists.
- **M4 is independent after M1.** It needs the classification table (to know what
  shapes exist) but nothing else, and it ships fleet-wide, so it lands alone.
- **Do not parallelize M1 and M2 across two builders.** The `OpenQuestion` record
  shape is the interface between them and it will move while M1 is being written.
  Two builders would spend more on reconciling the record than either saves.
- **The writeback fan-out is a feature behavior, not a build strategy.** It is listed
  here because it spawns agents at runtime and that has to be designed, not because
  the build is orchestrated that way.

## Step by Step Tasks

### M1 — Harvester

#### 1. Records and plan-doc discovery

- [ ] `tools/open_questions/models.py`: frozen `OpenQuestion` dataclass with `qid`,
      `text`, `source_path`, `source_line`, `source_kind`, `tracking_issue`,
      `disposition`, and leverage inputs. Style after
      `bridge/poll_gating.py:36-37`.
- [ ] `qid` = first 12 hex of `sha256` over the **normalized** question text
      (whitespace collapsed, markdown emphasis stripped, trailing punctuation
      dropped). Not position-keyed — plan docs churn constantly.
- [ ] Discovery walks `docs/plans/*.md` excluding `tools/plan_doc_scope.NON_LANE_PLANS`
      (import it; do not re-list the names).
- [ ] Frontmatter parsing tolerates `tracking: null`, `last_comment_id: none`, a bare
      `last_comment_id:`, and absent frontmatter.

#### 2. Heading discovery and item parsing

- [ ] Scan `^#{2,4}\s` — not `^## ` — so the H3 at
      `durability-room-job-agentrun.md:512` is found.
- [ ] Heading synonym table as a named env-overridable constant: `Open Questions`,
      `Resolved Questions`, `Resolved Decisions`, `Decisions Recorded`,
      `Decisions (Owner, …)`, `Schema Gate — Open Decisions from Spikes`. Overridable
      because other repos have their own dialect.
- [ ] Item parsing handles numbered, bulleted, and bold-lead-in items, and drops
      template placeholder text.

#### 3. Disposition classification

- [ ] Implement the classification table from Technical Approach as ordered,
      individually named rules, most-specific-first, so a resolution marker beats the
      mere presence of a question heading.
- [ ] Defaulted detection: section preamble "Silence keeps the default"; item-level
      `Default:`; item-level `**Disposition:**` paired with `Overturned by:`.
- [ ] Section-level short-circuits: `None.` / `**None.**` / `None blocking.` /
      `None outstanding.` and the prose all-clear phrasings.
- [ ] Blockquote `> **RESOLVED …**` banner and strikethrough-plus-`**Resolved**`.

#### 4. Validate classification against the real corpus

- [ ] `tests/unit/test_open_questions_extract.py` with fixtures lifted from the actual
      docs. Assert the audited counts: 15 open, 11 defaulted, 46 resolved.
- [ ] Assert agreement with `bridge/message_drafter._extract_open_questions` on the
      narrow single-well-formed-section case. **This is the guard on the deliberate
      duplication.**
- [ ] `scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py` green, and
      `tests/unit/test_open_question_gate.py` green **unchanged**.

#### 5. Issues as a secondary source

- [ ] Harvest question-shaped sections from this repo's open issues via `gh`, tolerant
      of the 14 heading variants, and mark them `source_kind="issue"`.
- [ ] Never reach another repo. No `--repo`, no `GH_REPO` override, no contents API.

#### 6. Leverage scoring and CLI

- [ ] `tools/open_questions/score.py`: `w_plans · blocked_plans + w_issues ·
      blocked_issues + w_prune · prunes`, weights as named env-overridable constants
      with a grain-of-salt comment.
- [ ] `python -m tools.open_questions list [--json] [--include-issues]` with
      informative `--help`.
- [ ] `tests/unit/test_open_questions_score.py` green.
- [ ] **Milestone gate:** run `list --json` against the live corpus and read the
      output by eye against the docs. Cheap, once, and it is the only real check on
      Risk 1.

### M2 — Preparation and clip library

#### 7. Clip cache

- [ ] `tools/question_tree/clips.py`: key
      `sha256("v1|{text}|{requested_voice}|{force_cloud}|opus")` →
      `data/clips/<hash>.ogg` plus `<hash>.json` sidecar recording actual `backend`,
      actual `voice`, and `duration` from the result dict.
- [ ] Read-time validation: sidecar backend disagreeing with the requested backend is
      a miss. Backend is **not** in the key — `_is_kokoro_available()` cannot be
      predicted.
- [ ] Atomic writes: synthesize to a temp path in the same directory, then
      `os.replace()`. Sidecar after audio.
- [ ] Handle the `{"error": ...}`-only return shape at every call site.
- [ ] `tests/unit/test_question_clip_cache.py` green.

#### 8. Spoken-text guard

- [ ] Reject multi-digit runs and text over `MAX_TEXT_LENGTH` (4096) **before**
      synthesis, naming the offending text.
- [ ] `tests/unit/test_question_clip_text_guard.py` green.

#### 9. Authoring pass

- [ ] `run_typed()` (PydanticAI, not `claude -p` — non-harness call), Literal-typed
      Pydantic output following `tools/classifier.py:305-440`.
- [ ] Per question: spoken text, deeper-context clip text, readback line, plausible
      dispositions, the qids each disposition moots, and a draft writeback per
      disposition.
- [ ] Prompts carry no specific numbers or identifiers; clips reference work by name,
      per the `/do-voice-recording` prosody rule against reciting multi-digit
      identifiers aloud.

#### 10. Tree construction

- [ ] Rank by leverage; pick the root maximizing expected pruning; emit ordered nodes
      plus pruning edges; bound by a target session length (named constant).
- [ ] `python -m tools.question_tree prepare [--dry-run]`. `--dry-run` reports the
      tree and which clips would be cut without synthesizing.
- [ ] Refuse to build a tree from an empty question set rather than emitting an empty
      one.

### M3 — Delivery and writeback

#### 11. Shared question model

- [ ] Extract a frozen question model from `tools/ask_poll.py:52-88`
      (`normalize_options`), the escape-hatch literal at `:39`, the imported
      `POLL_QUESTION_MAX_CHARS` at `:132-135`, and
      `agent/output_handler.py:375-386` (text rendering).
- [ ] The poll branch consumes it with no behavior change. Existing poll tests green
      unchanged.

#### 12. Voice branch in the single degradation point

- [ ] Add the voice arm inside `tools/ask_poll.py:141-164`. **No second CLI** — the
      skill must not detect the surface.
- [ ] If P2 is unresolved, fail closed (hard-exit, matching `:134`) and say so in the
      output.
- [ ] `tests/unit/test_ask_poll_voice_branch.py` green.

#### 13. Widen the pause predicate

- [ ] `session_has_open_poll` → `session_has_open_question` at
      `agent/session_executor.py:1739`, consulting the voice registry alongside the
      poll registry.
- [ ] `agent/output_router.py:180` untouched: pure function, plain bool,
      `False` default.
- [ ] `test_output_router.py` and `test_poll_prose_answer_closeout.py` green
      **unchanged**.

#### 14. Interview driver and clip registry

- [ ] `tools/voice_interview/`: state file at `data/voice_interviews/{id}.json`
      holding at most one outstanding clip as an invariant.
- [ ] Send with `--voice-note --ack-sent-id`, **never** `--cleanup-after-send`. Read
      the ack immediately, in the same function, and record `{msg_id → qid}`.
- [ ] Bind via `reply_to_msg_id` → registry; positional fallback on a missed ack;
      **stop and name both candidates** on disagreement.
- [ ] Advance only after the current answer is bound and recorded. A second inbound
      note while nothing is outstanding is recorded as unsolicited.
- [ ] Off-script stops traversal and records verbatim. Partial leaves the question
      open with the steer recorded. An unanticipated disposition is off-script, never
      coerced to the nearest edge.
- [ ] `tests/unit/test_voice_interview_binding.py` and
      `tests/unit/test_voice_interview_traversal.py` green.

#### 15. Writeback

- [ ] Drafting: one read-only subagent per plan doc, in parallel, each given the doc
      path and bound answer explicitly. Each returns the exact `Edit` plus the text it
      was authored against.
- [ ] Applying: one writer, one doc at a time. `Edit` never `Write` (four PostToolUse
      validators match `Write` on `docs/plans` with `exit_policy = "propagate"`).
- [ ] One atomic shell invocation per doc: `git add <explicit paths> && git commit`.
      **Never** `git add -A`.
- [ ] Plan-only commits carry no closing keyword; anything outside `docs/plans/` needs
      `Closes #N` / `Refs #N` / `No-issue: <reason>`.
- [ ] Re-verify against current `main` before each apply; report drift and skip.
- [ ] `--dry-run` prints every `Edit` and the exact `git` invocation.
- [ ] `tests/unit/test_open_questions_writeback.py` green.

#### 16. End-to-end validation

- [ ] Run one real interview against the live corpus: prepare, deliver, answer,
      write back, then re-run `list` and confirm the answered questions are resolved
      and not re-asked.

### M4 — Convention forward

#### 17. Amend the planning convention

- [ ] `.claude/skills-global/do-plan/SKILL.md:399`: Phase 4 marks the Open Questions
      section instead of removing it.
- [ ] `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:495`: name one resolution
      convention, including how a defaulted item is marked (Open Question 2).
- [ ] Teach the harvester the new convention as the preferred shape.
- [ ] Land as its own commit — it changes planning behavior fleet-wide on the next
      `/update`.
- [ ] Retro-edit nothing.

### N-1. Documentation

- [ ] `docs/features/voice-interview-open-questions.md`: harvester, question tree,
      clip library and its cache-key reasoning, the binding contract and why no
      timestamp alignment is needed, and the sequential writeback contract with the
      #2650 rationale.
- [ ] Row in `docs/features/README.md`.
- [ ] `docs/tools-reference.md` entries for all three CLIs.
- [ ] Voice row in `.claude/skill-context/ask-me.md` branch table (`:9-19`) and
      degradation matrix (`:57-79`).
- [ ] Note in the feature doc that the capability is per-project and per-machine, and
      that cross-project harvesting is a non-goal.

### N. Final Validation

- [ ] Every Verification table row passes.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] The three regression-guard suites green **unchanged**.
- [ ] Documentation tasks above complete and committed.
- [x] Side findings filed: **#3332** (plan migration ignores `stateReason`; 18 abandoned
      plans filed as completed, including the single-writer lease) and **#3331**
      (`bf_alice` documented but absent from `KOKORO_VOICES`).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Existing extractor contract unbroken | `scripts/pytest-clean.sh tests/unit/test_open_question_gate.py` | exit 0, no test edits |
| New unit tests | `scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py` (and each sibling by name) | exit 0 |
| Lint | `python -m ruff check` | exit 0 |
| Format | `python -m ruff format --check` | exit 0 |
| Harvester runs on the live corpus | `python -m tools.open_questions list --json` | exit 0, counts match Success Criteria |
| Clips exist and are speakable | `python -m tools.question_tree prepare --dry-run` | every node has an on-disk clip; no multi-digit identifier in spoken text |
| Writeback is inspectable before it writes | `python -m tools.open_questions writeback --dry-run` | prints each `Edit` and the exact atomic `git add`/`git commit`; `scripts/check_issue_disposition.py` accepts the message |

## Critique Results

[skeleton — /do-critique fills]

## Open Questions

Four of the tracking issue's five original questions were resolved by the spikes in
`## Spike Results` and are recorded here as closed, not carried:

| # | Original question | Resolution |
|---|---|---|
| Q1 | Normalize the 24 plan docs' heading conventions, or teach the harvester all of them? | **Teach.** Normalizing costs ~50 item edits across 15 of 24 docs for zero mechanical consumer, and the real root cause is `/do-plan` Phase 4 deleting the section (M4). |
| Q3 | Share the `ask-me` transport or build a parallel one? | **Reuse.** Turn-ending and resume are already transport-independent; `tools/ask_poll.py:141-164` is the single degradation point and gets one new arm. |
| Q4 | Where do recorded answers go once `/do-plan` removes the section? | **Dissolved by M4.** The section is marked rather than removed, so the answer lands in place. |
| Q5 | Does this need a new global skill? | **No.** No new skill; a voice row in `.claude/skill-context/ask-me.md` and three CLIs. |

Two questions remain.

### P1. How is a defaulted question marked going forward? (blocks M4 only)

The corpus already contains three incompatible shapes for "asked, nobody answered, the
default stands": a section preamble ("Silence keeps the default"), an item-level
`Default:` line, and an item-level `**Disposition:**` paired with `Overturned by:`. M1
must read all three regardless. The question is which single shape `/do-plan`'s
template mandates from M4 onward.

- **Why it needs a human:** it is a convention choice that changes planning output
  fleet-wide on the next `/update`, and the three shapes encode genuinely different
  intents (a whole section defaulting vs. one item defaulting vs. one item defaulting
  with a named escalation path).
- **Default if silence:** item-level `**Disposition:** default — <what stands>`, because
  it is the only one of the three that survives a section being partially answered.
- **Does not block M1–M3.** Only task 17 waits on it.

### P2. Must #3095's two-enforcement-points decision be settled before M3? (blocks task 12)

`validate_poll_question` is enforced in two places with different failure modes:
`agent/output_handler.py:1560` logs and discards, `tools/ask_poll.py:134` hard-exits.
#3095 filed the divergence and it is unresolved. A voice renderer added at
`tools/ask_poll.py:141-164` inherits whichever behavior is there, so M3 either settles
it or codifies it by accident.

- **Why it needs a human:** picking one is a behavior change to a shipped poll path, not
  a new-code decision, and it belongs to #3095's scope rather than this plan's.
- **Default if silence:** task 12 fails closed (hard-exit, matching `:134`) and says so
  in its output, which is the conservative arm and leaves #3095 free to change it later.
- **Blocks task 12 only.** M1, M2, and M4 are unaffected.

### Filed separately, not carried here

- **P3 — filed as #3332, and it changes a premise of this plan.** The single-writer
  lease from #2650 was **never built**: `models/plan_doc_lease.py` and
  `.claude/hooks/validators/validate_plan_doc_lease.py` do not exist, and #2650 closed
  `NOT_PLANNED` (consolidated into #3065, still open). Its plan nonetheless sits in
  `docs/archive/plans-completed/`, because `scripts/migrate_completed_plan.py:384-412`
  reads GitHub's `state` and never `stateReason`; 18 archived plans are mislabeled the
  same way. So this plan's writeback contract (sequential applier, `Edit` only, explicit
  paths, one atomic stage-and-commit) is **the only** protection against the #2650
  hazard, not a belt-and-braces layer on top of a lease. Treat those four rules in
  task 15 as load-bearing.
- **P4.** `.claude/skill-context/do-debrief.md:21` documents `bf_alice` as "the female
  alternative", and `tools/tts/__init__.py:68` repeats it in a comment, but it is
  absent from `KOKORO_VOICES` (`:27-38`) and from `tools/tts/README.md:77`. It is
  unknown to both backends, so `_resolve_voice` (`:191-197`) returns an
  `{"error": ...}` dict — a hard synthesis failure, not a fallback. Affects M2 only if
  a clip recipe names that voice.
