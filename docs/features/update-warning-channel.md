# /update Warning Channel

How `/update` warnings are produced, detected, capped, suppressed, and retrieved (#2845). This is the contract between `scripts/update/run.py` (the producer) and its two consumers, `bridge/update.py` and `tests/unit/test_update_cron_summary.py`.

## The problem this fixes

Four independent defects conspired so a real warning from `/update` reliably went unseen:

1. **`_queue_fix_session` truncated the fix-session brief at `stdout[:500]`.** The npm/git-pull preamble ate most of that budget, so the warning list — the entire reason the session exists — was cut mid-word.
2. **`has_warnings` couldn't see the cron summary.** `run.py --cron` renders `up to date at <sha> (N warnings)` plus `  ⚠️ <text>` bullets, or `update failed at <sha>` plus `  - <text>` bullets. The old detector matched only four legacy line prefixes (`"[update] WARN"`, `"WARNING:"`, `"ERROR"`, `"RESTART FAILED"`) — none of which match either cron form.
3. **`env-completeness` flooded operators with noise dominated by keys with in-code defaults**, and its documented remediation (copy the declaration into the shared vault) broke `Settings()` construction fleet-wide for empty-valued declarations.
4. **Human-gated warnings (`gws auth`, Redis ACL drift) re-emitted every 30-minute cycle forever**, with no way to see what was suppressed once a warning finally stopped repeating.

## The warning grammar `run.py` emits

`scripts/update/run.py:main()`'s `--cron` summary is mutually exclusive:

| Form | Shape |
|------|-------|
| Success, no warnings | `update successful` |
| Success, warnings | `{updated to \| up to date at} {sha} ({N} warning{s})` followed by one `  ⚠️ {warning}` line per entry |
| Failure | `update failed at {sha}` followed by one `  - {error}` line per entry, then (added by #2845) one `  ⚠️ {warning}` line per entry in `result.warnings` |

Three failure modes (`release verify FAILED`, `Worker not running after install and kickstart retry`, `Worker install failed`) append only to `result.warnings`, never `result.errors` — the failure-branch `⚠️` render exists specifically so those modes still hand the fix session a non-empty warning list.

The non-cron `--verify` path additionally emits four legacy line-anchored prefixes verbatim: `"[update] WARN"`, `"WARNING:"`, `"ERROR"`, `"RESTART FAILED"`.

### One warning is one line, by construction

`_append_warning(result, text)` and `_append_error(result, text)` collapse embedded newlines (`" ".join(text.split("\n"))`) before appending. Every one of the 85 warning/error-injection sites in `run.py` — 82 `result.warnings.append`, 2 `result.errors.append`, and the `readme_check` `result.warnings.extend` (converted to a per-iteration `_append_warning`, which also fixed an N² duplication bug: the `extend` sat inside its own iteration loop) — routes through one of these two helpers. Without this, a multi-line producer (an exception `str()`, the nine-line README-fix example block) would render its `⚠️`/`-` sentinel on only the first physical line and drop the rest — the same truncation this plan exists to fix, reproduced through a different producer.

## The detection contract: `extract_update_warnings`

`bridge/update.py::extract_update_warnings(status_lines: list[str]) -> list[str]` is the single parser both lossy hops (detection and the fix-session payload) share. It recognises every form above, plus a cross-check: when a `(N warnings)` summary line was seen, it compares `N` against the number of parsed `⚠️` bullets (excluding legacy-prefix lines and failure-block `-` bullets from the denominator) and appends a synthetic entry on mismatch:

```
[update] WARN: summary declared {N} warning(s) but {M} were parsed
```

`has_warnings` is `bool(extract_update_warnings(status_lines))`.

## The fix-session payload

`_queue_fix_session(event, machine, stdout, stderr, warnings, *, failed)` leads the brief with the complete, un-truncated warning list. The stdout/stderr tail follows under a raised cap (a few thousand characters), cut on whole-line boundaries with an explicit `[... N characters elided ...]` marker — a silent cut is what produced the original defect; a marked cut is recoverable. The log file path is parsed from the producer's own `<<FILE:<path>>>` marker rather than a hardcoded `data/update.txt` guess.

`failed` is passed by keyword at the call site, so the parameter order can change again without silently breaking `tests/unit/test_bridge_update.py`'s positional assertion.

## Suppression: `warn_state`

Three checks are genuinely unresolvable without a human action outside `/update`'s control: `gws auth` (browser OAuth consent), Redis ACL drift (a human-signed apply runbook), and `env-completeness` (a human editing the vault). `scripts/update/warn_state.py` collapses each to one emission per state transition:

- **`should_emit(key, signature, project_dir) -> bool`** — `True` iff `signature` differs from the last-stored value for `key`. An empty `signature` means "resolved": the stored entry clears and the transition itself emits once.
- Signatures encode content, not just presence, so a *changed* drift re-warns: the Redis ACL signature is a digest of the planned commands (which already carry the `<REDIS_APP_PASSWORD>` placeholder, never a real secret); the gws signature is the auth-method string; `env-completeness`'s signature is `f"unresolved:{tool.error}"` — the capped summary string, so any change to the first five keys or the missing count re-warns. The uncapped enumeration lives at `python -m scripts.update.verify`.
- `env-completeness`'s resolution branch is gated on the check having actually passed (`not (tool.version or "").startswith("skipped")`) — a transient vault-symlink outage must never clear the stored signature, or the next healthy run re-emits the whole report.

## Retrieval: the suppressed conditions stay discoverable

Suppression without retrieval is how a real condition goes dark. Four surfaces answer "what is currently suppressed?":

1. **`warn_state.active(project_dir) -> dict[str, str]`** — the currently-suppressed key → signature map, fail-soft (`{}` on any error).
2. **`python -m scripts.update.warn_state`** — prints the state file path it read (first line, always — this is what makes a wrong-root call visibly wrong rather than a silent empty map), then every suppressed key and its signature.
3. **`python -m tools.doctor`** (full run, not `--quick`) — a `gws` result (`_check_gws_auth`, mirroring `_check_redis_acl`) alongside the existing `redis_acl` result. `gws` is registered inside the `if not quick:` block, not the unconditional list, because this repo has no WARN tier (`CheckResult.passed` is binary) — `passed=False` plus exclusion from `--quick` *is* the WARN idiom, and `--quick` backs the opt-in pre-push hook that must never block an unauthenticated machine's pushes on a condition only a human at a browser can clear. `env-completeness` has no doctor counterpart by design — `python -m scripts.update.verify` already prints its full report on demand.
4. **The `suppressed:` trailer** — an always-on line, emitted whenever `warn_state.active()` minus this run's own emissions (`result.warn_keys_emitted`) is non-empty:

   ```
   suppressed (unchanged since first warning): gws-auth, redis-acl-drift — details: python -m scripts.update.warn_state
   ```

   The **emitted-subtracted** map matters: `should_emit` writes its signature the instant it returns `True`, so a trailer built from raw `active()` would call a key "unchanged since first warning" on the very run it first warned. `result.warn_keys_emitted` records every `should_emit` site that returned `True` this run (all six: `gws-auth`, `redis-acl-drift`, `env-completeness`'s two branches, and `calendar-config`'s two branches), and the trailer names `active() - warn_keys_emitted`.

   The trailer is composed at the summary-render site in `run.py`, **outside** the success/failure `if/elif/else` — the modal suppressed case is a run with nothing else wrong (the `else` branch), so composing it inside `elif result.warnings:` would make it silently disappear on exactly the run this exists to cover.

   It is emitted to **two sinks**, both at that one composition point: `log(trailer, always=True)` reaches `_log_buffer` → `data/update.txt`, and `status += "\n" + trailer` reaches stdout → `status_lines` → the Telegram reply. `log()` prepends `"[update] "`, so the `data/update.txt` copy carries that five-byte offset in front of the constant below; the bare form is what `bridge/update.py` matches, since it reads stdout-derived `status_lines`.

   `data/update.txt`'s write gate widens from `if not result.success or result.warnings:` to `... or suppressed:` (the emitted-subtracted map) — otherwise the file is absent on exactly the clean-except-suppressed run this exists to cover.

The trailer's leading token is `SUPPRESSED_PREFIX`, a constant defined once in `scripts/update/warn_state.py` and imported by both `run.py` (the composer) and `bridge/update.py` (the reader/forwarder) — an independently-spelled prefix on each side is the exact producer/consumer drift that produced Defect 2, and it must not be reintroduced here. It deliberately carries neither `⚠️` nor any of the four legacy prefixes, so `extract_update_warnings` is inert to it — tested directly, including immediately after a real `(N warnings)` block, so the parser is proven inert while already in a matching state.

## Where the trailer reaches a reader — and where it cannot

| Path | Trigger | Reader at emission time | Surface after #2845 |
|------|---------|--------------------------|----------------------|
| Interactive | A human types `/update` in Telegram | A human, already watching for the reply | **Push.** `bridge/update.py::handle_update_command` forwards the trailer from `status_lines` onto the Telegram reply, unconditionally — no warning need be present. |
| Unattended cron | `com.valor.update`, every 30 min | None — `scripts/remote-update.sh` runs directly with stdout to `logs/update.log`; `bridge/update.py` is never entered, and there is no chat context (`UPDATE_REPORT_CHAT_ID` is set only by `bridge/update.py`) | **Pull.** The trailer lands in `logs/update.log` (stdout) and, via `log()` and the widened write gate, in `data/update.txt`. `python -m scripts.update.warn_state` and `python -m tools.doctor` answer on demand. |

Pull-only on the unattended path is deliberate, not a gap: pushing there would need a hardcoded chat id in a script that runs on every fleet machine, or a fix session queued 48×/day for a condition this plan defines as unresolvable-without-a-human — the exact respam this whole feature exists to end, re-spent as agent time.

## Tests

- `tests/unit/test_update_warning_extraction.py` — `extract_update_warnings` (all forms, the count cross-check, adversarial green transcripts) and `_queue_fix_session` (legibility, empty inputs, exception swallowing).
- `tests/unit/test_bridge_update.py` — the #1898 regression, the `failed=` keyword-argument change, and Task 5's trailer-forwarding tests (`test_suppressed_trailer_reaches_telegram_on_clean_run`, `test_suppressed_prefix_constant_is_shared`, `test_suppressed_trailer_extracts_as_zero_warnings`).
- `tests/unit/test_update_cron_summary.py` — the failure-branch warnings render and the trailer's composition/write-gate behavior.
- `tests/unit/test_update_warn_state.py` — `should_emit`/`active`/`_main` and the `SUPPRESSED_PREFIX` constant.
- `tests/unit/test_update_append_warning.py` — the newline-collapse helpers and the README N² dedup fix.
- `tests/unit/test_doctor.py` — `_check_gws_auth`'s four branches and its `--quick` absence.

See also [`env-completeness-validation.md`](env-completeness-validation.md) for the required/optional split and the reader-recurrence guard, and [`redis-flush-hardening.md`](redis-flush-hardening.md) for the ACL drift check itself.
