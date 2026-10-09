---
tracking: none
type: plan
status: draft
---

# Cutover runbook

How Valor's Cowboy moves from the old system to the new kernel: what is
backed up, exported, disabled, imported, enabled, and verified, in order,
how to go back, and what only Tom does. Data and gaps (G1 to G12, Q1 to
Q3) are in [cutover-data.md](cutover-data.md); the machine's parts are in
[../machine.md](../machine.md); setting up a Mac is
[rebuild-handoff.md](rebuild-handoff.md).

**Cutover date: ______ (Tom writes it here.)** Everything runs on the Cowboy
(`Valor the Cowboy`, user `valorengels`), by Valor, in one sitting. The
Captain, the Bald, and the Pirate are not touched (G11).

Rules for every step: the old system is read only except for its own
services (its Redis is never written, except by the old code itself in
step B3 of the way back; `~/src/ai` is never edited or
switched, no old job is uninstalled; disabled means `launchctl disable`
plus `launchctl bootout`, which `enable` and `bootstrap` undo). No secret
is printed; the archive in step 1 holds copies of files that carry secrets,
so it is made with `umask 077` and stays on this Mac. A step is done when its check passes.

## Shell setup

Every command runs from the kernel checkout, in one shell with this:
```
unset VIRTUAL_ENV; cd ~/src/valor-rebuild
export PGPASSFILE=~/.config/valor-kernel/pgpass
export PATH=/opt/homebrew/opt/postgresql@18/bin:$PATH
export VALOR_BACKUP_DIR=/Volumes/PINK/valor_temp
export VALOR_MACHINE="Valor the Cowboy"
U=$(id -u); D=gui/$U; LA=~/Library/LaunchAgents
DAY=$(date +%F); CUT=~/src/valor-build-notes/cutover-$DAY
SQL="psql -h 127.0.0.1 -U valor_kernel valor_rebuild -At -c"
PY=.venv/bin/python
OLDPY=~/src/ai/.venv/bin/python      # read only use: PYTHONDONTWRITEBYTECODE=1, -I
REB=$(git branch --show-current)     # the rebuild branch, before step 6
```

The old jobs: `com.valor.bridge` (Telegram), `worker`,
`reflection-worker`, `bridge-watchdog`, `update`, `email-bridge`. The new
jobs: `com.valor.kernel`, `kernel.telegram`, `email`, `routine.expiry`,
`routine.emulator`, `backup`. Left as they are: `caffeinate`, `log-rotate`,
`brew-nightly`, `nightly-tests`, the machine Postgres, and the old Redis
(it holds the way back and the Memory records).

## 0. Before the day

Valor does these on any earlier day, on the rebuild branch. They change
nothing live: no bridge receives a listed chat until step 9.

**0.1 The kernel is at the tip.** A1, A2, A3, and B1 are merged and rolled
out.

```
git fetch && git status -sb | head -1
$PY -m core settings | grep -E "SETTING_(MACHINE|PROJECTS_DIR|BACKUP_DIR)"
```

Check: the first line names the branch with no `ahead` or `behind`; the
settings print without an error.

**0.2 The project specs are written and committed** (cutover-data.md
section 2.1, G1, G2, G3, G9). In `projects/`:

- `valor.toml`: `machine = "Valor the Cowboy"`; `chats` gains
  `"telegram:179144806"` (Tom's own DM, G2) beside the operator group
  `telegram:-1003890616618` and the Eng group `telegram:-1003449100931`;
  `branch = "main"` once step 6 is done (it stays the rebuild branch until
  then).
- `popoto.toml`: the draft in cutover-data.md, `machine` changed to
  `"Valor the Cowboy"`, `chats = ["telegram:-5189826365"]`.
- `psyoptimal.toml` and `cuttlefish.toml`: the drafts, `machine` changed
  the same way, and **no `chats` line yet**. Their groups are read by the
  Captain's old bridge, which this runbook cannot stop (G11, Tom's list
  below). Tasks for them start by `core start --project NAME` until the
  Captain's bridge is off; then a one-line edit adds the group
  (`telegram:-1003743854645` for psyoptimal, `telegram:-1003801797780` for
  cuttlefish). `branch` for psyoptimal is `main` (Q1 assumed).
