---
tracking: none
slug: c4-small-fixes
type: plan
status: planned
critique_rounds: 0
review_rounds: 1
---

# Three small fixes

Bug fixes; none adds a check, gate, hook or guard.

1. **Hard-coded dead port in the gateway meter test.**
   Cause: `tests/test_gateway_meter.py` points the upstream at port 6561 and
   assumes nothing listens there; another run may. Change: bind port 0, read
   the port, close the socket, use that port. Test: the existing
   `test_an_unreachable_upstream_is_a_502_naming_it` is the test.
2. **Email bridge log lines have no timestamps.**
   Cause: `bridges/email/__main__.py` never calls `logging.basicConfig`, unlike
   the telegram and local bridges. Change: the `run` verb configures
   `level=INFO` with `%(asctime)s %(levelname)s %(message)s`, the same line the
   other two use. Test: `test_run_logs_with_timestamps` in
   `tests/test_email_bridge.py`.
3. **`docs/harnesses.md` "Denied entirely" omits `performing_dir`.**
   Cause: `core/workspace.py` `kernel_paths` denies it, the doc does not list
   it. Change: name it in the list. Test: documentation only.
