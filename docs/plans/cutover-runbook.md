---
tracking: none
type: plan
status: draft
---

# Cutover runbook

How Valor's Cowboy moves from the old system to the new kernel: what is
backed up, exported, disabled, imported, enabled, and verified, in order,
how to go back, and what only Tom does. Data and gaps (G1 to G12, and
Q3) are in [cutover-data.md](cutover-data.md); the machine's parts are in
[../machine.md](../machine.md); setting up a Mac is
[rebuild-handoff.md](rebuild-handoff.md); what only Tom does is
[cutover-runbook-tom.md](cutover-runbook-tom.md).

**Cutover date: 2026-10-14 (Tom's date, asked through the architect).** Everything runs on the Cowboy
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

Every command runs from the kernel checkout, in one `bash` shell (the
variables below hold commands and lists that zsh does not split) with this:
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

The old jobs: `com.valor.bridge` (Telegram), `worker`, `reflection-worker`,
`bridge-watchdog`, `update`, `email-bridge`. The new jobs: `com.valor.kernel`,
`kernel.telegram`, `email`, and `backup`, and the routines `verify` (02:00),
`expiry` (04:00), `conditions` (05:00 daily), and `emulator` (Sunday 01:00),
each `routine.NAME`. Left as they are: `caffeinate`, `log-rotate`,
`brew-nightly`, `nightly-tests`, the machine Postgres, and the old Redis.

## 0. Before the day

Valor does these on any earlier day, on the rebuild branch. They change
nothing live: no bridge receives a listed chat until step 9.

**0.1 The kernel is at the tip, with memory.** B1's rollout by hand
([b1-memory.md](b1-memory.md), "Rollout, by hand") was done on this Mac.
Two schema changes since the rollout of 2026-10-09 need `migrate` run by
hand with the kernel stopped: the verdict constraint (`ended`, `rebase`),
and the memory schema, whose store `migrate` drops and the next ingest
rebuilds when `memory.ingested` lacks the `ledger` column. The kernel's job
also gains `VALOR_BACKUP_DIR`, so a later rollout's backup before a schema
change lands on PINK. The memory store stays on this Mac's own Postgres
(`VALOR_MEMORY_DSN` unset: the schema `memory` in `valor_rebuild`); a second
Mac reaching it is the Captain's later install.

```
launchctl bootout $D/com.valor.kernel
git pull --ff-only && git status -sb | head -1
uv sync --frozen
$PY -m core migrate
plutil -replace EnvironmentVariables.VALOR_BACKUP_DIR -string "$VALOR_BACKUP_DIR" $LA/com.valor.kernel.plist
launchctl bootstrap $D $LA/com.valor.kernel.plist
sleep 10; launchctl print $D/com.valor.kernel | grep -E "state =|pid ="
$PY -m core settings | grep -E "SETTING_(MACHINE|PROJECTS_DIR|BACKUP_DIR|MEMORY|MEMORY_DSN)="
cut -d: -f1-4 ~/.config/valor-kernel/pgpass | grep -c valor_memory
$PY -m core memory ingest
$SQL "select count(*) from events e where e.type in ('task.started','question.answered','feedback.given','correction.recorded','turn.ended','memory.marked') and (e.task_id in ('corrections','memory') or exists (select 1 from events t where t.task_id = e.task_id and t.type = 'turn.started'))"
```

Check: the `git status` line names the branch with no `ahead` or `behind`;
`uv sync` and `migrate` end with no error; `state = running` with a pid; the
settings print without an error, `SETTING_MEMORY=on` and
`SETTING_MEMORY_DSN=''`; the count is 2 (the kernel database and its test
database); the ingest prints `{"rows": N, "records": M}` with no error (not
`skipped` and not `"memory": "off"`), N close to the last count, since the
store is rebuilt from every row it takes (a turn the kernel ran since its
restart has had its rows taken already).

**0.2 The project specs are committed** on the rebuild branch
(cutover-data.md section 2.1, G1, G3, G9; one Mac per client, Tom,
2026-10-11). In `projects/`:

- `popoto.toml`: `machine = "Valor the Cowboy"`, `client = "yudame"`
  (internal Yudame work, on the Cowboy with valor),
  `chats = ["telegram:-5189826365"]`.
- `psyoptimal.toml` and `cuttlefish.toml`: `machine = "Valor the
  Captain"`, which served them in the old fleet, with their groups in
  `chats` (`telegram:-1003743854645`, `telegram:-1003801797780`) and no
  `client` (open for Tom). No kernel runs with that machine name, so both
  are inert here: no bridge on the Cowboy reads their groups and the
  `conditions` routine does not fetch their repositories. They wait for the
  Captain's own install (G11); until then the Captain's old bridge keeps
  their groups. `branch` is `main` for both; cuttlefish's is unconfirmed.
- `valor.toml` is not edited here; it changes in step 7 (decided 2).
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

Check: four names print with no `Refused`; the list is `['-5189826365']`
(with `VALOR_MACHINE` exported as above, `valor.toml` still says `Mac` until
step 7, and the Captain's two groups are not this Mac's).

**0.3 The jobs and keys exist.** `ls $LA | grep -cE "com.valor.(kernel|kernel.telegram|email|routine.expiry|routine.emulator|routine.verify|routine.conditions|backup).plist"`
prints 8 (a missing routine job is printed by `$PY -m core routine NAME
--plist > $LA/com.valor.routine.NAME.plist`, run with the environment
`com.valor.routine.expiry` carries), and `ls ~/.config/valor-kernel` lists `claude-token`,
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
minute, `ls $CUT/launchagents | wc -l` is at least 16, and `$PY -m core
restore <that dump>` finishes with its manifest check passing (it uses a
scratch cluster, so the ledger is untouched). The dump does not hold the
schema `memory`; after any restore into the live ledger, `$PY -m core
memory ingest` rebuilds it from the ledger.

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

**2.1 The old Memory records** (G8). About 3,450 records. The new memory
takes records only from the ledger (`python -m core memory ingest`), so
nothing reads the old ones. They are exported now, while the old Redis is
up, to JSON lines, so they survive the old Redis and a later import task
has its input.

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

Check: the line count is the one the script prints (3,461 at cutover:
valor 1,464, company 783, cyndra 572, satsol 360, psyoptimal 116).

**2.2 The newest Telegram message the old system recorded, per chat**
(G4) is taken in step 5, after the old bridge has stopped, by
`scripts/cutover_seed_seen.py` (also `SCAN`, `TYPE`, and `HGET` only).

## 3. Quiet the old system

**3.1 The new kernel is idle**, so the restart in step 8 cuts no turn.

```
$SQL "select count(*) from events r where r.type = 'release.requested' and not exists (select 1 from events e where e.payload->>'effect_id' = r.payload->>'effect_id' and e.type in ('effect.outcome', 'effect.refused'))"
$SQL "select count(*) from events where at > now() - interval '10 minutes' and type not like 'routine.%'"
```

Check: the first count lists no send waiting for a bridge. The second is
0 or, with a backlog running, the running turn has ended (`core status
TASK_ID`); then go straight on. Never stop a task: the kernel's recovery
covers a restart between turns. A running emulator sweep is stopped before
this step (`core stop RUN`, then its watchers by pid); its finished arms
are recorded and the rest held. Notices not yet sent go out in step 9;
before it, one Tom should not get (a question answered, a refusal on a
guard nobody adds) is closed with `scripts/cutover_close_notice.py`.

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
the messages the old one did not. The valor spec gains its chats only in
step 7, so they are named here by hand.

```
IDS="$($PY -c "from core import intake; print(' '.join(intake.owned('telegram')))") -1003890616618 -1003449100931 179144806"
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
records for (a chat with none keeps the newest-message start), and
`ls -l ~/.config/valor-kernel/telegram-seen.json` shows `-rw-------`.

**5.2 The mailbox start** (G7). The old email bridge is off, so its unread
mail stays unseen. The new bridge receives only mail on or after
`VALOR_EMAIL_SINCE`, which step 7 sets to the cutover date.

## 6. Tom's yes: the branch becomes `main` (G6)

Tom answers one plain yes or no; on his yes Valor does item 3 of
[cutover-runbook-tom.md](cutover-runbook-tom.md) (`valorengels` can push, `main` is unprotected), after
`git fetch`, since a merge may have moved origin's `$REB` past the checkout.
Every old service is off on the Cowboy, so nothing here pulls the old `main`.

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

Check: the first line reads `## main...origin/main`. Then every `branch`
line that names the rebuild branch moves to `main`: the valor spec and the
`emulator`, `expiry`, and `verify` routines (`conditions` names none; it
reads each business on its own branch).

```
sed -i '' "s/^branch = \"$REB\"$/branch = \"main\"/" projects/valor.toml routines/emulator/routine.toml routines/expiry/routine.toml routines/verify/routine.toml
grep -n "^branch" projects/valor.toml routines/*/routine.toml
git commit -qam "Cutover: valor and its routines land on main" && git push origin main
$PY -c "from core.workspace import Spec; print(Spec.load('valor').branch)"
```

Check: four lines `branch = "main"` and none naming `$REB`; the push
succeeds; the last line prints `main`.

## 7. Point the jobs at the Cowboy and the cutover date (G3, G7)

Settings are read when a process starts, so each new job is booted out,
edited, and bootstrapped again in steps 8 and 9. `VALOR_MACHINE` is the
name the specs' `machine` lines match and the turn-slot lock carries; every
job on the Mac holds the same value. The valor spec moves in the same step
(0.2): in `projects/valor.toml`, `machine = "Valor the Cowboy"` and
`chats = ["telegram:-1003890616618", "telegram:-1003449100931",
"telegram:179144806"]` (the operator group, the Eng group, and Tom's own
DM, G2), committed and pushed to `main`.

```
for J in kernel kernel.telegram email routine.expiry routine.emulator routine.verify routine.conditions backup; do
  launchctl bootout $D/com.valor.$J 2>/dev/null
  plutil -replace EnvironmentVariables.VALOR_MACHINE -string "Valor the Cowboy" $LA/com.valor.$J.plist
done
plutil -replace EnvironmentVariables.VALOR_EMAIL_SINCE -string "$DAY" $LA/com.valor.email.plist
```

Check:

```
for J in kernel kernel.telegram email routine.expiry routine.emulator routine.verify routine.conditions backup; do
  printf "%s " $J; plutil -extract EnvironmentVariables.VALOR_MACHINE raw $LA/com.valor.$J.plist; done
plutil -extract EnvironmentVariables.VALOR_EMAIL_SINCE raw $LA/com.valor.email.plist
plutil -extract EnvironmentVariables.VALOR_OPERATOR_TELEGRAM_ID raw $LA/com.valor.kernel.plist
plutil -extract EnvironmentVariables.VALOR_OPERATOR_CHAT raw $LA/com.valor.kernel.telegram.plist
plutil -lint $LA/com.valor.*.plist | grep -v OK
git status -sb | head -1
$PY -c "from core import intake; print(intake.owned('telegram'))"
```

Check: eight lines ending `Valor the Cowboy`; the cutover date; `179144806`
(Tom's id); `-1003890616618`; `plutil -lint` prints no line that is not
`OK`; `## main...origin/main` with no `ahead`; the list holds
`-1003890616618`, `-1003449100931`, `179144806`, and `-5189826365`, and
neither of the Captain's groups.

## 8. Grants and the kernel

**8.1 Where merges land** (Tom's, item 2 of [cutover-runbook-tom.md](cutover-runbook-tom.md)). Each
project spec's `merge_url` and branch are recorded in a task's Brief at
start, and its merge lands there. The token's reach bounds the rest.

Check: on GitHub, the repositories `valorengels` can write are the four
day-one repositories and no others.

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
for J in routine.expiry routine.verify routine.conditions backup; do
  launchctl enable $D/com.valor.$J; launchctl bootstrap $D $LA/com.valor.$J.plist; done
sleep 10
```

`routine.emulator` stays booted out until task 3406a39ecec9 (the weekly
spend limit in code) merges, Tom's condition; then it is enabled the same way.

Check:

```
launchctl print $D/com.valor.kernel | grep -E "state =|pid ="
tail -n 5 ~/Library/Logs/valor/kernel.log | cut -c1-160
$PY -m core routines
$PY -m core settings | grep SETTING_MACHINE
```

Check: `state = running` with a pid; the log's last lines show no `serve
refused:`; the four routines (`conditions`, `emulator`, `expiry`,
`verify`) are listed; `SETTING_MACHINE` is read in this shell
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

**10.3 A request is bound and answered.** Only a sender whose id is
`VALOR_OPERATOR_TELEGRAM_ID` starts a task, and no stand-in account exists
that the bridge would take for Tom, so this is read on Tom's first request
after step 9 (10.5's task, or any other). Until it arrives, 10.2 shows the
bridge receiving.

```
$SQL "select id, type from events where type in ('message.received','message.bound','notice.sent') and at > '$(cat $CUT/cutover-at.txt)' order by id limit 6"
```

Check: a `message.received`, a `message.bound` (`start`), and a
`notice.sent` appear in order.

**10.4 The mailbox.**

```
tail -n 20 ~/Library/Logs/valor/email.log | cut -c1-160
$SQL "select count(*) from events where type='message.received' and payload->>'channel'='email'"
```

Check: the log shows the poll reaching the mailbox and no old backlog
received (every received message is dated on or after the cutover date).

**10.5 A task reaches a merge on each project's merge URL.** One small task
per project placed on the Cowboy (valor and popoto), started by a message
in its group. psyoptimal and cuttlefish wait for the Captain's own install
(0.2). The live window records the first run; at cutover one real task from
Tom's backlog is enough.

```
$PY -m core start "TEXT" --project popoto
$PY -m core status TASK_ID
```

Check: the task reaches `merge` and `merged` on the spec's URL and branch,
its merge effect has an `effect.outcome` of kind `done` and no
`effect.refused`, and nothing waited for Tom except a question the task
itself asked.

**10.6 The routines run and the backup runs.**

```
$PY -m core routines
launchctl kickstart $D/com.valor.backup; sleep 30; ls -t $VALOR_BACKUP_DIR | head -1
```

Check: each routine's last run is from its own latest scheduled time since
step 8: `verify` 02:00, `expiry` 04:00, and `conditions` 05:00 each day,
`emulator` Sunday 01:00. A routine whose time has not yet come since step 8
shows `never run` or an older run, and is read again after that time. A new
dump appears.

**10.7 Memory serves a later task.** After the tasks of 10.3 and 10.5 have
run a turn, memory has taken their rows, and a later task of the same
project (the spec's `repo`) is shown them.

```
$PY -m core memory ingest
$SQL "select count(*) filter (where payload->>'brief' like '%Memory: unavailable%'), count(*) filter (where payload->>'brief' like '%Memory: recall skipped%'), count(*) from events where type='turn.started' and at > '$(cat $CUT/cutover-at.txt)'"
```

Check: the ingest prints a row count and no error (a second run prints
`{"rows": 0, "records": 0}`); the first two numbers are 0, so no turn's
Brief says `Memory: unavailable` or `Memory: recall skipped`. A Brief with a Remembered section appears once a
second task of the same project has started after the first one's turn.

**Rolling the kernel after cutover.** The kernel rolls itself forward to its
own merges (`core/rollout.py`): a merge that changes `core/schema.sql` is
backed up first (to `VALOR_BACKUP_DIR`, which the kernel's job carries from
0.1), one that changes `uv.lock` or `pyproject.toml` runs `uv sync
--frozen`, and every restart migrates. It stops only when the checkout
holds an uncommitted `uv.lock`, `pyproject.toml`, or `core/schema.sql`
(step `uncommitted`) or the fast-forward would be refused; it is tried
again each wake, and the merge's notice names the step after three
failures. Commit or remove that edit in `~/src/valor-rebuild` and the next
wake rolls it.

## 11. The way back

[cutover-runbook-back.md](cutover-runbook-back.md), used if a check in steps 8 to 10 fails and
cannot be fixed in place, or when Tom says so.

## 12. Gaps, each with its step or why it is open

| Gap | Where it is handled |
|---|---|
| G1 other people's messages | Step 0.2 (left out of every spec; one operator by design) |
| G2 Tom's DM, G3 machine names | Step 0.2 (`machine` in every new spec) and step 7 (`telegram:179144806` and `machine` in `valor.toml`, `VALOR_MACHINE` in every job); checks in 0.2, 7, 8.3, 10.3 |
| G4 handover between bridges, G5 in-flight old work | Steps 2.2, 3.2, 4, 5.1; check in 10.2; open requests are re-asked by message |
| G6 branch is not `main`, G7 mailbox backlog | Step 6 and Tom's items 3 and 4; steps 4, 5.2, 7, check in 10.4 |
| G8 Memory and upkeep | Steps 0.1 and 10.7 (the new memory); step 2.1 exports the old records, which nothing imports yet; no upkeep routine is added until an incident names the need |
| G9 fields with no home, G10 third-party keys, G12 Google tokens | Left open on purpose: the spec carries only what the kernel acts on, a turn's sandbox denies the vault (one key is copied when a task needs it), the kernel does not read the Google files |
| G11 the other three Macs | Cowboy only. psyoptimal and cuttlefish are placed on the Captain and wait for its own install (step 0.2); the Bald and the Pirate stay open |
| Q3 | the operator group stays the operator chat (step 7 check) |

## Decided by default

Each is Valor's call, with its reason. The way back's are in
[cutover-runbook-back.md](cutover-runbook-back.md), the move of `main`'s in
[cutover-runbook-tom.md](cutover-runbook-tom.md).

1. **popoto on the Cowboy, psyoptimal and cuttlefish on the Captain** (0.2).
   Reason: one Mac per client (Tom, 2026-10-11); popoto is internal Yudame
   work like valor, and the Captain served the other two in the old fleet.
2. **`valor.toml`'s `machine` and `chats` change in step 7, not 0.2.**
   Reason: until step 7 the Cowboy's running kernel calls itself `Mac`, so
   an earlier edit moves the operator group off it.
3. **The memory store stays on the Cowboy's own Postgres** (0.1,
   `VALOR_MEMORY_DSN` unset). Reason: it is where the kernel's memory is
   today, and no second Mac reaches it before the Captain's install.
4. **The Cowboy's backups go to `/Volumes/PINK/valor_temp`**, by
   `VALOR_BACKUP_DIR` in the backup job and the kernel's job; the settings
   default is unchanged. Reason: PINK is the disk the handoff and B1 used,
   and the rollout's backup before a schema change runs in the kernel's job.

## Record, 2026-10-14

Output of every check: `~/src/valor-build-notes/cutover-2026-10-14/`. Sweep
stopped at 3.1 (ten arms done, nine held); 18 stale notices closed, 3 sent.
Step 4 10:44:03Z on Tom's yes, bridges up 10:46:46Z; at 6 `main` fast-forwarded
to a commit origin's `$REB` had past the checkout. 8.1 open (`valorengels`
writes far more than the four). 10.1, 10.2, 10.4, backup pass; 10.3 11:05Z
(`start`; question 11:40Z, late: an idle task held the turn slot, finding
filed). 10.5, 10.7, the routines: Tom's next tasks, nights.
