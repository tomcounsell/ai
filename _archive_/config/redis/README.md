# Redis Replication + Sentinel Config Templates (Fix #5 — #1827)

This directory holds the **config templates** for Redis host-level failover:
replication, Sentinel monitoring, and the Option-A stable-address front. They
implement Fix #5 from the [Redis durability model](../../docs/features/redis-durability.md).

## Single-node default posture

These are **templates only**. Nothing here is active on a standard machine. The
default posture for this system is a **single-node localhost Redis** with the
#1814 durability floor (AOF + `noeviction`). Replication and Sentinel are an
**opt-in, operator-provisioned** layer that requires a second host (and ideally a
third witness host). Until an operator provisions those hosts, substitutes the
placeholders, and starts the processes, this directory is inert.

A machine opts in to being a Redis node by touching the marker file
`data/redis-replication-enabled` (mirroring `data/auto-revert-enabled`). Only then
does the `/update` propagation step (`scripts/update/redis_replication.py`) and the
`redis-replication-health` doctor check act on it. On every other machine both are
a clean no-op.

## Templates

| File | Purpose |
|------|---------|
| `redis-replica.conf.template` | Replica directives (`replicaof`, `replica-read-only`, AOF + `noeviction` inherited from #1814). Start the second host with this to make it a live replica. |
| `sentinel.conf.template` | Sentinel monitor/quorum/timeout directives. Run >= 3 of these on 3 machines (`redis-sentinel sentinel.conf`). |
| `haproxy-redis.cfg.template` | HAProxy TCP frontend that always routes to the node reporting `role:master`. `REDIS_URL` points at its fixed bind so promotion is transparent. The keepalived-VIP alternative is documented inline. |

## Placeholder tokens

Every template uses `<UPPERCASE>` placeholder tokens — **no live host values are
committed**. Substitute them for your topology before use:

| Token | Meaning |
|-------|---------|
| `<PRIMARY_HOST>` | Hostname/IP of the current primary Redis (the master). |
| `<PRIMARY_PORT>` | Port of the primary Redis (default `6379`). |
| `<REPLICA_HOST>` | Hostname/IP of the replica Redis node. |
| `<MASTER_NAME>` | Logical name Sentinel uses for the monitored master (e.g. `valor-redis`). |
| `<QUORUM>` | Number of Sentinels that must agree the master is down (production: `2`). |
| `<BIND_HOST>` / `<BIND_PORT>` | Address/port the HAProxy frontend listens on — what `REDIS_URL` targets. |

### Substituting placeholders

Substitution is a manual operator step (the runbook spells it out). For example:

```bash
sed \
  -e 's/<PRIMARY_HOST>/10.0.0.1/' \
  -e 's/<PRIMARY_PORT>/6379/' \
  config/redis/redis-replica.conf.template > /etc/redis/redis-replica.conf
```

## Where to go next

The full topology diagram, async-replication RPO/RTO model, and the
step-by-step **Operational Runbook: Failover** live in
[`docs/features/redis-durability.md`](../../docs/features/redis-durability.md)
under "Replication + Sentinel Failover (Fix #5)".
