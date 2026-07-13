# Harness Cross-Compatibility — Infrastructure

## Current State

- Every bridge-connected top-level session runs through the local `claude -p`
  harness with subscription authentication.
- `agent/session_runner/harness/` provides a normalized adapter protocol, but
  Claude is the only concrete implementation on current main.
- Eng-session developer work runs as a resumable Claude subagent inside the
  top-level PM turn. Its continuation id and cwd are persisted on AgentSession.
- Codex CLI installation and authentication are currently machine-local and are
  not managed by `/update`. The primary development machine has
  `codex-cli 0.144.3` and a saved ChatGPT login.

## New Requirements

- `@openai/codex` CLI version `0.144.3` or newer on machines that explicitly opt
  into the Codex dev lane.
- Authentication via saved Codex CLI login, with `CODEX_API_KEY` accepted only
  as a single-`codex exec` fallback environment value. Authentication files and
  tokens must never be logged, committed, or copied into AgentSession.
- Typed settings for install opt-in, minimum version, model override, sandbox,
  turn timeout, and maximum resumed turns.
- Four nullable AgentSession fields: dev harness selection, Codex thread id,
  Codex version, and Codex turn count. They require an idempotent registered
  read-compatibility migration but no index rebuild/backfill.
- A session-scoped stdio MCP server exposed only to explicitly flagged eng PMs.
  It invokes `codex exec` in the same worktree and process group as the PM.

## Rules & Constraints

- Top-level PM and teammate sessions remain Claude. Codex is a dev-lane-only
  subprocess and must never be selected through `HeadlessRoleDriver`.
- Selection is manual at session creation and immutable after work begins.
- Default Codex sandbox is `workspace-write`; `danger-full-access` requires an
  explicit machine setting and cannot be enabled by the session creation flag.
- Approval policy is `never` for headless turns. Prompts are passed on stdin and
  subprocesses are constructed from argv lists, never shell strings.
- Never pass `--ephemeral` because the thread must be resumable. Repeat explicit
  cwd, sandbox, and approval globals for resume invocations.
- Persist `thread.started.thread_id` synchronously before later event parsing so
  a crash cannot lose an already-created thread.
- Serialize turns per AgentSession. Do not run two `exec resume` processes
  against the same thread.
- Enforce the configured resume-turn bound before spawn. On exhaustion, keep the
  old thread and surface an actionable error; do not silently roll over.
- The update module is disabled by default and nonfatal for machines that do not
  opt in. A flagged session itself fails fast on missing binary/version/auth.
- Telemetry may record harness, usage, version, lifecycle, and bounded error
  categories. It must not record prompts, tokens, auth files, or raw unbounded
  stderr.

## Rollback Plan

1. Stop creating sessions with `--dev-harness codex` and disable the Codex
   install opt-in in machine settings.
2. Allow already-running flagged sessions to finish or explicitly cancel them;
   do not switch a live thread to Claude because resume handles are not portable.
3. Revert conditional MCP config/prime wiring. Default Claude sessions require
   no data migration and remain unchanged.
4. Leave nullable Codex fields and completed migration records in place during
   rollback; dormant fields are harmless and preserve forensic continuity.
5. If necessary, uninstall the global Codex CLI after all flagged sessions are
   terminal. Saved authentication must be removed only through the Codex logout
   workflow by an authorized human.
