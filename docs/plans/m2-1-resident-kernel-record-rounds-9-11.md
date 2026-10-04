# 2.1 record, patch rounds 9 to 11

Continues [m2-1-resident-kernel-record.md](m2-1-resident-kernel-record.md).

## Patch round 9

Scope: a turn is never left uncollected because of a NUL character in an
action, as the lead decided (the gap of the test of round 8, and the NUL
the test of round 6 noted).

1. A path holding a NUL character names no file, and `os.open` raised
   `ValueError` on it out of `open_plain_file`, so `broker.request` raised
   for a send naming one. `workspace._parts` now takes such a path as not a
   plain relative path, so every descriptor walk answers it with a reason,
   and a send's file gets the one file answer, "not a regular file in the
   task's workspace". Test: `test_a_nul_in_a_file_path_gets_the_file_answer`
   (a NUL in the file name and in a directory component).
2. Postgres jsonb cannot store a NUL character. Every row an effect gets
   (`effect.refused`, `effect.held`, `effect.intent`) holds the payload
   whole, and `turn.collected` holds each request, so a request holding
   one failed when it was ledgered, whatever its type, and the turn was
   not collected; a send whose path holds one failed there too, after
   item 1's refusal. Rewriting the payload would break the rule that a row
   holds what its digest names and what Tom approves, so the request is
   answered where the turn's files are read: `signals._request` records a
   request holding a NUL anywhere (type, target, payload, keys included)
   as unreadable, "it holds a NUL character, which the ledger's JSON
   (Postgres jsonb) cannot store", without the request, as it records one
   that is not JSON. It never reaches the broker; the turn is collected
   and its next prompt names the file and the reason. Test:
   `test_a_nul_in_a_request_is_answered_and_the_turn_collected` on the
   test database, through `signals.collect` and `session.record` (which
   calls `broker.request`): a send with a NUL in a file path, an email
   with one in its subject, a request with one deep in its payload, and
   one in a target, each answered; a clean send beside them is held. It
   fails with `UntranslatableCharacter` without the change.
3. `docs/harnesses.md` says such a request is recorded with an error.

`broker.request` called directly with a payload holding a NUL still fails
at the ledger: its callers are the turn's requests, read by `signals`,
and the kernel's own merge. Nothing new limits, waits, or guards.

Suite: 1234 passed, 21 skipped (`valor_rebuild_test_2_1p9`, ports 6430-6439).
Ruff check and format check clean.

## Patch round 10

Scope: a turn is never left uncollected because of a request Postgres jsonb
cannot store, as the lead decided (R1 of the review of round 9).

