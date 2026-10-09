---
tracking: none
type: plan
status: draft
---

# Cutover runbook: the way back

The way back from [cutover-runbook.md](cutover-runbook.md). Use it if a
check in steps 8 to 10 fails and cannot be fixed in place, or when Tom says
so. In a new shell, run the runbook's shell setup with `DAY` set to the
cutover date and `REB=$(cat $CUT/rebuild-branch.txt)`.

The old system's files and checkout were never touched. Its Redis was
written only by the old system, until step B3, the one step in this
document that writes it.

## B1. Stop the new system, in this order

Nothing else runs first. A merge grant is re-checked before each push, so
the grants come off before anything is stopped, and a task cannot land on a
branch that step B2 is about to move.

```
for R in "https://github.com/tomcounsell/ai.git main" "https://github.com/tomcounsell/popoto.git main" \
         "https://github.com/yudame/psyoptimal.git main" "https://github.com/yudame/cuttlefish.git main"; do
  $PY -m core merge-target remove $R --note "go back" --by valor; done
$PY -m core merge-target list
$SQL "select distinct task_id from events where at > now() - interval '1 day' and task_id not in ('telegram','email','local') and task_id not like 'routine%'"
```

Check: the list is empty. (If the cuttlefish grant names another branch, use
that branch.) For each task id printed, `$PY -m core status TASK_ID`; every
one not `merged` or `stopped` is stopped with `$PY -m core stop TASK_ID
--reason "go back"`. Then stop the kernel, the bridges, and the routines:

```
for J in kernel.telegram email kernel routine.expiry routine.emulator backup; do
  launchctl disable $D/com.valor.$J; launchctl bootout $D/com.valor.$J 2>/dev/null; done
launchctl list | grep -E "com.valor.(kernel|kernel.telegram|email|routine.expiry|routine.emulator|backup)\b" ; echo "loaded: $?"
```

Check: `loaded: 1`. Every task shown by `core status` is `merged` or
`stopped`.

## B2. Put the old branch back, if step 6 ran

First keep the new `main` tip, because the kernel's merges landed on `main`
after step 6 and the reset would orphan them. Tom does the push (his
login; the ruleset restricts updates to `main`). He sets the ruleset
24370170 to disabled first.

```
NEWTIP=$(git ls-remote https://github.com/tomcounsell/ai.git refs/heads/main | cut -f1)
git push origin $NEWTIP:refs/tags/new-system-$DAY
git push origin $(cat $CUT/old-main.txt):main --force-with-lease=main:$NEWTIP
```

In the kernel checkout, switch to the tag, not to `$REB`: `git fetch --tags
&& git switch --detach new-system-$DAY`. Set the `branch` line of
`projects/valor.toml` back to `$REB`'s name.

Check: `git ls-remote https://github.com/tomcounsell/ai.git refs/heads/main
refs/tags/new-system-$DAY` prints the old hash for `main` and `$NEWTIP` for
the tag, and `git -C ~/src/ai rev-parse HEAD` prints the one in
`old-ai-head.txt`.

## B3. Record what the new system handled in the old dedup

This is the only step that writes the old Redis. At go-back the old system
is being restored, and its own functions write its own record:
`bridge.dedup.record_message_processed` and `record_last_processed`. The old
bridge's start-up scan and its 3-minute reconciler skip only a message the
old dedup holds or one the account sent; the cursor still points before
step 4. Without this step every Telegram message the new system received
since step 9 is dispatched again and answered twice. Email is not in the
old dedup (the old email bridge was never loaded on the Cowboy).

`scripts/cutover_goback_dedup.py` is go-back only. It reads `chat_id`,
`message_id`, `sent_at` lines on stdin and calls the two old functions for
each, oldest first, with the old interpreter. The lines come from a
read-only query on the new ledger:

```
$SQL "select payload->>'chat_id', payload->>'message_id', payload->>'sent_at' from events where type='message.received' and task_id='telegram' and at >= '$(cat $CUT/cutover-at.txt)' order by id" \
  | tr '|' '\t' > $CUT/goback-ids.tsv
wc -l < $CUT/goback-ids.tsv
cd /tmp && PYTHONDONTWRITEBYTECODE=1 $OLDPY -I ~/src/valor-rebuild/scripts/cutover_goback_dedup.py < $CUT/goback-ids.tsv; cd ~/src/valor-rebuild
```

