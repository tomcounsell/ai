# Rebuild: open questions for Tom

Each question is one decision. Answer with the letter, or "default" to take
the recommendation. Questions that block planning the rebuild come first.
Terms used throughout: a **tap** is your one-time approval of one specific
action (a push, a merge, a message). The **gateway** is the local proxy that
every Claude call goes through so its cost is counted against the task's
budget. A **cheap judgement model** is a small hosted model (Jev, or OpenAI's
judgements API) that answers yes/no style questions for under a cent.

## Blocks the rebuild plan

### 1. Should each component be built by one session you start, or by a lead agent managing builder agents?
*Example:* the old system ran psyoptimal #872 through plan, critique, revision,
re-critique, build, test, and 3 review rounds (16 commits). The replay with no
plan at all reached the same quality score (4 of 5) for $1.30 to $2.10.
- **A.** One session per component: one plan, one build, one independent check, no running findings log. **(Recommended)** The baseline showed the extra rounds bought nothing measurable.
- **B.** A lead agent hands work to builder agents in separate worktrees.
(docs/architecture.md, docs/sdlc-state-machine.md)

### 2. Is this the build order?
*Example:* the kernel (task, budget, ledger, approvals) and its database first;
Telegram and email second; the `claude` CLI wrapper third; persona and
scheduled routines fourth; tools as needed; memory last, once popoto ships
Postgres.
- **A.** Yes, that order. **(Recommended)** Each step only needs the ones before it.
- **B.** Change it (say how).
(all docs)

### 3. Should Valor's work be walled off from your Mac account and your Claude login?
*Example:* in the #894 demonstration Valor's sandbox ran as your user, so a
deliberate `security` command could have read your keychain, and the open
internet meant it could have called Claude with your Claude Code login,
skipping the $15 budget entirely.
- **A.** Yes, in the first kernel milestone: Valor runs as a separate macOS user, and only the gateway holds the Claude key. **(Recommended)** It closes both holes by construction instead of by sandbox rules.
- **B.** Later; keep the current sandbox for now.
(docs/machine.md, docs/tech-stack.md, docs/architecture.md)

### 4. Is the MacBook Air dedicated to Valor?
*Example:* after macOS, Postgres, the bridges, and one Valor session, about
6 GB of RAM is left. Chrome, Slack, and other desktop apps would take about
half of that.
- **A.** Yes, dedicated. **(Recommended)** The memory plan only works with that headroom.
- **B.** No, you also use it day to day; the plan budgets for desktop apps.
(docs/machine.md)

### 5. Should the model that checks Valor's work be as strong as the one that did it?
*Example:* in the baseline, a cheaper Sonnet reviewer accepted popoto #191
(the blind judge scored it 1 of 5; it passed 3 of 11 hidden tests) and
accepted #633 with a stale-cache bug moved, not fixed.
- **A.** A frontier-strength model, but a different one from the builder (for example an earlier Opus). **(Recommended)** A weaker checker misses a stronger builder's mistakes; the extra cost per task is to be measured.
- **B.** A cheaper Sonnet-class checker.
(docs/architecture.md, docs/sdlc-state-machine.md, docs/tech-stack.md)

### 6. Does your 2026-10-01 "lean SDLC" decision count as your approval for the test-breadth check?
*Example:* the rule is that any new check needs your tap and expires after
90 days unless it proves useful. The breadth check exists because every
replay wrote too few tests: #894 shipped 10 tests against the real PR's 35,
and #872 missed the archived-team cases.
- **A.** Yes, that decision is the approval; it expires 90 days from it. **(Recommended)** You already chose it on purpose.
- **B.** No, it needs its own tap.
(docs/sdlc-state-machine.md, docs/judgement-layer.md)

### 7. Which cheap judgement model reads each request first, and what backs it up?
*Example:* "Home page notification if user profile settings not complete"
gets one quick call that decides "ask before building" or "just build". It
costs well under $0.05; on #894, asking first would have saved an estimated $1.40 to $1.80 and a feedback round.
- **Primary.** **A.** Jev. **(Recommended)** The setup plan's pick. **B.** OpenAI's judgements API.
- **Backup when it is down.** **A.** The same open model hosted by a second provider. **(Recommended)** Uses no Mac memory and works while Valor is busy. **B.** A local copy (about 5 GB) loaded only when Valor is idle; works offline but waits.
(docs/judgement-layer.md, docs/tech-stack.md, docs/machine.md)

