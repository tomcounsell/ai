# Email: a long draft first, then editorial passes against Tom's guide

Tom, 2026-11-09 (Slack DM): his guide "Writing Emails That Work" goes into the
email flow as an editorial pass after a full draft, never as a recipe for
writing the email in one go. At least one pass on every email, more when the
stakes are high. Background: on 2026-11-01 a client email Valor drafted ran
long, and the guide was nowhere in the flow.

## What is true today (checked)

- `yudame/skills` (public; no CLAUDE.md or AGENTS.md; practice is a plan in
  `plans/`, then a commit with its `tests/check_*.py`, listed in README's test
  line): `plugins/yudame/skills/email/SKILL.md` says only "Draft first", then
  de-slop before an external email leaves. It has no references directory and
  does not cover chat.
- `de-slop` is a cold read of the draft alone that keeps facts and commitments
  word for word and adds nothing. The guide needs the drafting context (who
  agreed a promise, where each fact came from), and it deletes promises and
  puts the answer in the email ("do it now"). Folding it into de-slop would
  break both of de-slop's rules. These are two concerns: the guide pass is
  the author's rewrite with the sources at hand, and de-slop is the cold copy
  edit after it. So the guide goes into the email skill, and de-slop is unchanged.
- `tomcounsell/ai` (public): the chat session drafts email, and sends its own
  replies in an email thread with `email.send` (`skills/sdlc/converse.md`,
  lines 307-319). Every task turn, at any stage including patch, gets
  `skills/sdlc/channel.md` (`core/tasks.py` `channel_text`), which already
  holds the task rule for email (lines 13-18). Each turn loads the yudame plugin
  from the marketplace clone that the nightly job pulls (`skills/README.md`).

## What will be built

In `yudame/skills`, on a branch, as a PR that I merge:
1. `plans/email-editorial-pass.md`: a short plan in that repo's style.
2. `plugins/yudame/skills/email/references/writing-emails-that-work.md`: the
   guide copied byte for byte (sha256 `be4f4822…6343b9`).
3. `email/SKILL.md`, a **Drafting** section before Sending, in this order:
   (a) write the full draft from the context and goal, as long as it needs to
   be, without trying to follow the guide; the guide is never used to write
   the first draft. (b) At least one editorial pass, done by the author with its
   sources at hand: rewrite the whole draft against the guide's rules, then
   go through its checklist. High stakes (a client, money, terms, a complaint,
   anything hard to take back) get at least two passes, each on the last
   pass's result. (c) Then the existing de-slop step, unchanged, with its
   "trivial logistics can skip" line kept as it is. The guide pass has no skip:
   every email gets it, a one-liner included, and the skill says so. The description becomes "drafted in
   full, edited against the email guide, then de-slopped".
4. `tests/check_email.py` (stdlib): asserts the guide's sha256, that SKILL.md
   links it, says it is never used for the first draft, puts the full draft
   before the editorial pass and the pass before de-slop, says high stakes
   get at least two passes, and that the Drafting section has no skip or
   exemption wording (the de-slop skip stays in Sending). It fails on today's file. Added to README's test line.

In `tomcounsell/ai`, after that merge:
5. One sentence, the same in two places: an email, drafted for someone or
   sent, follows the email skill's flow (`yudame:email`): the full draft first,
   then at least one editorial pass against its guide (more when the stakes are
   high), then de-slop. In `converse.md`, placed before the "A draft is text"
   paragraph so it covers both the draft and the `email.send` reply below it;
   in `channel.md`, beside the existing email line, so every task stage (patch
   too) has it. The stage files are not changed.
6. `tests/test_kernel.py`: a test that the rendered chat-session text and
   `channel_text()` carry that sentence.
7. Before the demo: `claude plugin marketplace update yudame` and
   `claude plugin update yudame@yudame` on this Mac, and confirm the
   installed email SKILL.md has the Drafting section, so the demo and the
   next real drafts run the new skill.

## Demo (build stage, not committed, sent to no one)

A made-up long client reply (made-up names), run through the flow: two guide
passes (client means high stakes), then de-slop in a fresh subagent. The
before, the after each pass, and de-slop's verdict go in the delivery only.

## Out of scope

- Chat replies (the skill does not cover chat). The de-slop skill. Mercury's
  invoice notice, which is a generated template, not a draft. No new check,
  gate or approval step: this is wording, and tests of documented behavior.

## Done when

Both merged; `python3 tests/check_email.py`, `check_skills.py` and the ai suite
pass; the demo before and after are in the delivery. Done in Tom's sense (rule
5) when an email drafted under it reaches him, recorded with the date.