Check: the script prints `recorded N messages` with N equal to the line
count above. Spot check one id with the old code: `cd ~/src/ai &&
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import asyncio; from
bridge.dedup import is_duplicate_message as d; print(asyncio.run(d(CHAT,
ID)))"` prints `True` (substitute one chat and id from the file).

## B4. Re-enable the old jobs, in the reverse of step 4

The worker comes before the bridge, so a message the bridge catches has a
worker waiting. The update job comes last and only once B2's check has
passed: the old update job rebases onto the branch it finds on `main`, and
on the rebuild's history that leaves `~/src/ai` mid-rebase.

```
for L in com.valor.worker com.valor.reflection-worker com.valor.bridge \
         com.valor.bridge-watchdog; do
  launchctl enable $D/$L; launchctl bootstrap $D $LA/$L.plist; done
```

Bootstrap the old email bridge (`com.valor.email-bridge`) only if
`$CUT/launchd-before.txt` lists it; otherwise only `launchctl enable` it.
Check:

```
launchctl list | grep -E "com.valor.(worker|reflection-worker|bridge|bridge-watchdog)\b"
tail -n 20 ~/src/ai/logs/bridge.log | cut -c1-160
```

Check: four jobs listed, the bridge with a pid; its log shows a connection
and its catch-up, and none of the ids from B3 is dispatched. Then, once B2's
check has passed:

```
launchctl enable $D/com.valor.update; launchctl bootstrap $D $LA/com.valor.update.plist
launchctl list | grep "com.valor.update"
```

## B5. Undo the new jobs' edits

Only if they are not just paused: `cp $CUT/launchagents/com.valor.*.plist
$LA/` (do not bootstrap them). The telegram-seen marks stay: they only move
forward, and the new bridge needs them when it returns.

## To cut over again

1. Settle the notices queued while the bridges were down. They are sent
   when the new bridge returns, into chats the old system has answered
   since. List them:

   ```
   $SQL "select n.id, n.task_id, left(n.payload->>'text',60) from events n where n.type='notice.requested' and not exists (select 1 from events s where s.type='notice.sent' and s.payload->>'notice_id' = n.payload->>'notice_id') order by n.id"
   ```

   A notice still wanted stays and goes out. A stale one is closed with the
   row the bridge itself writes, with nothing sent:

   ```
   $PY - <<'PYEOF'
   import asyncio
   from core import db, ledger
   async def main():
       async with await db.connect() as c:
           await ledger.append(c, "TASK_ID", "notice.sent", {"notice_id": "NOTICE_ID", "sent": []})
   asyncio.run(main())
   PYEOF
   ```

   (`TASK_ID` and `NOTICE_ID` come from the listing; the notice id is the
   `notice_id` in the row's payload.)
2. Run step 5 again, since the old bridge handled messages in the meantime.
3. Grant the four merge targets again (Tom's item 2), enable the kernel,
   routines, and bridges as in steps 8 and 9, and write a new
   `$CUT/cutover-at.txt`.

## Decided by default

Each is the lead's decision, with its reason.

1. **Messages answered twice on the way back.** B3 writes the old dedup
   through the old code's own functions, run with the old interpreter.
   Reason: the old start-up scan and reconciler trust only the old dedup and
   a cursor that points before cutover, so without the write every message
   the new system handled is answered again, and a merge could be redone.
   The rule that the old Redis is never written gives way for this one step,
   because at go-back the old system is being restored and its own function
   writes its own record.
2. **B1 first.** The four grants are removed, running tasks stopped with
   `core stop`, then the kernel stopped, before anything else. Reason: a
   grant is re-checked before each push, so a still-running kernel could
   push to the old `main` after B2, and popoto would be merged by both
   systems.
3. **B2 tags the new tip** (`new-system-<date>`) before rewinding. Reason:
   after step 6 the kernel's merges land on `main`, and a forced reset
   would orphan them.
4. **B4 enables `com.valor.update` last**, after B2's check. Reason: the old
   update job rebases onto whatever `main` holds; on the rebuild's history
   it conflicts and leaves `~/src/ai` mid-rebase, which breaks the old
   system on its next restart.
5. **Notices are settled before a re-cutover.** Reason: outbound notices
   queue in the ledger and go out when the bridge returns, however old.