- No other spec. The teammate and customer-service groups, the thirteen
  other DM users, and the email contacts and domains stay out of every spec
  on purpose (G1): a chat in no spec is not read at all. Cyndra, royop,
  mondayflowers, gato-os, counsell-home, yudame, and satsol have no spec
  (G11).

Check, after the commit:

```
for p in valor popoto psyoptimal cuttlefish; do
  $PY -c "from core.workspace import Spec; s=Spec.load('$p'); print(s.name)"; done
$PY -c "from core import intake; print(intake.owned('telegram'))"
```

Check: four names print with no `Refused`; the list holds the operator
group, `-1003449100931`, `-5189826365`, and `179144806` (bare ids, with
`VALOR_MACHINE` exported as above).

**0.3 The jobs and keys exist.** `ls $LA | grep -cE "com.valor.(kernel|kernel.telegram|email|routine.expiry|routine.emulator|backup).plist"`
prints 6, and `ls ~/.config/valor-kernel` lists `claude-token`,
`github-keys`, `judgement-keys`, `mail-keys`, `openai-key`, `pgpass`,
`telegram-keys`, `telegram.session`. A missing key is copied with its
command (`core judgement-keys`, `core openai-key`, `core github-key`,
`bridges.telegram keys`, `bridges.email keys`), never by copying the old
session file.

## 1. Back up

**1.1 The new side.**

```
umask 077; mkdir -p $CUT/launchagents $CUT/kernel
$PY -m core backup
ls -t $VALOR_BACKUP_DIR | head -2
cp $LA/com.valor.*.plist $CUT/launchagents/
cp ~/.config/valor-kernel/telegram-seen.json $CUT/kernel/
```

Check: the newest dump in `$VALOR_BACKUP_DIR` has a time from the last
minute, `ls $CUT/launchagents | wc -l` is at least 15, and `$PY -m core
restore <that dump>` finishes with its manifest check passing (it uses a
scratch cluster, so the ledger is untouched).

**1.2 The old side's files.** All copies, no edits:

```
cp ~/Desktop/Valor/projects.json ~/Desktop/Valor/reflections.yaml $CUT/
git ls-remote https://github.com/tomcounsell/ai.git refs/heads/main | cut -f1 > $CUT/old-main.txt
git -C ~/src/ai rev-parse HEAD > $CUT/old-ai-head.txt
git -C ~/src/ai log --oneline origin/main..HEAD > $CUT/old-ai-unpushed.txt
echo $REB > $CUT/rebuild-branch.txt
launchctl list | grep com.valor > $CUT/launchd-before.txt
cp -R /opt/homebrew/var/db/redis $CUT/old-redis-files
```

Check: `old-main.txt` and `old-ai-head.txt` each hold one 40-character
hash; `ls $CUT/old-redis-files` lists `dump.rdb` and `appendonlydir`. The
first is the commit the remote `main` stands at, the second what the old
checkout runs from (commits it holds that the remote lacks are listed in
`old-ai-unpushed.txt`); the way back needs both.
The Redis files are a copy of what Redis wrote itself; the JSON export in
step 2 is the copy that is read back.

The old Telegram session files are never copied; the new bridge has its
own login, and a copied session breaks the account's authorization key.

## 2. Export, read only

**2.1 The old Memory records** (G8). About 3,450 records have no
destination until the memory milestone (milestone 6). They are exported
now, while the old Redis is up, to JSON lines, and imported later.

```
cd /tmp && PYTHONDONTWRITEBYTECODE=1 $OLDPY -I ~/src/valor-rebuild/scripts/cutover_export_memory.py $CUT/memory.jsonl; cd ~/src/valor-rebuild
```

