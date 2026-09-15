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
| #3095 — poll-feature deferrals | Open | Live backlog on the poll channel, including owner decisions. Load-bearing: this plan must not leave a second half-finished question channel standing next to it. |
| #2650 — plan-doc writes in the shared `main` checkout have no single-writer protocol | Closed, protocol never built | **The governing hazard for writeback.** Catalogues seven collision shapes with SHAs, including an unrecoverable 2026-08-13 incident where a plan-doc agent's `git add -A` swallowed another lane's staged code onto `main` under an unrelated issue reference. |
| #1688 / PR #1847 — hook-driven turn returns | Shipped | The `AskUserQuestion` PreToolUse `needs_human` edge and the `pause_open_question` router verdict. A voice session must wait on this existing edge rather than invent a mechanism. |
| #2902 — meeting participant, joins invited Google Meets | Open, explicitly text-out-only | Nearest in-flight audio work. No overlap: it never speaks, and this never joins a call. |

### Why Previous Fixes Failed

Not applicable in the usual sense — no prior attempt at a voice question channel
exists. But one prior fix is *relevant by its absence*: #2650 correctly diagnosed
the plan-doc concurrency hazard and was closed without the mitigation being built,
and the plan that would have built it
(`docs/archive/plans-completed/plan-doc-single-writer-lease.md`) was swept into
the completed-plans archive by a content-free rename while still reading
`status: Ready`. The lesson this plan takes from that: do not stake the writeback
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

**Net-new, mostly additive.** Three new packages under `tools/`, one new skill,
one new gitignored artifact directory. No change to the bridge, the worker, the
relay, the session model, or any Popoto schema — which also means **no migration**
(the repo's Popoto migration requirement does not apply to this plan).

| Surface | Impact |
|---|---|
| `tools/open_questions/` | New package. Harvester, disposition classifier, leverage scoring, writeback. |
| `tools/question_tree/` | New package. Tree construction, spoken-text authoring, clip cache. |
| `tools/voice_interview/` | New package. The delivery session loop and its state file. |
| `tools/tts/` | **Unchanged.** `synthesize(text, output_path, ...)` already lets the caller choose the destination, so the clip library is a pure caller-side concern. |
| `tools/transcribe/` | **Unchanged.** Inbound voice notes are already transcribed into the agent's message text at `bridge/media.py:461`. |
| `bridge/telegram_relay.py` | **Unchanged.** The unlink is opt-in on `cleanup_file`; the clip sender simply never sets it. |
| `bridge/message_drafter.py` | **Unchanged** — see the deliberate non-refactor below. |
| `data/question_clips/` | New, gitignored (`data/` is ignored at `.gitignore:181`). The clip library and its index. |
| A new skill | The delivery half. Location (`.claude/skills-global/` vs `.claude/skills/`) is Open Question 5. |
| `docs/features/`, `docs/tools-reference.md` | New feature doc and CLI entry. |

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
`tools/voice_interview` imports both. Nothing in `bridge/`, `worker/`, or `agent/`
imports any of the three, so the blast radius of a bug is confined to a manually
invoked CLI and one skill.

**Replication.** Other machines run this against their own project. Anything
encoding *this* repo's plan-doc dialect (heading synonyms, disposition markers,
the `NON_LANE_PLANS` exclusion) must be overridable per repo rather than baked
into the global tool body — the mechanism the repo already has for this is
`.claude/skill-context/{skill}.md` for skills and named env-overridable constants
for tools.

## Appetite

**Large**, split into three independently shippable milestones. Each one ends
green and useful on its own, so the appetite can be spent down and stopped at any
boundary without leaving a half-built channel standing.

| Milestone | Deliverable | Standalone value if the plan stops here |
|---|---|---|
| **M1 — Harvester** | `tools/open_questions`: enumerate, classify disposition, score unblock leverage, CLI with `--json` | The corpus becomes machine-readable. `/ask-me` and the existing poll channel can consume it immediately, which is most of the velocity win without any audio. |
| **M2 — Preparation and clip library** | `tools/question_tree`: leverage ranking, answer dispositions, pruning edges, spoken-text authoring, content-addressed OGG/Opus clip cache | A prepared, inspectable question tree with audio. Usable manually (send clips by hand) and reusable by any future transport including the deferred live call. |
| **M3 — Delivery and writeback** | The voice session loop over Telegram voice notes, plus parallel-draft / sequential-apply writeback | The full loop the issue asks for. |

