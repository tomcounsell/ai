---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-10-26
tracking: https://github.com/tomcounsell/ai/issues/2902
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-10-26T09:14:26Z
---

# Valor in Google Meet

**Ask.** Tom, 2026-10-26: Valor joins Meet calls "just like any other user
would join" (valor@yuda.me, a Workspace seat); first connecting, then
answering when addressed in a group; video last. "full duplex is more
important than voice quality". Turn taking and usefulness "as human-like
as possible (not visually)". Tools and subagents still work, faster:
"named worker agents that are mostly read-only and high token rates".
Whether to speak: "a decision (Jev-like) model". And: when he has a quick
answer, or needs time for tools, he types it in the chat "anytime" rather
than interrupting.

**Freshness and prior art.** #2902 (2026-08-20, old system) asks for a
chat-only Meet participant. Every file it cites is gone with the rebuild
(baseline b3d8f08d7); its product rules carry over (precision over
recall, never block listening on thinking, a late answer refers back to
its question). Its design branch (`claude/live-streaming-bridge-design-63h5bt`)
chose a browser participant reading Meet's captions. Dropped from #2902:
the per-hour cap, cooldown and ownership check (governance with no
incident or grant), the post-nothing shadow phase (rule 5), and
`read_the_room` (gone; Jev sees what Valor already posted).

**Premise, checked 2026-10-26.**
- Meet Media API: preview, every participant enrolled, no way to send
  audio. **Rejected.**
- Attendee (attendee-labs/attendee @ 5acfb99, ELv2) as a server: Docker VM
  of about 5 GB, its own Postgres, Redis and celery, an amd64-only Chrome
  134 (arm64 only in open PR #1064), sign-in only by SAML SSO through it.
  **Rejected.** Its in-page payloads are what we need and run in any
  Chrome: `google_meet_chromedriver_payload.js` hooks `RTCPeerConnection`
  for audio per received stream, labelled with the loudest speaker;
  `shared_chromedriver_payload.js` overrides `getUserMedia` with a virtual
  mic; chat is read from Meet's messages and typed to everyone (Meet has
  no private chat). **Reused**, vendored at that commit with its notice.
- Pipecat 1.9.0 installs on 3.14 without torch but models one user per
  pipeline. **Not adopted**; its Smart Turn v3 (`local_smart_turn_v3.py`,
  `_whisper_features.py`, model file; BSD-2; 43 ms per check here) is
  copied. Silero VAD comes from onnx-asr, already used.
- Jev on the Cowboy, 3,566 calls: median 389 ms, 90% under 637 ms. The
  fallback (Qwen3-235B): median 6.0 s.
- The Cowboy: M4, 16 GB, no Docker. Native Chrome (1.5 to 2.5 GB), Parakeet
  (1.7 GB) and Kokoro (0.6 GB) fit beside the kernel; the Docker route did
  not comfortably.

## What is built

