# Spike 06: apple/container as the sandbox adapter

**Question.** Does apple/container on this Mac give the six operations of
the Sandbox port (tech stack §6), the three profiles, and a network
restriction enforced from outside the VM, and what do boot and exec cost?

**Method.** `brew install container` (1.4.1) on macOS 26.6, kernel from
kata 3.32.0 installed by `container system start --enable-kernel-install`.
Base image `infra/sandbox/base.Dockerfile`: `python:3.14-slim-bookworm`
plus git, curl, and uv 0.9.30 copied from the astral image, running as uid
1000. Two vmnet networks: `cori-hostonly` (`--internal`, host-only) and
`cori-egress` (NAT). `spike.py` drives the CLI with `subprocess` and times
each call from the host, so every number includes CLI and XPC overhead,
which is what the adapter will pay too. `run.sh` sets everything up.

## Numbers

Three runs on the same machine, cold image cache on the first.

| Operation | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `run -d` returns (VM booted) | 995 ms | 996 ms | 2,123 ms |
| first `exec` answers after boot | 74 ms | 75 ms | 76 ms |
| `exec true` round trip, median of 20 | 65 ms | 75 ms | 110 ms |
| `exec true` p95 | 78 ms | 82 ms | 129 ms |
| write one file (`cp` host to container) | 40 ms | 47 ms | 43 ms |
| read one file (`cp` container to host) | 29 ms | 31 ms | 30 ms |
| snapshot (`export` full rootfs, 285 MB tar) | 14,604 ms | 2,434 ms | 4,733 ms |
| destroy (`stop -t 1` + `rm`) | 6,544 ms (default grace) | 5,477 ms (default grace) | 1,505 ms |

Boot to first usable exec is about 1.1 s. An exec costs 65 to 110 ms,
which is the floor for one tool call from the PydanticAI loop.

## Network

| From | To | Result |
|---|---|---|
| host-only | the Mac (HTTP server bound to the vmnet gateway address) | 200 |
| host-only | pypi.org | blocked, curl timeout |
| host-only | 1.1.1.1 | blocked, curl timeout |
| NAT | pypi.org | 200 |

The restriction is the vmnet network's mode, chosen at `network create` on
the host and applied at `run --network`. Nothing inside the VM can change
it. A host-only network gives exactly one reachable host, the Mac, which
is where the gateway runs. That is the `worktree` profile's "gateway"
leg. The "allowlist" leg (a package registry) is a second host, which
host-only does not give: it needs a proxy on the Mac or a `pf` rule on the
bridge interface (root). The proxy is the smaller change, since the
gateway already is one.

## Profiles

| Profile | How | Checked |
|---|---|---|
| `scratch` | host-only network, `--mount type=bind,...,readonly` of the space slice | reads the slice, write to it refused, can write `/tmp` |
| `worktree` | host-only network, writable bind mount keyed by node id | a write inside the VM is visible on the host |
| `verify` | a new container from the same image, read-only mount of the artifact | the Executor's `/tmp/proto.txt` is absent, the artifact is readable, writes refused |

All six booleans in `spike.py` hold.

## Surprises

1. **The builder VM requires Rosetta** even for an arm64-only build.
   `build.rosetta = true` is a system property with no CLI to set it in
   1.4.1, so `softwareupdate --install-rosetta` was the fix.
2. **The host's bridge interface exists only while a container is
   attached** to the network. A server bound to the gateway address before
   the first container boots fails with `EADDRNOTAVAIL`, and the first run
   after a destroy on the same network saw one probe time out before the
   bridge settled. The adapter should bind after boot and tolerate one
   retry.
3. **`sleep infinity` as PID 1 ignores SIGTERM**, so `stop` pays the whole
   5 s grace. `stop -t 1` brings destroy to 1.5 s; the adapter has no
   reason to be gentle with a sandbox.
4. **Snapshot of a whole rootfs is 285 MB and 2.4 to 14.6 s.** For the
   `worktree` profile the deliverable lives in the bind mount, so the
   snapshot the kernel keeps should be of the mount, taken on the host,
   with `export` reserved for forensics.
5. **`/work` must be chowned to the sandbox user in the image.** `WORKDIR`
   before `USER` creates it as root and the first write fails.

## Recommendation

Holds. apple/container gives the port's six operations from the CLI with
about 1 s boot and under 100 ms per exec. Write the adapter against the
CLI (the XPC API has no Python binding), use host-only networks for every
profile, run the registry allowlist as a proxy on the Mac beside the
gateway, snapshot the mount rather than the VM, and kill rather than stop.