`scripts/cutover_export_memory.py` runs only `SCAN`, `TYPE`, and `HGETALL`.
It keeps every field but the three derived indexes (`embedding`, `bm25`,
`bloom`), which the new memory rebuilds.

Check:

```
wc -l < $CUT/memory.jsonl
$PY -c "
import json,collections,sys
c=collections.Counter(json.loads(l)['project_key'] for l in open('$CUT/memory.jsonl'))
print(dict(c))"
```

Check: 3,447 lines when this was written, and the script prints the count
it wrote. Project counts are about valor 1,454, company 783, cyndra 572,
satsol 360, psyoptimal 116, gato-os and gato 103, popoto 45, cuttlefish 12.

**2.2 The newest Telegram message the old system recorded, per chat**
(G4) is taken in step 5, after the old bridge has stopped, by
`scripts/cutover_seed_seen.py` (also `SCAN`, `TYPE`, and `HGET` only).

## 3. Quiet the old system

**3.1 The new kernel is idle**, so the restart in step 8 cuts no turn.

```
$PY -m core pending
$SQL "select count(*) from events where at > now() - interval '10 minutes' and type not like 'routine.%'"
```

Check: `pending` prints nothing held, and the count is 0. If a task is mid
turn, wait for its turn to end (`core status TASK_ID`); do not stop it.

**3.2 The old worker has no live turn** (G5). The old sessions are not
imported; open requests are re-asked as messages afterwards.

```
cd ~/src/ai && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m tools.valor_session list --status running,pending --limit 50; cd ~/src/valor-rebuild
tail -n 5 ~/src/ai/logs/worker.log | cut -c1-160
```

Check: the worker log's last write is older than a few minutes
(`ls -l ~/src/ai/logs/worker.log`) and its last lines are idle polling.
`running` rows with an old created time are records of dead sessions (the
old Redis holds dozens). A session created in the last hour with turn
output in the log means wait, then re-run. Save the list (append `>
$CUT/old-open-sessions.txt`) so each open request is re-asked by message
once the new bridge is up.

## 4. Disable the old system on the Cowboy

Order matters: the two jobs that restart the others go first, then the
bridges, then the worker. Each line disables first so launchd cannot
respawn it, then boots it out. These are the same two calls the old
`valor-service.sh worker-disable` and `email-disable` make.

```
for L in com.valor.update com.valor.bridge-watchdog com.valor.bridge \
         com.valor.email-bridge com.valor.worker com.valor.reflection-worker; do
  launchctl disable $D/$L; launchctl bootout $D/$L 2>/dev/null; done
```

Check:

```
launchctl print-disabled $D | grep -E "com.valor.(update|bridge-watchdog|bridge|email-bridge|worker|reflection-worker)\""
launchctl list | grep -E "com.valor.(update|bridge-watchdog|bridge|email-bridge|worker|reflection-worker)\b" ; echo "loaded: $?"
sleep 5; pgrep -fl "bridge/telegram_bridge.py|-m worker|-m reflections|email_bridge" ; echo "procs: $?"
```

Check: six lines each `=> disabled`; `loaded: 1` (grep found none);
`procs: 1` (none). A process still listed after the five seconds is
stopped by its pid: `kill PID`, then `kill -9 PID` only if it is still
there a few seconds later. The old Redis keeps running.

From here until step 9, nothing answers Telegram messages on the Cowboy.
Steps 5 to 9 are kept short for that reason.

## 5. Import: the handover marks (G4, G7)

**5.1 Telegram.** The new bridge's first pass in a chat with no mark
records only the newest message as its starting point. The old system's
last handled id, written as the mark, makes the first pass receive exactly
the messages the old one did not.

