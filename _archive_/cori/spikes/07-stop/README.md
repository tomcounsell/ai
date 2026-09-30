# Spike 07: stop kills the compute and keeps the disk

**Question.** Tech stack §4 says a stop is a generation fence, a token revoke,
and a compute stop with the disk retained, and that "lossless" means durable
state only. Does apple/container deliver the compute half: how long from the
stop call until a background process is dead and an open request is gone,
and does the retained disk survive?

**Method.** `spike.py` drives the `container` CLI. Each run boots
`cori-base:3.14` on the `cori-hostonly` network with a temp directory bind
mounted at `/work`, then starts two detached processes inside: a shell loop
appending a counter to `/work/counter.txt` once a second, and a `curl` holding
a request open against a Python HTTP server on the Mac (bound to the vmnet
gateway address) whose handler writes a byte every 50 ms for 120 s, so the
server notices the moment the peer goes away. After the request is open and
the counter has ticked, the run issues `container stop -t 1` (series one) or
`container kill` (series two) and measures, on the Mac's clock: time until
the command returns, time until the server's write fails, and the last
modification of the counter file relative to the stop. Then `container rm`,
and a check that the temp directory still holds the counter file. Twenty
runs per series. A separate probe sends `kill -TERM 1` inside a container and
watches what survives. `results.json` holds the summary the script printed.

Both series ran while another session was booting and stopping its own
containers on the same machine, so the wall-clock numbers carry that noise;
the ordering between the series does not.

## Numbers

Twenty runs each, milliseconds after the stop call.

| | `stop -t 1` median | p95 | `kill` median | p95 |
|---|---|---|---|---|
| command returns | 2,807 | 5,543 (one 21,208 outlier) | 1,656 | 2,909 |
| open request dropped at the server | 1,380 | 2,711 | 265 | 700 |
| last counter write | 978 | 2,019 | 0 | 0 |
| counter grew after the call | 20 of 20 (by 1, three times by 2) | | 0 of 20 | |
| `counter.txt` on the retained disk | 20 of 20 | | 20 of 20 | |
| `rm` | 596 | 951 | 508 | 968 |

Per run, sorted:

- `stop` command return: 1.8 s to 5.5 s in nineteen runs, 21.2 s once.
- `stop` request drop: 1.23 s to 1.71 s in eighteen runs, 2.1 s and 2.7 s.
- `stop` last counter write: 0.94 s to 1.04 s in eighteen runs, 2.0 s twice.
- `kill` command return: 0.36 s to 2.9 s.
- `kill` request drop: 140 ms to 700 ms, median 265 ms.
- `kill` last counter write: 0 in every run, and the line count never
  changed after the call.

Every run in both series left `counter.txt` on the host directory with the
lines written before the stop, and the line count matched the seconds the
workload had run.

**SIGTERM to PID 1 alone does nothing.** With the container left running,
`kill -TERM 1` returned 0, the loop kept writing (3 lines to 6 in three
seconds), the `curl` stayed connected, and the process list three seconds
later still held the loop's `sh`, its current `sleep`, and `curl`. PID 1 in
this image is `sleep infinity` with no handler, so the signal is discarded.

## What the numbers say

- **`stop -t 1` pays its full second.** The grace period runs out before
  anything happens: the counter always wrote one more line about 0.95 s after
  the call, and the request dropped about 1.4 s after it. The VM then goes
  down and takes every process with it. Nothing inside handles SIGTERM, so
  the second is wasted on a signal that the workload discards.
- **`kill` stops execution before the next tick.** The counter never wrote
  again and the request was gone in a quarter of a second at the median.
  The command itself returns later than the compute stops (1.7 s median),
  so the adapter should measure a stop from the request drop or from a probe,
  not from the command's return.
- **The disk survives both.** Twenty of twenty in each series. The bind
  mount is host storage; the VM's death does not touch it. `rm` after the
  stop leaves it in place.
- **The open request resets at once.** From the server's side the drop is a
  `BrokenPipeError` on the next 50 ms write, never a lingering socket. The VM's
  network goes with the VM, and the Mac's TCP stack sees the reset within
  one write interval. A gateway with an in-flight stream to a stopped sandbox
  will see the same and can close the provider side immediately.

## Surprises

1. `kill` is the fast path and `stop -t 1` is a second slower for no gain,
   because nothing in the sandbox will ever honor SIGTERM. Spike 06 chose
   `stop -t 1` to get under the 5 s default grace; this spike moves the
   choice to `kill`.
2. One `stop` took 21 s to return while the compute stopped on the usual
   schedule (request dropped at 1.6 s). The command's return time and the
   compute stop are different events, and only the second matters for the
   protocol.
3. One `kill` in the first full attempt failed with an XPC "Connection
   interrupted" from the runtime while the container still went down. The
   script now records a command error rather than aborting; the rerun had
   none in forty. The adapter should treat a failed kill as "check the state,
   then retry", not as a live sandbox.
4. The counter on the bind mount is visible on the host as it is written,
   which is what made the last-write measurement possible without any
   cooperation from inside the sandbox.

## Recommendation

Holds. For the compute stop in tech stack §4 the adapter should call
`container kill` and then confirm from outside the VM: execution is over
within about 300 ms at the median and 700 ms at p95, the disk keeps every
byte written before the call, and an open connection resets at once.
`stop -t 1` is enough only in the sense that it also ends everything; it
spends a second on a signal the sandbox ignores and lets the workload take
one more step. Use `kill`, confirm with a probe rather than the command's
exit, and retry on a runtime error after checking the container's state.
