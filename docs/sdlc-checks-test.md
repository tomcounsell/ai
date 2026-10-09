# checks.test: the suite, then breadth

The `checks.test` stage of the [SDLC state machine](sdlc-state-machine.md).

**What runs.** First one judgement call (use shape 8 in
[judgement-layer.md](judgement-layer.md)) over the diff and the tests it
changed: three yes/no questions, one per gap kind (records in states other
than the obvious one, a member of an enumeration the code branches on,
existing tests whose bounds encode the old behavior); the kernel lists each
kind at caution as a behavior and refuses a caller's. Both legs failing
leaves no verdict and runs no suite; after two such runs it is a behavior.
While breadth has no passing calibration record, what it lists is
information shown in the delivery, never a behavior. Then the project's own
suite command, deterministic, at the task's base commit and then at the
candidate's head, each in a blind checkout from the kernel mirror with its
own copy of the caches and fresh Postgres and Redis on the task's ports
(`core/checks.py`). A base run whose setup failed is never reused. No
setup command or suite has a time limit; a stop ends a running one.
A candidate whose tree holds a `.valor` entry has no checkout: the run is
recorded as the commit's own fault, red at head, and never rerun (the base's
tree is never checked out, so its entries do not count). A JUnit report that
is not XML, or declares an encoding the parser cannot read, gives no
per-test result. A test with parameters counts as gone when the diff touches
what feeds them: its `parametrize` decorators, `pytestmark`, the `params=` of
fixtures it requests (also in a `conftest.py`), `pytest_generate_tests`, the
bindings those use (an `if` holding one counts whole), files they name.

**Exit evidence.** `test.decided`: the candidate, the command, the failures
at head that do not fail at base, `deleted_at_head` (tests that passed at
base and are gone at head), `failing_at_base` (shown, never counted), the
behaviors, the breadth judgement (id, actions, model, cost, guard id, and
`information` while uncalibrated), and the verdict the kernel computes: any
failure `red`, else any behavior `gaps`, else `pass`. Per-test results at
base and none at head is `red`. A head whose report has no passed, failed or skipped test (the
suite collected nothing, pytest exits 5, or collection errored, exit 2) is
`red` too, never `pass`.

**Why.** Mission item 1 ("testing actual use"). On popoto #633 the clarify
arm broke a bound in an existing test and was accepted anyway
(rebuild-baseline.md, Test breadth).

**Guard record for breadth.** Granted 2026-10-01, expiring ninety days
from it; Mission item 1. Every replay wrote fewer tests than its reference:
#872 missed the archived-team guards, #191 the list key name and hash
exclusion, and the demonstration wrote 10 tests against the reference's 35
(rebuild-baseline.md, Test breadth; rebuild-demonstration.md).
