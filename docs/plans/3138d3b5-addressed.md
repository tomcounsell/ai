# Team chats: hear what is meant for Valor without a tag

Tom, 2026-11-17 05:36 UTC (#valor-dev): in mentions-only team chats Valor
also gets involved in messages meant for him that neither name him nor
reply to his post, decided by a classifier over every message. Answers
(2026-11-17): all mentions-only rooms; "for Valor" first, "would speak up"
second and leaning no; no cap; a reply chain reaching his post wakes him
with no classifier; the untagged messages, with a few lines of context, go
to Jev and Gemini. Tom, 05:54: "usually nothing, half the time an emoji.
sometimes a short line. and a full handling when it's clearly in response
to something you were already engaged in." The teammate voice is task
b4c0d2aa2e8c's.

## The cases (observed in two Macs' ledgers; specifics kept private)

1. A client team group: a plain answer to Valor's post, no tag, no link,
   bound `heard`; no turn.
2. A client team group: a reply to a reply under Valor's post, and two
   more down that chain, all `heard`: `_replied` resolved only the post
   `reply_to` names.
3. A public group where people usually tag Valor: some untagged messages
   were for him; most are links and talk between others.

## What is built

1. **Chain rule** (`intake._replied`): when `reply_to` names no post of
   Valor's, the newest of his posts among the chain's ids (`thread`, which
   the Telegram bridge fetches from Telegram) binds as a direct reply.
2. **Site `intake.addressed`** (`core/judgement_tasks.py`), one PICK
   question: `engaged`, `aside`, `react`, `none`, floor 0.5. Inputs: the
   message, the chat's last 8 messages with Valor's sends into it, how long
   since he posted there, and the reply chain from the `message.received`
   row. Gemini decides for this site, Jev beside it (`JudgementTask.primary`).
   Quiet (`ledger.QUIET_SITES`).
3. **Asked off the binding path.** A heard message in a mentions-only room
   is marked `addressed: asked`; a kernel job per message (`serve.react`,
   `intake.address`) asks, up to `judgement.UNANSWERED_RUNS` failures.
   `engaged` steers as a mention does (👀); `aside` steers marked "not
   addressed to you"; `react` owes a done reaction told it is an
   acknowledgement; `none`, an abstain or a failure leaves it heard. A
   message the session already read is not steered again.
4. **Guidance**: `skills/sdlc/converse.md` in Tom's words; the private room
   texts that said "answer whoever mentions you" say untagged messages
   meant for Valor reach him too.

## Calibration (private cases, `~/Desktop/Valor/judgement/`)

71 labelled heard messages from the three groups (5 engaged, 3 aside, 4
react, 59 none), run with `python -m core calibrate`, Gemini first as the
port runs it: 4 of 5 `engaged` caught (case 1 among them), **1 of 59 talk
between others falsely woken** as engaged, `aside` 0 of 3, `react` 2 of 4;
64 of 71 right. Jev first: 2 of 5 engaged, case 1 missed, 1 false wake.
The legs agree on 65 of 71. Labels are Valor's, so `calibrated=None`.

## Cost and latency

Per message, both legs: about $0.0001 (Gemini $0.000068, Jev $0.000038).
Gemini median 652 ms (p90 825); binding is not delayed. Volume: about 40
heard messages a week on each of the two Macs with busy groups.

## Not here

No cap, no new storage, no third provider. Slack threads already resolve.
Emoji-only asides are one line away (`aside` calling
`reactions.acknowledge`). **Governance:** none; the consumer only adds
wakes and reactions, and every failure is today's behaviour.

## Tests and done

`tests/test_addressed.py` (chain, inputs, each label, failures, already
read, rendered once, the kernel job) and `tests/test_judgement.py`
(Gemini-first routing, agreement). Done when, after rollout to the four
Macs, Tom sees Valor pick up an untagged message meant for him and stay out
of talk that isn't.
