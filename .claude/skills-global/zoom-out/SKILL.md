---
name: zoom-out
description: "Use when course-correcting mid-session or reassessing priorities. Triggered by 'zoom out', 'am I solving the right problem', 'step back', 'reassess', 'am I on track', or any request to check whether current work aligns with real goals."
allowed-tools: Read, Bash
---

# Skill: /zoom-out

## Purpose
Pull back from the current task, reassess priorities, and reorient toward the actual goal — before more time is spent solving the wrong problem.

## When to Use
- You are on a third consecutive patch loop on the same issue and it is still not fixed
- The current sprint has drifted from the original goal
- A session has gone quiet or circular — same fixes, same failures
- The user says "step back", "zoom out", or "are we doing the right thing?"
- Before starting a fourth implementation attempt on something that keeps breaking

Concrete trigger example: Run before a third consecutive patch loop on the same issue. If /do-patch has been invoked twice and the tests still fail, invoke /zoom-out before a third attempt.

## Steps

1. **Synthesize memory for recent context.** Search for observations related to the current work area:
   ```bash
   python -m tools.memory_search search "<recent keywords from current session>"
   ```
   Read the top 5 results. Note any corrections or pattern observations that apply.

2. **Check open GitHub issues.** Get a snapshot of active work:
   ```bash
   gh issue list --state open --limit 10
   ```
   Note which issues are in progress, which are blocked, and which have been open the longest.

3. **Read recent session context.** If a plan doc or PROGRESS.md is available at the worktree root, read it. Check the last 10 git log entries:
   ```bash
   git log --oneline -10
   ```

4. **Produce a strategic summary.** Write a concise assessment covering:
   - **What we set out to do** (original goal)
   - **What we've actually done** (last 5 actions/commits)
   - **Where we are stuck** (current blocker, if any)
   - **Recommended next focus** (highest-leverage action)
   - **Deprioritization list** (2–3 things that can wait)

5. **Deliver the summary.** Print it in the session. If the user is remote, optionally send via Telegram:
   ```bash
   valor-telegram send --chat "Dev: Valor" "<summary>"
   ```

6. **Ask one question.** Close with: "Does this match your mental model, or is there something I'm missing?"

## Output
A strategic summary with recommended next focus and deprioritization list. One follow-up question.

## Anti-Patterns
- Do not use /zoom-out as a stalling tactic — if you know what to do next, do it.
- Do not run /zoom-out on every session — it is for course correction, not routine check-ins.
- Do not produce a long report — the summary should fit in a Telegram message (< 300 words).
- Do not skip the "recommended next focus" — a zoom-out without a recommendation is just a status report.
- Do not re-read all files from scratch — use memory search and git log to reconstruct context efficiently.
