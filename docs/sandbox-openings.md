# Sandbox openings

What the turn sandbox ([harnesses.md](harnesses.md), The turn sandbox) leaves
open, and which programs the kernel runs outside it.

Stated so the boundary is drawn where it is [4]:

- The turn runs as the machine's user. A deliberate keychain read through
  `security` is not fenced, though in a provisioned task Claude Code holds
  only a placeholder credential.
- The public internet is open, so a turn could reach a provider directly,
  outside the gateway, or reach GitHub anonymously. The baseline found no
  such call in any transcript (rebuild-baseline.md, Caveats).
- Both openings above are accepted (Tom, 2026-10-01): no separate macOS
  user, and the gateway is for visibility and honest metering, not a hard
  wall.
- A fresh session's blindness covers the paths the kernel names for the
  builder (Files, in [harnesses.md](harnesses.md)). The working session has its own `TMPDIR` but can
  still write `/tmp`, which every task shares (common file names recurred
  across items in the baseline), and places such as `~/Library/Caches`,
  `~/.cache`, or `/Users/Shared`, which a fresh session can read, so a builder
  could leave a note there for a reviewer. Nothing reads one on purpose;
  independence rests on the inputs, the checkout, and the reviewer's own
  reruns.
- Narrowed for kernel workspaces, not closed: their profiles deny writes to
  the machine user's startup places, `~/.local/bin` (which the user's PATH
  puts before `/usr/bin`), `~/Library/LaunchAgents`, the shell's rc files
  (`~/.zshrc` and the like), Claude Code's install (`~/.local/share/claude`)
  and state (`~/.claude`, `~/.claude.json`), git's global config, uv's cache
  (`~/.cache/uv`, from which the user's `uv sync` fills the kernel's own
  environment without rehashing what is there) and managed Pythons and tools
  (`~/.local/share/uv`, whose interpreters that `uv sync` runs; a fresh
  session gets its own uv cache and Python directory), Homebrew's prefix, and
  Pi's install and the node that runs it, with every symlink on the way to
  each and the ancestors of those (`workspace.pi_install`, `docs/pi.md`),
  and every turn runs with `DISABLE_AUTOUPDATER=1`. The demonstration's profile denies none of these,
  and other places remain where a turn could leave a program a later
  unsandboxed process of the user runs, such as the caches under
  `/var/folders` that Apple's `/usr/bin` shims read (`/usr/bin/git` is the
  `xcrun` shim, which finds the real git through one), and the directories
  on the user's PATH ahead of Homebrew (`~/.bun/bin`, `~/.opencode/bin`,
  `~/Library/Python/3.12/bin`), which Tom's own shell searches. The
  kernel's own interpreter comes from that search too: `uv sync`, run from
  Tom's shell, takes the first Python on PATH that fits the project when no
  managed one does (none does on this Mac), so a `python3.14` a turn left in
  one of those directories becomes the kernel's `.venv` interpreter, which
  runs outside the sandbox. Apart from that, the kernel runs nothing from
  that reach outside the sandbox and never looks a program up on PATH: its git is the Command Line Tools' install,
  its `ps` is `/bin/ps`, and the sandbox's own launcher is
  `/usr/bin/sandbox-exec`, each checked before it runs to be root's alone,
  file and every directory above it (`core/binaries.py`), with a PATH of
  system directories only. `claude` is `~/.local/bin/claude` (the `claude`
  setting) and Postgres's tools are `/opt/homebrew/opt/postgresql@18/bin`
  (`pg_bin`), both under write-denied directories; an override
  (`VALOR_CLAUDE`, `VALOR_PG_BIN`) must name a program inside a directory
  the profiles write-deny too, or a turn can replace it. The kernel runs
  `claude` only inside the sandbox; the one exception is
  `claude_code.turn`, a tool-less turn with no workspace that runs
  unsandboxed and only in tests (the router never builds one). Postgres's
  tools run outside it for `python -m core backup` and `restore`, and the
  scratch cluster `restore` makes sits in the kernel key directory (the
  `pg_scratch` setting), which no turn can read or write; a task's Postgres
  and Redis run only under `service.sb`.
- A turn can unmount a disk the user mounted, the backup disk included,
  since macOS allows a user's own unmount whatever the profile says. It
  cannot mount anything in the disk's place (The turn sandbox, Files, in
  [harnesses.md](harnesses.md)), so that costs the next backup its disk (the
  dump refuses a missing directory), not its contents.
- `launchservicesd` stays reachable from a turn, since Claude Code hangs
  without it; what keeps `open` from launching anything is the denied Launch
  Services database, quarantine resolver, and Apple events, not a deny of the
  service that launches.
- The demonstration's databases share one `test` role and password on
  5439, so a turn there could connect to another run's database. A task
  the kernel provisions has its own cluster, roles, and passwords, and its
  profiles reach only its own ports.
- sandbox-exec is marked deprecated by Apple. The plan names Apple
  containers for sandboxes; which one runs which work is
  `docs/architecture.md`'s (The turn sandbox and reaping). A container closes
  the keychain, internet, and `/tmp` openings by construction and costs RAM the 16 GB machine
  has to find.
