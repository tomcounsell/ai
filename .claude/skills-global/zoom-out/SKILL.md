---
name: zoom-out
description: "Course-correct mid-session and reassess priorities. Triggered by 'zoom out', 'step back', 'reassess', 'am I on track', 'am I solving the right problem'."
allowed-tools: Read, Bash
---

# Skill: /zoom-out

If `.claude/skill-context/zoom-out.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

## Purpose
Step back from the current task, check it against the actual goal, and reorient before
more time goes into solving the wrong problem.

## When to Use
- A third consecutive patch loop on the same issue, or before a fourth attempt at something that keeps breaking
- The work has drifted from the original goal, or the session has gone circular
- The user says "step back", "zoom out", or "are we doing the right thing?"

## Steps
1. Reconstruct context cheaply: the repo context file's memory/recall tool if it declares one; otherwise `git log --oneline -10`, any plan doc or PROGRESS.md at the worktree root, and `gh issue list --state open --limit 10` for what is in progress, blocked, or longest open.
2. Write the summary (see Output) and print it in-session. If the user is remote and the context file declares a messaging tool, you may also send it there.
3. Close with one question: "Does this match your mental model, or is there something I'm missing?"

## Output
Under 300 words: what we set out to do, what we've actually done (last few commits or
actions), where we're stuck, the recommended next focus (required: without it this is
just a status report), and 2-3 things that can wait.

## Anti-Patterns
- Using zoom-out to stall when the next step is already clear: do it instead.
- Running it as a routine check-in rather than for course correction.
