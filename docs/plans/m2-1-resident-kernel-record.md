# 2.1 The resident kernel: the record

The critique and patch rounds of [m2-1-resident-kernel.md](m2-1-resident-kernel.md),
in order.

## Critique round 1 (of 2): revise

Each finding of critique-2-1-r1.md, and how this revision handles it.

- F1, performers per process: Performers per task from the Brief, in
  `step` and per effect in recover; 1.4d's signatures and async `refuse`;
  `declared_performers()` in every task.
- F2, signals lost after `turn.ended`: recover re-collects through
  `read_turn_file`; test added.
- F3, live calls charged: `holder` on `gateway.opened`; only free holders
  are charged; test added.
- F4, schema not re-runnable: `IF NOT EXISTS`, `OR REPLACE`, drop then
  create the trigger; `test_migrate_twice`.
- F5, nested `sent`: one GIN index over `COALESCE` of both paths; test.
- F6, refused releases yielded forever: yielded only with no intent,
  outcome, or `effect.refused`; the bridge release appends it once;
  `release.requested` on the task stream.
- F7, approval then release in `bind`: `bind` writes the approval and
  `release.requested` together; `schedule` releases kernel ones; merge
  self-restart and approve-after-stop covered.
- F8, propose-class reconcile: `reconcile` reads the intent row; test.
- F9, sweep stops kept services: `services:<task>` lock; sweep needs both
  locks free; 1.4b noted.
- F10, steering spent by fresh turns: only working-session turns spend
  it; test during checks.
- F11, binding: the port's decisions 10 to 14.
- F12, context identity: `kernel_commit` and `offered` on `turn.started`;
  the claim restated; fresh-process test.
- F13, the slot: `core/slot.py`, per turn, FIFO, shared with `core run`
  and the checks; 4.3 adds its sort key and preemption.
- F14, the port: [m2-1-port.md](m2-1-port.md), written to the lead's
  decisions.
- F15, the operator group: settings, `valor.toml`, rollout step 2, and Q1
  narrowed to identities.
- F16: `Spec` fields, the plist `PATH`, plists printed for Tom (decision
  31), the caffeinate limit, a connection per concurrent intake call,
  notices deduplicated across kernels.
- F17: no governance added (the DMARC check is 2.3's, round 2 E).
- F18: Q2 decided by default (decision 15).
- F19: limits cited as protocol facts with split and request-time refusal
  (decision 15b); near-miss notices (15a); the start rule widened to
  `valor` for unlisted chats.

## Critique round 2 (of 2): revise

Each finding of critique-2-1-r2.md and how it is built in.

- A: the intent carries the action; reconcile reads it, falling back to
  `effect.held`; 1.4d builds to the shape; test added.
- B: `slot.held` is reentrant within a process; test added.
- C: a binding that raises binds `none` and owes a notice; `approve`
  reads the effect first (port item 39); test added.
- D: a refused kernel release appends `effect.refused` once and owes a
  notice (item 40); test added.
- E: the DMARC check is 2.3's, under its grant (item 11a); email is
  `verified: false` until it lands.
- F: a provision job off the loop; a failure owes a notice; test added.
- G: `settle_after_s` is a number or a function (item 37).
- H, I: the limits live in `core/bridge.py`; Telegram counts UTF-16
  units; email's limit is the whole message through a size function
  (item 15c).
- J: `Bridge.tick()` (item 38).
- K: every MTProto record is verified; operator status is decided at
  bind; chat ids are marked strings in records and in `sent`.
- L: a steer no runner reads owes a notice.
- M: `recollect` reads `handled/` and what remains in `.valor/`, and
  rebuilds `state` and `finished` from the turn rows; test added.
- N: one services handle per task in `serve`; the reap runs when it
  first starts them; `core run` refuses a task the kernel holds.
- Low 1: an absent `machine` is `settings.default_machine`. Low 2: the
  start rule cites 2.3's rollout. Low 3: 4.1 dropped from the schema
  row. Low 4: 4.3 is told the slot is `core/slot.py`. Low 5: `offered`
  is recorded after narrowing. Low 6: the port says how a lookup finds
  the intent's `at`. Low 7: `step` takes a Performers factory. Low 8:
  the cc is decided by default. Low 9: an email near-approve notice says
  approvals come by Telegram.

## Patch round 1

Review round 1 said `changes`, the test check `gaps`. Retries ride the
`serve_tick_s` wake or a row on the task's stream.

1, 3. A failed `services:<task>` claim, a release that raises other than
   a refusal, or services that cannot open park the task and free the
   harness slot until its next row or the next tick. Tests.
2. `slot.held` unlocks before closing its session.
4. `message.bound` is each binding's first row; a second binder writes
   nothing. Test with two binders.
5. `core/README.md` brought to the code.
6. A notice with no chat writes `notice.undeliverable`. Test.
7. Files alone from Tom start a task listing them. Test.
8. The outbox relistens after a drop; an `is_error` turn leaves the
   steering; one task's error is logged and the rest go on. Tests.
   Telegram `verified` is true for every record (Telegram attests the
   sender id); bind checks `sender_id == operator_telegram_id`. The
   port said so; m2-2-telegram.md's row 11 is corrected.
9. Tests: `recorded`, `claimed`, one bridge per channel and machine,
   `core run` refusing while `services:<task>` is held.
10. `intake.lowest(channel, chat)`, the smallest integer id recorded for
    the chat or None, so 2.2's gap fill of a chat with no seen entry
    stops there by membership (D32). Test with `highest`.
11. The plan and the port describe reconcile as waiting on the
    effect's performing lock, then reading the remote; no age.
12. The rounds live in this file, linked from the plan, so each file
    stays under 600 lines.