```
IDS=$($PY -c "from core import intake; print(' '.join(intake.owned('telegram')))")
cd /tmp && SEEN=$(PYTHONDONTWRITEBYTECODE=1 $OLDPY -I ~/src/valor-rebuild/scripts/cutover_seed_seen.py $IDS); cd ~/src/valor-rebuild
echo "$SEEN" > $CUT/seed-seen.json
$PY - <<PYEOF
import json, os
p = os.path.expanduser("~/.config/valor-kernel/telegram-seen.json")
have = json.load(open(p))
for chat, mid in json.loads('$SEEN').items():
    have[chat] = max(int(have.get(chat, 0)), mid)
tmp = p + ".new"
open(tmp, "w").write(json.dumps(have)); os.chmod(tmp, 0o600); os.replace(tmp, p)
print(have)
PYEOF
```

The command keeps the larger of the old and new mark, so the operator
group's mark is never lowered.

Check: the printed map has an id for each listed chat the old system had
records for (popoto's `-5189826365` has none and keeps the newest-message
start, so a message there between step 4 and step 9 is not received), and
`ls -l ~/.config/valor-kernel/telegram-seen.json` shows `-rw-------`.

**5.2 The mailbox start** (G7). The old email bridge is off, so its unread
mail stays unseen. The new bridge receives only mail on or after
`VALOR_EMAIL_SINCE`, which step 7 sets to the cutover date.

## 6. Tom's step: the branch becomes `main` (G6)

Tom does this (section 12, item 3); Valor waits. Every old service is off
on the Cowboy, so nothing here pulls the old `main`.

Check:

```
git ls-remote https://github.com/tomcounsell/ai.git refs/heads/main refs/tags/old-system
cat $CUT/old-main.txt
```

Check: the tag is the hash in `old-main.txt` and `main` is the rebuild
branch's tip. Then, in the kernel checkout:

```
git fetch && git switch main 2>/dev/null || git switch -c main origin/main
git status -sb | head -1
```

Check: the first line reads `## main...origin/main`. Set `branch = "main"`
in `projects/valor.toml`, commit and push, and `$PY -c "from core.workspace
import Spec; print(Spec.load('valor').branch)"` prints `main`.

## 7. Point the jobs at the Cowboy and the cutover date (G3, G7)

Settings are read when a process starts, so each new job is booted out,
edited, and bootstrapped again in steps 8 and 9. `VALOR_MACHINE` is the
name the specs' `machine` lines match and the turn-slot lock carries; every
job on the Mac holds the same value.

```
for J in kernel kernel.telegram email routine.expiry routine.emulator backup; do
  launchctl bootout $D/com.valor.$J 2>/dev/null
  plutil -replace EnvironmentVariables.VALOR_MACHINE -string "Valor the Cowboy" $LA/com.valor.$J.plist
done
plutil -replace EnvironmentVariables.VALOR_EMAIL_SINCE -string "$DAY" $LA/com.valor.email.plist
plutil -replace EnvironmentVariables.VALOR_EMAIL_SINCE -string "$DAY" $LA/com.valor.kernel.plist
```

Check:

```
for J in kernel kernel.telegram email routine.expiry routine.emulator backup; do
  printf "%s " $J; plutil -extract EnvironmentVariables.VALOR_MACHINE raw $LA/com.valor.$J.plist; done
plutil -extract EnvironmentVariables.VALOR_EMAIL_SINCE raw $LA/com.valor.email.plist
plutil -extract EnvironmentVariables.VALOR_OPERATOR_TELEGRAM_ID raw $LA/com.valor.kernel.plist
plutil -extract EnvironmentVariables.VALOR_OPERATOR_CHAT raw $LA/com.valor.kernel.telegram.plist
plutil -lint $LA/com.valor.*.plist | grep -v OK
```

Check: six lines ending `Valor the Cowboy`; the cutover date; `179144806`
(Tom's id, not the window stand-in's `8833713379`); `-1003890616618`;
`plutil -lint` prints no line that is not `OK`.

## 8. Grants and the kernel