1. **The Meet bridge, `bridges/meet/`.** A dedicated Chrome profile on the
   Cowboy (not BYOB's: a call would hold BYOB for its length), signed in
   once as valor@yuda.me, driven by Playwright with the vendored payloads.
   `python -m bridges.meet join URL` joins as Valor, camera off; `leave`
   ends it. On joining he posts a notice that he keeps a transcript for
   follow-ups (all-party consent; wording is the builder's; that he is an
   AI is known, Tom 2026-10-26). A call is a task whose Brief chat is
   `{"channel": "meet", "chat_id": <meeting code>}`. Like the other
   bridges it imports only the port's allowed modules (`core/bridge.py:7`)
   and sends what the outbox yields: `DECLARED` (`core/bridge.py:274`)
   gains `meet.send_message` (chat post, `act`, payload `{"text"}`) and
   `meet.say` (speech, `act`, payload `{"text"}`); `_refuse_meet`, a
   shape check like `_refuse_local` (`core/bridge.py:243`), refuses empty
   text, files, or a meeting the bridge is not in (it can reach no other); `LIMITS` gains `meet`: 500 characters per chat
   post (Meet's chat limit, confirmed on the live run), no files.
   Per-speaker PCM in and the reply's PCM out travel beside the port, as a
   local stream between the bridge and the voice process, never through
   the outbox. Whatever way `join` exits, it closes Chrome and ends the
   task: `leave`, a crash, being denied at or left over 10 minutes in the
   lobby, or being removed after joining. The page signals for lobby and
   removal are recorded on the `VALOR_LIVE` run; the stand-in page cannot
   prove them. It logs frames per speaker every 10 s, so a dead payload
   shows. Speakers come from the labelled audio; Meet's captions are not
   read (a follow-up only if Tom's done call shows speakers mislabelled).
2. **Hearing**, in `core/call.py`, which holds the call loop (turn ends,
   deciding, drafting, barge-in) and runs in the voice process
   (`tools/voice.py`, which may import `core/`; a bridge may not). Per
   speaker: Silero VAD, Smart Turn v3 at each pause, Parakeet on the
   finished turn. `tools/voice.py`: one cached `_asr()`; `listen` splits
   into decode and recognize-from-samples, which `/transcribe` and the
   call's PCM share; Kokoro loads there too (docstring updated). The call's
   hearing and speaking each get their own worker, apart from the file
   worker, so neither waits on the other or on a voice note.
3. **Deciding.** At each turn's end the judgement task `meet.speak`, a
   `pick` question, asks Jev about the recent lines (with speakers),
   whether Valor spoke last, and what he posted. Labels: asked Valor
   (speak), something useful to add (post in chat), someone else's turn,
   nothing for him; the last two do nothing. Floor 0.60 on both legs (a
   clear majority of four; under it the leg abstains). "Post" ships
   enabled on first release; Tom's done call judges it. Abstain and the
   failure route are silence, recorded in `judgement.*` rows. Each answer
   carries its speaker's turn id, which any new speech from that speaker
   advances; an answer or draft is current only while that turn id is
   current and nobody else has spoken since the turn ended. `core/call.py`
   drops one that is not, a late fallback answer included.
4. **Answering**, front model first, workers second. A front model (Haiku
   5.5, streamed through the gateway) drafts while Jev decides; the draft
   is dropped on a no. A turn's startup is too slow for a live reply
   (step 4 measures it against a warm session). Its one tool asks a
   named, mostly read-only worker (repo, mail and calendar, web, memory),
   a Claude Code session started at join through the harness, so it is
   metered, on a fast model.
   A spoken question that needs a worker gets "I'll put it in the chat",
   and the answer is posted when ready, naming the question it answers.
   Human-like timing: a natural gap before speaking; he stops within a
   word when the person he answers talks, not for side talk; short
   answers by name; no "mm-hm".
   **Effects.** One `act` effect per reply, never per frame: `core/call.py`
   requests `meet.say` or `meet.send_message` for the call task, and the
   bridge plays or posts it on release. A reply cut by barge-in settles
   `done` with `spoken_text` (what was heard) in its outcome. Both go to
   the task's own chat, so no `notices.report` is owed
   (`core/broker.py:45`, `core/notices.py:444`); `_own_chat`
   (`core/notices.py:435`) gains `meet.say` beside `.send_message`. The
   bridge's `lookup` answers absent for both, so after a restart
   `reconcile` (`core/broker.py:323`) settles an in-flight one `failed`
   and nothing replays it: turn ids are not persisted, so any answer from
   before the restart is stale. Step 2 measures the release round trip
   (request to first played frame) on the Cowboy.
5. **After the call**, the transcript and what he promised become a task.
6. **Video**, last, its own plan.

**No-Gos.** [ORDERED] joining from the calendar on his own, after Tom has
used this. [SEPARATE] Zoom, Teams; telling apart people sharing one mic.
Two calls at once (one profile, one call).

**Risks.** Meet's page changes break the payloads: one seam, re-vendored
from Attendee. Crosstalk mislabels the quieter speaker. Google may
challenge the automated profile: sign in by hand once, keep the profile.
**Races.** Barge-in and staleness: item 3 and Effects.

**Governance.** None added.

## Tests and verification

A local page with two `RTCPeerConnection`s stands in for Meet: per-speaker
audio in, virtual-mic audio out, chat both ways. Turn ends on recorded
speech; three talkers over fewer streams; `meet.speak` routes (speak,
post, silent, abstain, failed, stale turn id, another speaker since);
one effect per reply, no report owed, a barged reply `done` with
`spoken_text`; a kill mid-reply never re-speaks on rejoin; barge-in by the addressed
speaker but not by side talk; a worker's late answer posted; one Parakeet
and Kokoro copy; transcript latency while speaking and while a voice note
transcribes; `leave` and a crashed `join` both end the task. Live
(`VALOR_LIVE=1`): Valor joins a test Meet signed in; lobby and removal
page signals and the chat limit recorded; memory, latency and the release
round trip logged. `uv run pytest -q -n auto`, ruff, `tests/test_bloat.py`.

**Done** when Tom has a real Meet call with Valor in it, Valor joins as
himself and speaks or posts when it is his turn, with a tool where
needed, and Tom records the date with what he saw: barge-in worked, no
unprompted speech in side talk, the reply delay heard, which posts were
worth reading.