**Why Large and not Medium.** Three genuine gaps have to be built, not wired:
there is no clip library (the current send path actively deletes clips), no
cross-document question harvester, and no answer-to-question binding. The
classifier in M1 alone has to handle seven mutually incompatible resolution
conventions across nine distinct heading strings. Medium would force dropping
either the classifier's fidelity — which produces a channel that asks resolved
questions and loses owner trust on its first call — or the writeback, which leaves
the answers in chat where they already are.

**What the appetite buys generously, by design.** All intelligence runs in M2,
before the session starts, where it has full context and no latency budget. The
owner's explicit trade: spend heavily on preparation so the delivery loop is dumb
and the call traverses the shortest path to unblocking work. Preparation cost is
therefore not a thing to optimize in this plan.

**What the appetite does not buy.** Everything in No-Gos, most importantly the
live phone call and any plan-doc locking protocol.

## Prerequisites

[skeleton — Phase 2 fill]

## Solution

### Key Elements

[skeleton — Phase 2 fill]

### Flow

[skeleton — Phase 2 fill]

### Technical Approach

[skeleton — Phase 2 fill]

## Failure Path Test Strategy

[skeleton — Phase 2 fill]

## Test Impact

New test files are added under `tests/unit/` for each new module; no existing
test is expected to change behavior, but `tests/unit/test_open_question_gate.py`
is a direct consumer of the extraction logic being lifted out of
`bridge/message_drafter.py` and must stay green unchanged as the regression
guard for that refactor.

- [ ] `tests/unit/test_open_question_gate.py` — must pass unchanged after the
      extractor is moved; this is the contract that the lift did not change
      behavior for its existing caller.
- [ ] `tests/unit/test_open_questions_extract.py` — new. Corpus-fixture tests for
      all resolution-marking shapes, including defaulted items.
- [ ] `tests/unit/test_open_questions_score.py` — new. Leverage scoring and
      pruning-edge selection on synthetic question sets.
- [ ] `tests/unit/test_question_clip_cache.py` — new. Cache-key coverage and
      reuse/invalidations.
- [ ] `tests/unit/test_voice_interview_session.py` — new. Answer-to-clip binding,
      pruning traversal, off-script stop.
- [ ] `tests/unit/test_open_questions_writeback.py` — new. Sequential apply,
      `Edit`-only on plan docs, drift detection, commit-message shape.

Run scope: `scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py`
and siblings by name. Never a full `tests/unit/` sweep for this work.

## Rabbit Holes

[skeleton — Phase 2 fill]

## Risks

[skeleton — Phase 2 fill]

## Race Conditions

[skeleton — Phase 2 fill]

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
  Justification: [skeleton — decided by spike-normalize + Open Question 1].
- **Timestamped transcript alignment.** Justification: unnecessary on the
  Telegram transport, where one inbound voice note answers one outbound clip, and
  it would force the cloud STT path since the local SuperWhisper backend returns
  no timestamps.

## Update System

[skeleton — Phase 2 fill]

## Agent Integration

[skeleton — Phase 2 fill]

## Documentation

- [ ] Create `docs/features/voice-interview-open-questions.md` describing the
      harvester, the question tree, the clip library, the delivery loop, and the
      sequential writeback contract.
- [ ] Add a row to `docs/features/README.md` index table.
- [ ] Add the `tools.open_questions` CLI to `docs/tools-reference.md`.
- [ ] Update `.claude/skill-context/ask-me.md` if the voice mode shares that
      skill's transport (depends on Open Question 3).

## Success Criteria

[skeleton — Phase 2 fill]

## Team Orchestration

[skeleton — Phase 2 fill]

## Step by Step Tasks

[skeleton — Phase 2 fill]

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

[skeleton — Phase 2 fill]
