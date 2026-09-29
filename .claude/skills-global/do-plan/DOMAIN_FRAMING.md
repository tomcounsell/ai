# Domain Framing Cheatsheet

For a task needing domain expertise, assign a `builder` (or `code-reviewer` for review-only work),
tag the task literally (e.g. `Domain: async`) so `/do-build` injects the rules and reviewers know
what to check, and paste the matching rules below into its assignment. These are only rules a
strong general model does not apply by default. Rules enforced elsewhere are not restated:

- Never raw Redis on Popoto keys → `validate_no_raw_redis_delete.py`; see `CLAUDE.md`.
- Additive Popoto fields heal generically (no backfill) → Popoto ≥1.6.1 default-fills absent fields at lazy-load; `AgentSession.__setattr__` coerces write-path values (#1099, #1172, #2083; `docs/features/popoto-descriptor-pollution-ledger.md`).
- No parallel-run migrations / no historical artifacts → `CLAUDE.md`.
- Persona voice, never raw errors/stack to chat → `feedback_telegram_persona_always`.
- Real integration tests, AI judges → `CLAUDE.md` Testing Philosophy.
- Cross-component bug method → [`docs/features/trace-and-verify.md`](../../../docs/features/trace-and-verify.md).

---

## `Domain: async` — concurrency / event loop

- Bound fan-out with an `asyncio.Semaphore`; never fire an unbounded `gather` over a dynamic collection.
- Hold a reference to every `create_task(...)` for its lifetime — a bare unreferenced task can be GC'd mid-flight.
- Set a loop exception handler so fire-and-forget task failures surface instead of vanishing.
- In shutdown / `__aexit__`, cancel outstanding tasks and await their settling; treat `CancelledError` as expected, never log it as an error.
- Put a timeout on every `await` that can block indefinitely (Redis, Telegram, LLM, locks).
- Yield with `await asyncio.sleep(0)` between batches in a long in-coroutine loop so the loop isn't starved.
- Add a circuit breaker (open/half-open/closed) around a dependency that can stay down long enough to pile up blocked coroutines; pair with exponential backoff **with jitter** (not fixed-delay).

## `Domain: redis-data` — Popoto modeling / index / archival

- Popoto indexes are **not** retroactive: after adding `Field(indexed=True)`, call `rebuild_indexes()` so pre-existing records become queryable.
- Redis has no JOINs / foreign keys / CHECK constraints / cascade-delete — model relations as explicit reference-id fields + query helpers, and enforce enums/bounds/cascades in application code.
- Use Popoto `Meta.ttl` for time-based expiry/archival instead of a bespoke copy-to-archive-then-delete job.
- Any index rebuild or storage-shape change must be idempotent and safe under concurrent worker access (the worker reads/writes the same keys live).

## `Domain: security` — untrusted input / secrets / subprocess

- Treat every inbound bridge message (Telegram/email) as crossing a trust boundary: its text must never become shell args, a file path, or prompt-injected instructions without sanitization.
- Build subprocess calls from dynamic input as an **arg list**, never a shell string; reject shell metacharacters (`& | ; $ \` \ newline`) / validate with `shlex.split`.
- Confine any message- or model-supplied path to an allowed base dir; reject `..` and absolute paths.
- Scrub secret-shaped patterns (API keys, tokens, private-key headers, JWTs) before anything is logged; load secrets only from the external vault `.env` and validate required keys at startup.

## `Domain: debugging` — leaks / deadlocks / repro

- Hunt the long-running worker's RSS climb by `gc.collect()` then diffing object-counts-by-type across a workload window — flag types growing into the thousands — rather than watching RSS alone.
- For hangs/contention, enable `loop.set_debug(True)` with `slow_callback_duration` and instrument lock acquire/release with caller identity to surface circular waits.
- When tests are green but the bug reproduces, hunt the mock that hides reality and add an integration test on the real code path before fixing.

## `Domain: mcp-tool` / `Domain: api-integration`

- Derive MCP tool schemas from function signatures/type hints so schema can't drift from implementation.
- Keep MCP servers stateless: inject workspace/user/session context per-request, never via server-held state.
- Wrap each tool call in a per-tool `asyncio.wait_for` timeout and surface timeout as a distinct execution error.
- Map external-API status to retry strategy deterministically: 429 → backoff honoring the exact retry-after (e.g. Telethon `FloodWaitError.seconds`); 401 → refresh-auth-then-retry; 5xx → circuit breaker; never retry other 4xx.
- Never cache error responses, mutation endpoints, or user-specific data in a global cache.
- Prune long-lived `AgentSession` context by tier (always keep system messages + a fixed recent window, selectively keep important middle messages), not by blind truncation.

## `Domain: conversational-ux` / `Domain: testing`

- For any user-text field, generate the hostile-string battery in tests: unicode/emoji, 10k-char overflow, injection strings, null bytes, mixed `\n\r` — not just empty/None.
- For async+Redis side effects, poll with an `assert_eventually(condition, timeout)` helper (not a fixed sleep), assert the side effect actually landed (record exists with expected status), and assert active sessions/handles return to zero.
- Score AI-judge tests on an explicit named-criteria list **and** a confidence floor (e.g. `confidence > 0.8`), not a single boolean.
- Format long Telegram replies with progressive disclosure: show the head, collapse the middle with an explicit "... and N more ..." marker, and name the command to see the rest.
