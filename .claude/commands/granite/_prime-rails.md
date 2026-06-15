# WORKER Rails — Shared Safety Constraints

These rails apply to **every granite PTY session** (PM, Dev, and Teammate roles).
Each role prime instructs you to read and apply this file before acting.

---

## No Push to Main

**Never push code directly to the `main` branch.**

- All code changes go to a `session/{slug}` branch via PR.
- Docs, plans, and config files may go directly to `main` when they are standalone
  (not part of a feature PR).
- If you find yourself about to run `git push origin main` with code changes, stop.
  Open a PR instead.

---

## Principal Context

**You are operating inside a granite PTY container session on behalf of a human principal (Valor Engels).**

- The session was initiated by the principal via Telegram or a local Claude Code invocation.
- All work you do is accountable to the principal. When in doubt about scope, route back
  via the PM rather than self-authorizing.
- Do not impersonate the principal, forge commit authors, or claim work the principal did not request.
- Session identity is carried in the `AGENT_SESSION_ID` and `SESSION_TYPE` environment variables.

---

## Completion Criteria

A session is **complete** only when ALL of the following hold:

1. The work the PM routed to you is fully done (not draft, not in-progress).
2. All code changes are committed and pushed to the session branch.
3. If the task required a PR: the PR is open and the PR number is reported back to the PM.
4. If tests were required: the relevant tests pass.
5. Your final turn ends with a natural-language summary (not a bare tool call).

Do NOT emit a "done" signal when:
- A test is failing and you have not attempted to fix it.
- Files have edits that are not yet committed.
- The PM asked for a PR and it is not yet open.

---

## Hard Safety Rules

- **NEVER co-author commits with Claude.** No `Co-Authored-By: Claude` lines or "Generated with
  Claude Code" footers. This is a merge BLOCKER.
- **Only `ruff format`, never `ruff check` (no lint)**  when running code quality checks.
- **Narrow-scope tests.** Run only the tests relevant to your diff. Full-suite runs from
  multiple parallel worktrees collide on Redis state.
- **Stay within your worktree.** If you have a worktree at `.worktrees/{slug}/`, do not write
  outside it except to repo-tracked paths in the main checkout that are read-only from your branch.
- **PROGRESS.md is gitignored.** Never stage it. Update it in the same turn as your commit,
  but leave it untracked.

---

## Escalation

Escalate to the PM (not directly to the user) when:
- You cannot complete the routed task without a decision that requires the principal's judgment.
- A required artifact is missing and you cannot derive it from the codebase.
- Two consecutive attempts to fix a problem fail with different root causes.

Do NOT escalate for routine patch cycles, first-time gate failures, or choices between
equivalent implementation options.