**8.1 Merge grants** (Tom's, section 12 item 2; Q2). Four pairs.

```
$PY -m core merge-target list
```

Check: the list holds `https://github.com/tomcounsell/ai.git main`,
`https://github.com/tomcounsell/popoto.git main`,
`https://github.com/yudame/psyoptimal.git main`, and
`https://github.com/yudame/cuttlefish.git <its branch>`. Without a pair,
`core start --project NAME` refuses; the refusal names the pair.

**8.2 Check Valor's push access to each repo** before its first task.

```
for R in tomcounsell/ai tomcounsell/popoto yudame/psyoptimal yudame/cuttlefish; do
  printf "%s " $R; gh api repos/$R --jq .permissions.push; done
```

Check: four `true`. A `false` means the spec for that repo waits for
access (by hand, GitHub collaborator).

**8.3 Start the kernel and the routines.**

```
launchctl enable $D/com.valor.kernel; launchctl bootstrap $D $LA/com.valor.kernel.plist
for J in routine.expiry routine.emulator backup; do
  launchctl enable $D/com.valor.$J; launchctl bootstrap $D $LA/com.valor.$J.plist; done
sleep 10
```

Check:

```
launchctl print $D/com.valor.kernel | grep -E "state =|pid ="
tail -n 5 ~/Library/Logs/valor/kernel.log | cut -c1-160
$PY -m core routines
$PY -m core settings | grep SETTING_MACHINE
```

Check: `state = running` with a pid; the log's last lines show no `serve
refused:`; both routines are listed; `SETTING_MACHINE` is read in this shell
from `VALOR_MACHINE` and prints `Valor the Cowboy`.

## 9. Enable the new bridges

```
launchctl enable $D/com.valor.kernel.telegram; launchctl bootstrap $D $LA/com.valor.kernel.telegram.plist
launchctl enable $D/com.valor.email; launchctl bootstrap $D $LA/com.valor.email.plist
date -u +%FT%TZ > $CUT/cutover-at.txt
sleep 20
```

Check:

```
launchctl print $D/com.valor.kernel.telegram | grep -E "state =|pid ="
launchctl print $D/com.valor.email | grep -E "state =|pid ="
tail -n 8 ~/Library/Logs/valor/telegram.log | cut -c1-160
tail -n 8 ~/Library/Logs/valor/email.log | cut -c1-160
```

Check: both `state = running`; the Telegram log shows a connection and a
gap fill with no traceback; the email log shows a login to the mailbox with
no `credential` error. The window between step 4 and this step is the time
the Cowboy's chats had no reader.

## 10. Verify

Valor checks each item and records the output in `$CUT/verify.txt`.

**10.1 Nothing of the old system is running on the Cowboy.**

```
pgrep -fl "bridge/telegram_bridge.py|-m worker|-m reflections|email_bridge|bridge_watchdog|remote-update" ; echo "old procs: $?"
```

Check: `old procs: 1`.

**10.2 Every listed chat is read, and nothing arrives twice.**

```
$SQL "select payload->>'chat_id', count(*), count(distinct payload->>'message_id') from events where type='message.received' and at > now() - interval '30 minutes' group by 1 order by 1"
$PY -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/valor-kernel/telegram-seen.json'))))"
```

Check: in each row the two counts are equal; each chat in a spec has a
mark in the second line at least as high as its step 5 value. A message
the old bridge answered (`tail ~/src/ai/logs/bridge.log`) that also
appears in the ledger is a double: write it down as a defect.

**10.3 A request is bound and answered.** Valor drives it as the windows
did, with the stand-in bot `@valor_window_standin_bot` (token in
`~/.config/valor-kernel/window-bot`, a member of the operator group): set
`VALOR_OPERATOR_TELEGRAM_ID` to its id in the kernel and Telegram plists,
boot both out and bootstrap them, send one request from the stand-in, wait
for the task's notice, then restore the two plists from
`$CUT/launchagents/` and boot out and bootstrap both again.

```
$SQL "select id, type from events where type in ('message.received','message.bound','notice.sent') order by id desc limit 6"
plutil -extract EnvironmentVariables.VALOR_OPERATOR_TELEGRAM_ID raw $LA/com.valor.kernel.plist
```

Check: a `message.received`, a `message.bound` (`start`), and a
`notice.sent` appear in order; the last line prints `179144806` again.

**10.4 The mailbox.**

```
tail -n 20 ~/Library/Logs/valor/email.log | cut -c1-160
$SQL "select count(*) from events where type='message.received' and payload->>'channel'='email'"
```

Check: the log shows the poll reaching the mailbox and no old backlog
received (every received message is dated on or after the cutover date).

**10.5 A task reaches a merge under each granted pair.** One small task
per project, started by a message in its group (`core start ... --project
NAME` for psyoptimal and cuttlefish until their chats are added). The
live window records the first run; at cutover one real task from Tom's
backlog is enough.

```
$PY -m core start "TEXT" --project popoto
$PY -m core status TASK_ID
```

Check: the task reaches `merge` and `merged` on the granted URL and branch
with no held effect waiting for Tom.

**10.6 The routines run and the backup runs.**

```
$PY -m core routines
launchctl kickstart $D/com.valor.backup; sleep 30; ls -t $VALOR_BACKUP_DIR | head -1
```

Check: both routines list a run date from the last day (the expiry job
runs at 04:00) and a new dump appears.

## 11. The way back

It is in [cutover-runbook-back.md](cutover-runbook-back.md): stop the new
system (grants off, tasks stopped, kernel stopped), keep the new `main` tip
under a tag before Tom rewinds `main`, write the messages the new system
handled into the old dedup, re-enable the old jobs with the update job
last, and settle unsent notices before cutting over again. Use it if a
check in steps 8 to 10 fails and cannot be fixed in place, or when Tom says
so.

## 12. What Tom alone does

The only places a person is in the path; none is a tap on work Valor started.

1. **Writes the cutover date** at the top of this file.
2. **Grants the merge targets** (Q2; the command is always his):

   ```
   $PY -m core merge-target add https://github.com/tomcounsell/ai.git main --note "cutover"
   $PY -m core merge-target add https://github.com/tomcounsell/popoto.git main --note "cutover"
   $PY -m core merge-target add https://github.com/yudame/psyoptimal.git main --note "cutover"
   $PY -m core merge-target add https://github.com/yudame/cuttlefish.git main --note "cutover"
   ```

   The cuttlefish branch is assumed `main`; if its deploy branch differs,
   the spec and the grant carry that one. This is a grant of authority, not
   a tap on a held action; the lead decides whether the A1 ruling covers it.
3. **Makes the rebuild branch `main`** (G6), after step 4 has printed its
   checks. First, the four open pull requests against `main` (#3607, #3598,
   #3596, #3593) are closed or relabeled as aimed at the old system, since
   they are Tom's and would show diverged diffs. Then the old tip is kept
   under a tag:

   ```
   git push origin $(cat $CUT/old-main.txt):refs/tags/old-system
   git push origin $REB:main --force-with-lease=main:$(cat $CUT/old-main.txt)
   ```

   The first line tags the old `main` commit; the second makes `main` the
   rebuild branch's tip (the histories differ, so it is a forced update)
   and fails if `main` moved since. It is not a merge commit: a merge
   would let every old Mac fast-forward onto the rebuild and lose its
   bridge. As a forced update it is inert there: the old cron update does
   `merge --ff-only`, which fails on diverged history and logs "continuing
   with current code". His `gh` login, not Valor's, since the ruleset
   restricts updates to `main`. Afterwards no old Mac may run `/update
   --full`, whose pull falls back to a rebase and leaves that checkout
   mid-rebase. Tom tells whoever holds each of the Captain, the Bald, and
   the Pirate (by message, one line: "do not run /update --full"). Their
   update jobs stay enabled, since the cron path is safe.
4. **Sets ruleset 24370170 to active** (admin only), after item 3:

   ```
   gh api -X PUT repos/tomcounsell/ai/rulesets/24370170 -f enforcement=active
   ```

   After this, pushes to `main` go only through the kernel's merge
   performer, under the grant in item 2. To undo, `enforcement=disabled`.
   Valor's account is not a bypass actor: `gh api
   repos/tomcounsell/ai/rulesets/24370170` reports `current_user_can_bypass:
   never` for it (the ruleset's actor list itself is hidden from Valor's
   token, so Tom can confirm it in the ruleset's page).
5. **Stops the Captain's old bridge, worker, and email bridge** (G11), or
   says which person or session holds that Mac, so psyoptimal and
   cuttlefish chats can be added to their specs. On the Captain, as
   `valorengels`, the same lines as step 4 with that Mac's labels. Until
   then those two projects run by `core start --project`. The Captain's old
   email bridge also polls Tom's mailbox for the cuttlefish contacts list;
   until it stops, it marks his mail seen.
6. **Any governance grant** a diff in the final merges carries
   (`python -m core grant TASK_ID INSTANCE --note TEXT`), one per instance.
   Nothing else is held for him: an `act`-class effect from a task he
   started runs without a tap.
7. **Uses the work.** When the first task from his backlog merges, Valor
   sends one report saying what shipped and where to use it; his use is
   recorded with `python -m core used TASK --by tom`.

## 13. Gaps, each with its step or why it is open

| Gap | Where it is handled |
|---|---|
| G1 other people's messages | Step 0.2 (left out of every spec; one operator by design) |
| G2 Tom's DM, G3 machine names | Step 0.2 (`telegram:179144806` in `valor.toml`, `machine` in every spec) and step 7; checks in 0.2, 7, 8.3, 10.3 |
| G4 handover between bridges, G5 in-flight old work | Steps 2.2, 3.2, 4, 5.1; check in 10.2; open requests are re-asked by message |
| G6 branch is not `main`, G7 mailbox backlog | Step 6 and Tom's items 3 and 4; steps 4, 5.2, 7, check in 10.4 |
| G8 Memory and upkeep | Step 2.1 exports; the import waits for milestone 6; no upkeep routine is added until an incident names the need |
| G9 fields with no home, G10 third-party keys, G12 Google tokens | Left open on purpose: the spec carries only what the kernel acts on, a turn's sandbox denies the vault (one key is copied when a task needs it), the kernel does not read the Google files |
| G11 the other three Macs | Open for the Captain, Bald, Pirate; Cowboy only; Tom's item 5 |
| Q1, Q2, Q3 | `main` in step 0.2; grants in 8.1 and Tom's item 2; the operator group stays the operator chat (step 7 check) |

## Decided by default

The way back's decisions are in the back file. For the move of `main`:

- **A forced update with the `old-system` tag, not a merge commit or a new
  default branch.** Reason: a merge commit lets every old Mac fast-forward
  onto the rebuild, whose tree has no old bridge or update script, so the
  old system dies on every Mac; a new default branch buys nothing, since
  the ruleset, the grants, and the specs all name `main` and the old Macs
  keep pulling it.
- **Open pull requests closed or relabeled before the force.** Reason: they
  would show diverged diffs against the rebuild.
- **No `/update --full` on an old Mac after the force.** Reason: its pull
  falls back to a rebase on divergence and never aborts it. The note goes
  to each old Mac's holder by message; the cron update path is safe and
  stays on.
- **The ruleset's bypass list was read.** Valor's account is not on it
  (`current_user_can_bypass: never`), so the ruleset tells an old Mac's
  push from the kernel's push.

The old Redis, its data, and `~/Desktop/Valor` stay running and in place;
Tom removes them on a later day, once the way back is not wanted.