1. `json.loads` accepts three things jsonb refuses: a `\u0000` escape (a
   NUL character), an unpaired surrogate escape such as `\ud800`, and
   `NaN`, `Infinity`, or a number too large for a float, which becomes
   infinite and is written back as `Infinity`. Each failed at
   `ledger.append`, so the turn was not collected. `signals._request` now
   records a request holding any of them anywhere (type, target, payload,
   keys included) as unreadable, "it holds a NUL character" (or "an
   unpaired surrogate", or "a NaN or infinite number"), "which the
   ledger's JSON (Postgres jsonb) cannot store". A surrogate pair is one
   character to jsonb and is stored. Checked against jsonb on this
   machine's Postgres 18: `"\u0000"`, `"\ud800"`, `"\udc00"`,
   `"\ude00\ud83d"`, `NaN` and `Infinity` are refused; `1e400` as text
   and a paired emoji are stored.
2. Test: `test_a_request_jsonb_cannot_store_is_answered_and_the_turn_collected`
   (round 9's NUL test, renamed and extended) on the test database,
   through `signals.collect` and `session.record`: round 9's four NUL
   requests, a lone high surrogate in a subject, a lone low surrogate as a
   key, a swapped pair, a NaN, a negative and a positive infinity, `1e999`, and a lone surrogate
   in an action type, a target, a nested value and a send's file path, each
   answered with its reason; a clean send holding a paired emoji is held
   with the emoji intact. Without the change it fails with
   `InvalidTextRepresentation` (the surrogate), and with the number check
   alone removed, on the `NaN` token.
3. The `effects` row in `docs/harnesses.md` and the signals module
   docstring name all three.

A NUL in `question.md`, `no_question.md`, `done.md` or `plan.json`, and an
unpaired surrogate or non-finite number in `plan.json`, are a later
follow-up. Nothing new limits, waits, or guards.

Suite: 1234 passed, 21 skipped (`valor_rebuild_test_2_1p10`, ports 6470-6479).
Ruff check and format check clean.

## Rebase onto 6e123f88f (lead)

Round 10 rebased onto 1.4c part one's merge. Two conflicts: `core/README.md`
(the review runner paragraph kept, with 2.1's service and reconcile
sentences), and `core/fresh.py`'s imports. The review runner that 1.4c part
one added runs suites and a session, so it now holds the turn slot
throughout, as the test and docs checks do; `test_a_check_holds_the_slot`
covers it and fails without the change. `docs/machine.md` and
`docs/architecture.md` name the review check.

## Patch round 11

Scope: a request file the turn wrote never raises out of `signals.collect`
or `session.record`; whatever it holds, it is answered (unreadable or
refused, with a reason) and the turn is collected, a clean request beside
it still held. The lead's decision on R1 to R3 of the review of round 10.

1. Storability is Postgres's to judge (R1). Round 10's recursive walk is
   gone; it raised `RecursionError` on a request nested past Python's
   recursion limit. `ledger.unstorable` asks Postgres, `SELECT %s::jsonb`
   of the value adapted as `append` adapts a payload, inside a savepoint,
   and answers a data exception or a program limit exceeded (SQLSTATE
   classes 22 and 54; the stack depth error is 54001, a sibling of
   `ProgramLimitExceeded` in psycopg, not a subclass) with the error's
   class and primary message; any other error is raised. A `RecursionError`
   from the adapter's own serialization is the same answer.
   `session.record` asks it of each request nested as `turn.collected`
   holds it (`{"effects": [entry]}`, three levels deeper than the request,
   the deepest row a request reaches), before the merge check and the
   broker, and records a refused one as "unreadable request: the ledger's
   JSON (Postgres jsonb) cannot store it: <class> (<message>)" without the
   request. No depth number of our own: Postgres's `max_stack_depth` and
   Python's parser decide. `signals._request` catches `RecursionError`
   from parsing (and from `str` of a deep action type) with the other
   parse errors, and `plan.json`, fresh and read again, does the same.
2. A surrogate code point is not text (R2, refused rather than joined).
   `json.loads` joins a properly escaped pair into one character, so a
   surrogate left after parsing is a lone escape, a swapped pair, or a
   pair split between an escape and raw bytes (CESU-8 included, which
   `json.loads(bytes)` decodes with `surrogatepass`). `signals._request`
   strict-encodes the request to UTF-8 (`json.dumps(found,
   ensure_ascii=False).encode("utf-8")`) and answers one that fails as
   "unreadable request: it holds a surrogate code point outside an escaped
   pair, which is not text". Refused rather than joined because those bytes
   were never valid UTF-8 or JSON text, and the broker and bridges then see
   only text; joining would accept a malformed file as if well formed.
3. A send's text fields are shape-checked (R3). Telegram's `text` is
   absent, null, or a string, else refused "the text must be a string"
   (`bridge.TEXT_SHAPE`); `email.send`'s `subject`, `body` and
   `in_reply_to` are absent, null, or strings, and `to`, `cc` and
   `references` absent, null, or lists of strings, else refused with
   `bridge.EMAIL_SHAPE`, before the recipient check and so before
   `message_bytes`, which reads them as text. Each is in the channel's
   own check, which a bridge's `Bound.refuse` runs too. The source is the
   port's declared payload (each usage line), as for `FILES_SHAPE`.
4. Tests, each failing without its change:
   `test_a_request_file_never_stops_the_turn_being_collected` (round 10's
   test, renamed and extended) through `signals.collect` and
   `session.record`: round 10's NUL, surrogate and number requests, each
   with its new reason; a split pair written as an escape plus raw low
   surrogate bytes and a CESU-8 pair; a payload nested 2000 deep (stored,
   reaches the broker: "no performer"), 100000 deep (Postgres:
   `StatementTooComplex (stack depth limit exceeded)`) and 400000 deep
   (`RecursionError` from parsing); Telegram text `5` and `["hi"]`, an email
   subject `5`, a `to` of `[5]`, and an object body, each refused with its
   shape; the clean emoji send held. Without the Postgres ask it fails with
   `UntranslatableCharacter`; without `RecursionError` in the parse catch,
   with `RecursionError`; without the UTF-8 test, with `UnicodeEncodeError`
   in `bridge._units`; without the Telegram shape, with `AttributeError` in
   `split_text`; without the email shape, the subject send is held.
   `test_a_request_is_judged_as_turn_collected_nests_it` bisects, with
   `ledger.unstorable`, the deepest request Postgres stores on its own and
   writes it; it is answered `StatementTooComplex`. With the request asked
   un-nested, `turn.collected` fails with `StatementTooComplex`.
   `test_a_plan_nested_past_the_parser_is_a_plan_error` (test_signals):
   a 400000-deep `plan.json` fresh and from `handled/`; each catch removed
   fails with `RecursionError`.
5. Docs: `docs/harnesses.md`'s effects row, the shapes in
   `docs/bridges/telegram.md` and `email.md`, and the docstrings.

Notes, not closed here: a file name that is not UTF-8 (not possible on
APFS) would reach `turn.collected` as a lone surrogate; many requests each
within jsonb's size limit could together exceed it in the one
`turn.collected` row. A NUL in a text signal or `plan.json` remains round
10's follow-up. Nothing new limits, waits, or guards.

Suite: 1294 passed, 23 skipped (`valor_rebuild_test_2_1p11`, ports 6480-6489).
Ruff check and format check clean.
