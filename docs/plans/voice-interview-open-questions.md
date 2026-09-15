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

[skeleton — Phase 2 fill]

## Data Flow

[skeleton — Phase 2 fill]

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