### 8. Can the old code in `_archive_/` be deleted once the rebuild plan is written?
*Example:* `_archive_/agent/pipeline_state.py` and the old bridge and worker
code. The new docs, the #894 demonstration, and the six-replay baseline are
what the rebuild reads from.
- **A.** Yes. **(Recommended)** Git history keeps every file if one is ever needed.
- **B.** Keep it until the new kernel reruns the #894 demonstration.
(docs/README.md, CLAUDE.md)

## Decide soon

### 9. Should Valor be able to message you directly without asking first?
*Example:* when Valor messages a client (e.g. Wayne: "The 180 report now says
which requirement is missing"), that waits for your tap. But when Valor
messages YOU ("Question before I build: does incomplete mean the whole
profile or just the sports dates?"), requiring your tap first would be circular.
- **A.** Yes, messages to your own chat go straight out; everything else waits for a tap. **(Recommended)** Your chat is where you tap, so it cannot itself wait on a tap.
- **B.** No, every message waits.
(docs/bridges/telegram.md, docs/architecture.md)

### 10. May the text of a client's request go to the cheap judgement model?
*Example:* your psyoptimal request about coaches assigning evaluations (#872)
sent to Jev to decide whether to ask questions first. Request text only, no code.
- **A.** Yes, request text only. **(Recommended)** It is one sentence, and the alternative runs the same check on Opus at about 100 times the cost.
- **B.** No, client work stays on Anthropic only.
(docs/judgement-layer.md)

### 11. How should your interruptions per task be counted, and should a limit ever block Valor?
*Example:* #894 cost you 2 feedback rounds and 3 push approvals. Counting
approvals, that is 5 interruptions; not counting them, 2.
- **Count.** **A.** Questions and feedback rounds; approvals counted separately. **(Recommended)** Approvals are the price of keeping Valor's authority small, not a sign it was confused. **B.** All three together.
- **At the limit.** **A.** Never block; flag it on the delivery. **(Recommended)** A blocked question makes Valor guess, which costs you more later. **B.** Stop and ask.
(docs/mission.md, docs/architecture.md, docs/data.md)

### 12. When you start a task from a Telegram message, what budget does it get?
*Example:* you message "list() in popoto hydrates every row twice, fix it".
- **A.** A fixed default from settings ($8, the baseline's per-run budget; every replay spent under $2.20), with every push or send still waiting for your tap. **(Recommended)** A message should never be able to grant itself more money or authority.
- **B.** Let you write a budget in the message ("budget $20").
(docs/bridges/telegram.md)

### 13. Which past work should Valor replay to test itself, and how much per run of the test set?
*Example:* the baseline replayed 6 requests from psyoptimal, popoto, and
cuttlefish for about $22 in total.
- **A.** Those three repos, requests you wrote in the last 12 months, $25 per run. **(Recommended)** Same repos as the baseline, so results compare.
- **B.** Different repos, dates, or budget (say which).
(docs/emulator.md)

## Can wait

### 14. Should replies in one chat be pre-approved, so you stop tapping each one?
*Example:* in a busy group with a client, every Valor reply waits for you.
- **A.** Not yet; tap each one. **(Recommended)** Pre-approval widens authority; revisit once you see how many taps it really is. **B.** Yes, per chat.
(docs/bridges/telegram.md, docs/bridges/email.md)

### 15. When a check reaches its 90-day expiry and it did catch something, what happens?
*Example:* in 90 days the test-breadth check comes due, and it caught missing tests on two deliveries.
- **A.** It expires unless you renew it with a new tap. **(Recommended)** Keeps checks from piling up silently. **B.** It stays as long as it keeps catching things.
(docs/judgement-layer.md, docs/mission.md)

### 16. Should the 90-day cleanup (removing unused routines, checks, and tools) merge without your tap?
*Example:* a branch that deletes a routine nobody ran since July.
- **A.** No, it waits for a tap like any merge. **(Recommended)** One tap per sweep is cheap. **B.** Yes, pre-approved.
(docs/routines.md)

### 17. Is "the email passed your domain's DMARC check" enough proof an email is from you?
*Example:* an email from your address saying "start on psyoptimal #893".
- **A.** Yes, and treat it as an approved check now. **(Recommended)** Without it anyone could spoof your address. **B.** No, email can only start tasks after a Telegram confirmation.
(docs/bridges/email.md)

### 18. How do you approve from your phone?
*Example:* Valor asks to push the #894 branch while you are out.
- **A.** A tap in Telegram. **(Recommended)** No new surface to build. **B.** A small web page, with a passkey signature for pushes and sends.
(docs/tech-stack.md)

### 19. What should the skill system do?
*Example:* today `do-build` lives in this repo and is copied to every machine;
you also want skills that update psyoptimal's own repo-specific skills.
- **A.** Decide when `skills/` is built. **(Recommended)** Nothing in the first three components needs it. **B.** Tell me the requirements now (a voice note is fine).
(skills/README.md, docs/harnesses.md)

### 20. Small machine and housekeeping calls
- **Sleep:** keep the Air awake on mains power **(Recommended)**, or let each session hold it awake. (docs/machine.md)
- **Backups:** nightly database dump to an external disk, keep 30 dumps and keep the ledger forever **(Recommended)**. (docs/data.md, docs/tech-stack.md)
- **Answer keys:** confirm the inferred answers for cuttlefish #646, popoto #191, and popoto #188 (about 5 minutes). (docs/emulator.md)
- **Flutter:** do not install Flutter for the one usable mobile test case (localsend #2765) **(Recommended)**. (docs/emulator.md)

## Decided by default (tell me if wrong)

- Valor's past corrections are stored by the kernel; memory later reads them and never writes them. (docs/architecture.md, docs/data.md)
- Work counts as delivered only after the independent check passes, not when Valor says it is done. (docs/architecture.md, docs/sdlc-state-machine.md)
- Valor's sessions run under the macOS sandbox; the checker reruns tests in a fresh Apple container. (docs/architecture.md, docs/tech-stack.md)
- When the cheap judgement model is unsure whether a request is clear, Valor inspects the code and asks only if something material is open. (docs/sdlc-state-machine.md)
- Your feedback on a delivery goes straight to rework, without a judgement call. (docs/sdlc-state-machine.md)
- The judgement model's accuracy bars are set from its first measured results. (docs/judgement-layer.md)
- A code summary is added to the judgement model's input only if replays show it helps. (docs/judgement-layer.md)
- Running out of budget ends the task with a question to you asking for more; never silence. (docs/architecture.md)
- A doc found contradicting the code opens a fix task; it blocks nothing. (docs/judgement-layer.md)
- Approvals record whether you tapped or someone stood in for you. (docs/architecture.md, docs/data.md)
- A correction is withdrawn by a later correction naming it. (docs/architecture.md)
- The kernel gets its own database login that only it holds. (docs/data.md)
- An always-on kernel process is built together with the bridges. (docs/tech-stack.md, docs/routines.md)
- Telegram uses Telethon; email uses Python's built-in IMAP and SMTP, adapted from existing code. (docs/tech-stack.md)
- Replay costs (stand-in and judge calls) go through the gateway under one budget. (docs/emulator.md)
- The stand-in for you in replays moves to a stronger model, since Sonnet accepted every run. (docs/emulator.md)
- Valor's full persona is built with `persona/` in step four; the kernel says "You are Valor." until then. (docs/persona.md)
- Codex and other agent CLIs wait until the gateway supports their providers. (docs/harnesses.md)
- Each session records the `claude` CLI version so replays months apart compare. (docs/tech-stack.md)

Measurements still to take (no decision needed): RAM use on the Air, a
headless browser under the sandbox, resume versus fresh sessions, Telegram
duplicate-send behaviour, and the email provider's sent folder.
(docs/machine.md, docs/harnesses.md, docs/bridges/*.md)
