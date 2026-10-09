---
tracking: none
slug: c8-followups
type: plan
status: merged
critique_rounds: 0
review_rounds: 0
---

# Six follow-up bugs

Bug fixes at stakes 1; none adds a check, gate, hook or guard.

1. **A suite that collects no tests passes.**
   Cause: `checks.compare` read a head report with no test in it as nothing
   failing. Change: such a head is a failure, `checks.NO_TESTS`. Test:
   two cases in `test_compare`. Doc: `docs/sdlc-checks-test.md`.
2. **The email bridge's launchd job has no operator settings.**
   Cause: its `PLIST_ENV` lacked `VALOR_OPERATOR_CHAT`, `_CHANNEL` and
   `_TELEGRAM_ID`. Change: the three are added. Test: the email job's
   environment holds every `VALOR_OPERATOR_` value the kernel's job holds.
3. **`no context window for gpt-6.1` at plan: dropped, not a bug.**
   The head still raises it for `gpt-6.1`. The OpenAI price table holds
   `gpt-6.1-sol`, and a price matches only the exact id or the id with a
   dated suffix, because OpenAI ships pricier variants under a base name
   (`core/spending.openai_prices`, `docs/metered-spending.md`). The id was
   mistyped; the table and error are as documented.
4. **The repository's suite fails in the verification VM.**
   Email tests built `EmailBridge(cfg)` without the test database, so the
   bridge connected to `valor_rebuild`; they now pass the `dsn` fixture. The
   governance-paragraph test lists files by walking the tree when
   `git ls-files` fails. The default-model test builds its sandboxed
   workspace turn only on macOS (`macos` mark). Host behavior is unchanged.
5. **`container.checked` reports a down runtime as a missing image.**
   Change: when the image is not held and the runtime is not running, it
   raises `Failed` (kernel) "the container runtime is down", and the
   recorded digest stays; a missing image still returns None. Test:
   `test_a_runtime_that_is_down_is_told_apart_from_a_missing_image`.
6. **The popoto spec's suite fails collection on `tests/postgres`.**
   The spec lives outside the repository; its draft in `cutover-data.md`
   and the live copy under the build notes add `--ignore=tests/postgres`.
