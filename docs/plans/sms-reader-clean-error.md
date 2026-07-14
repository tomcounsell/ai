# sms_reader CLI: clean error instead of raw traceback

Issue: #2073
Branch: session/dev-6434747e
Labels: bug, skills

## Problem

On a machine without Full Disk Access to the Messages DB (or without a Messages
DB at all), `/update`'s verifier runs `python -m tools.sms_reader.cli recent
--limit 1`. The CLI's `main()` does not catch `SMSReaderError`, so the process
dies with an unhandled traceback on stderr. The `/update` verifier surfaces
stderr verbatim as the failure reason, producing the warning:

```
⚠️ sms_reader: Traceback (most recent call last)
```

This is useless (names no cause, no fix) and — being multi-line — buries the
warnings printed after it in the update summary (the reason "Valor the Pirate"'s
3-warning run only showed 2 lines when pasted).

## Freshness Check

- `tools/sms_reader/cli.py` — current `main()` dispatches directly, no error trap.
- `scripts/update/verify.py::check_valor_tools` (lines ~283-307) — runs the CLI,
  stores `result.stderr.strip()` as the ToolCheck error.
- `scripts/update/run.py` (lines ~1805-1808) — appends `{name}: {error}` per
  unavailable valor tool to `result.warnings`.
- `tools/sms_reader/__init__.py::SMSReaderError` — already carries a clean,
  actionable `.message` (e.g. "Grant Full Disk Access…").

## Prior Art

`SMSReaderError` is raised throughout `tools/sms_reader/__init__.py` with
category-tagged, human-readable messages. The fix simply surfaces that existing
message cleanly instead of letting it escape as a traceback.

## Data Flow

`/update` → `verify.check_valor_tools` → subprocess `sms_reader.cli recent` →
`get_recent_messages` → `_get_db_connection` raises `SMSReaderError` → (today)
unhandled traceback on stderr → verifier stores it → run.py appends it as a
warning. The fix inserts a `try/except SMSReaderError` at the `main()` boundary
that converts the exception into `sms: <message>` + exit 1.

## Appetite

Small — a single-file bug fix plus regression tests. Under an hour.

## Solution

### Key Elements

- Split `main()` into `_build_parser()` / `_dispatch(args)` / `main()`.
- `main()` wraps `_dispatch()` in `try/except SMSReaderError`, printing
  `sms: {exc.message}` to stderr and exiting 1.

### Technical Approach

No behavior change on the success path (JSON still printed, exit 0). On any
`SMSReaderError` from any subcommand, stderr becomes a single actionable line and
the exit code stays 1, so the `/update` warning reads the message verbatim.

## Failure Path Test Strategy

### Exception Handling Coverage
- `SMSReaderError` from `get_recent_messages` → clean `sms: …` stderr, exit 1,
  no traceback, no stdout.
- `SMSReaderError` from `list_senders` → same wrapper covers all subcommands.

### Empty/Invalid Input Handling
- Success path with empty result list still prints `[]` and does not exit
  non-zero (wrapper does not swallow normal output).

### Error State Rendering
- Assert `captured.err` equals the exact `sms: <message>` string and contains no
  "Traceback".

## Test Impact
No existing tests affected — `tools/sms_reader/cli.py` had no prior test module;
this adds `tests/unit/test_sms_reader_cli.py` as greenfield coverage.

## Rabbit Holes

- Do NOT add a DB-path env override or attempt to auto-grant Full Disk Access —
  granting FDA is a per-machine GUI action, out of scope for this code fix.
- Do NOT rework the `/update` verifier's multi-line handling; a clean one-line
  error from the CLI resolves the burying symptom at the source.

## No-Gos (Out of Scope)

- The stale `GRANITE_DELIVERY_TIMEOUT_S` process-env warning (separate,
  machine-local; self-heals on the next worker restart — config already correct).
- The `env-completeness: 20 missing` warning (pre-existing; those keys are
  optional/defaulted knobs plus an intentionally-absent `ANTHROPIC_API_KEY`).

## Update System

No update-script or update-skill changes required. This fix improves the text of
an existing `/update` verifier warning; the verifier wiring is unchanged.

## Agent Integration

No new CLI entry point or bridge import required — `sms_reader.cli` is already an
existing entrypoint invoked via the agent's Bash tool and by the `/update`
verifier. This only changes its stderr on the error path.

## Documentation
- [ ] No new feature doc required — update the inline module docstring/comment in
  `tools/sms_reader/cli.py` explaining why the error is trapped (done in the fix).
  The `reading-sms-messages` skill and existing behavior are otherwise unchanged;
  no `docs/features/*.md` covers the CLI's error text, so no external doc drifts.

## Success Criteria

- `python -m tools.sms_reader.cli recent --limit 1` on an FDA-less machine prints
  a single `sms: …` line to stderr, exit 1, no traceback.
- `tests/unit/test_sms_reader_cli.py` passes.
- Success path unchanged (JSON + exit 0).

## Step by Step Tasks

### 1. Trap SMSReaderError in the CLI
Refactor `main()` into `_build_parser` / `_dispatch` / `main`, wrap dispatch in
`try/except SMSReaderError`.

### 2. Add regression tests
`tests/unit/test_sms_reader_cli.py`: error-path (recent, senders) and success
path, asserting no traceback and exact stderr.

### 3. Final Validation
Ruff clean; new tests green; manual CLI run shows clean error.

## Verification

- [ ] `python -m ruff check tools/sms_reader/cli.py tests/unit/test_sms_reader_cli.py` passes.
- [ ] `pytest tests/unit/test_sms_reader_cli.py -q -n0` passes (3 tests).
- [ ] Manual: `python -m tools.sms_reader.cli recent --limit 1` emits `sms: …`
      (no `Traceback`) on a machine without Full Disk Access.

## Knowledge Base

- Memory (project `valor`): no prior memory on sms_reader CLI error handling;
  this plan is the record.
- Vault: no vault entry touches sms_reader; none needed.

## Open Questions

None.
