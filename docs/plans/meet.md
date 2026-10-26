---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-10-26
tracking: https://github.com/tomcounsell/ai/issues/2902
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-10-26T08:58:16Z
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
   ends it. I/O only, through `core/bridge.py`'s port: per-speaker PCM and
   chat in, PCM and chat out. A call is a task; speaking and posting are
   `act` effects the broker releases to it. Whatever way `join` exits, it
   closes Chrome and ends the task. It logs frames per speaker every 10 s,
   so a dead payload shows. Step 1 records Meet's captions beside the
   labelled audio on one real call and keeps whichever names speakers
   better in crosstalk; the other is deleted.
2. **Hearing**, in `core/call.py`, which holds the call loop (turn ends,
   deciding, drafting, barge-in). Per speaker: Silero VAD, Smart Turn v3
   at each pause, Parakeet on the finished turn. `tools/voice.py`: one
   cached `_asr()`; `listen` splits into decode and recognize-from-samples,
   which `/transcribe` and the call's PCM share; Kokoro loads there too
   (docstring updated). The call's hearing and speaking each get their own
   worker, apart from the file worker, so neither waits on the other or on
   a voice note.
3. **Deciding.** At each turn's end the judgement task `meet.speak`, a
   `pick` question, asks Jev about the recent lines (with speakers),
   whether Valor spoke last, and what he posted. Labels: asked Valor
   (speak), something useful to add (post in chat), someone else's turn,
   nothing for him; the last two do nothing. Abstain and the failure route
   are silence, recorded in `judgement.*` rows. Each answer carries its
   speaker's turn id, which any new speech from that speaker advances;
   `core/call.py` drops an answer or draft whose turn id is no longer
   current, a late fallback answer included.
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
5. **After the call**, the transcript and what he promised become a task.
6. **Video**, last, its own plan.

**No-Gos.** [ORDERED] joining from the calendar on his own, after Tom has
used this. [SEPARATE] Zoom, Teams; telling apart people sharing one mic.
Two calls at once (one profile, one call).

**Risks.** Meet's page changes break the payloads: one seam, re-vendored
from Attendee. Crosstalk mislabels the quieter speaker. Google may
challenge the automated profile: sign in by hand once, keep the profile.
A broker release per reply adds a few milliseconds; measured in step 2.
**Races.** Barge-in cancels the playing reply and the effect records the
part spoken; a stale answer or draft is dropped by turn id (item 3).

**Governance.** None added.

## Tests and verification

A local page with two `RTCPeerConnection`s stands in for Meet: per-speaker
audio in, virtual-mic audio out, chat both ways. Turn ends on recorded
speech; three talkers over fewer streams; `meet.speak` routes (speak,
post, silent, abstain, failed, stale turn id); barge-in by the addressed
speaker but not by side talk; a worker's late answer posted; one Parakeet
and Kokoro copy; transcript latency while speaking and while a voice note
transcribes; `leave` and a crashed `join` both end the task. Live
(`VALOR_LIVE=1`): Valor joins a test Meet signed in; memory and
latency logged. `uv run pytest -q -n auto`, ruff, `tests/test_bloat.py`.

**Done** when Tom has a real Meet call with Valor in it, Valor joins as
himself and speaks or posts when it is his turn, with a tool where
needed, and Tom records the date with what he saw: barge-in worked, no
unprompted speech in side talk, the reply delay heard, which posts were
worth reading.

## Open Questions

1. Should Valor say in the chat when he joins that he is an AI? Joining
   "like any other user" suggests no; people in the call may expect it.
   The critique recommends a one-line chat note on join as the default
   before any call with outside people.
