# Reflection Telegram Routing

How a reflection callable pages a human over Telegram, and to which chat.
`reflections/utilities.py` owns the one rule; five modules consume it instead
of each asserting a destination as a string literal (#3072).

## Two entry points

Recon (spike-2 of the #3072 plan) found the reflections split into two
distinct shapes, so there are two entry points rather than one:

| Entry point | For callers that... | Consumers |
|---|---|---|
| `send_eng_telegram(project, message, *, logger_prefix) -> bool` | already hold a project dict | `expectation_reconciler`, `sdlc_progress` |
| `send_host_eng_telegram(message, *, logger_prefix) -> bool` | have no project in scope — a fleet-wide digest | `sentry_triage`, `stall_advisory` |

`send_eng_telegram` takes the **dict**, not a `project_key`: its resolver,
`resolve_eng_group(project)`, scans `project["telegram"]["groups"]`, and
every call site already holds the dict. A key-based signature would force a
redundant `load_local_projects()` scan on every send.

`send_host_eng_telegram` wraps `resolve_host_eng_chat()` with no `repo_root`,
so it always resolves *this* checkout — the right behavior for a caller with
no audited repo in scope. `docs_auditor._send_telegram_notification` is
parameterized by `repo_root` instead (the point of #2754: it addresses
whichever repo it audited, including a foreign one) and keeps its own sender
rather than delegating to `send_host_eng_telegram` — folding the two would
re-add a `repo_root` knob that `sentry_triage`/`stall_advisory` must not have.

## The id-not-name rule, and why

`valor-telegram send --chat <dest>` accepts a group *name*, which re-enters
its ambiguity-tolerant `resolve_chat` cascade — two projects whose engineer
groups differ only in suffix could resolve to whichever the cascade prefers.
A numeric `chat_id` cannot be ambiguous. Every sender in this module
therefore resolves a project's `Eng:` group to `(group_name, chat_id)` via
`resolve_eng_group` and puts `str(chat_id)` in argv, never the name.

## `resolve_host_eng_chat` and its fallback

```
resolve_host_eng_chat(repo_root=None, *, load_projects=None, project_root=None) -> str | None
  1. Match repo_root (or PROJECT_ROOT) against a projects.json entry's working_directory.
  2. Matched -> resolve that project's Eng: group -> str(chat_id).
  3. No match, or no configured Eng: group -> FALLBACK_ENG_CHAT ("Eng: Valor")
     ONLY when the root is this very checkout. Never for a foreign or
     unregistered repo.
  4. Any exception is swallowed, logged, and falls through to rung 3.
```

**Do not "simplify" rung 3 into an unconditional default.** An unconditional
fallback re-creates the exact misroute this module exists to remove: paging
the valor engineers about a repo they don't own. This checkout's own
engineer group genuinely is `Eng: Valor`, so the fallback is provably
correct only in that one case. `FALLBACK_ENG_CHAT` is the sole surviving
bare `"Eng: Valor"` literal in the tree outside tests and prompt text — every
other site resolves a destination instead of asserting one.

`resolve_host_eng_chat` was lifted verbatim (ladder and reasoning both) from
`reflections/docs_auditor.py::_resolve_notify_chat` (#2754), generalized from
a required `repo_root` to defaulting on `reflections.utilities.PROJECT_ROOT`.
`docs_auditor._resolve_notify_chat` now delegates to it, passing its own
`load_local_projects` and `PROJECT_ROOT` bindings through explicitly — read
in a body that lives in `docs_auditor`, so a test that patches
`reflections.docs_auditor.load_local_projects` still lands on the value
actually used. `unittest.mock.patch` rebinds a name inside the target
module's `__dict__` only; it does not follow the object into another
module's globals, which is why an un-injected lift would have silently
broken that patch seam.

## Return contract

Both `send_eng_telegram` and `send_host_eng_telegram` publish the same
contract: `True` means a destination resolved and a send was *attempted* —
this covers a swallowed `FileNotFoundError`, a swallowed
`subprocess.TimeoutExpired`, any other swallowed transport exception, and a
non-zero `valor-telegram` exit code. `False` means, and only means, "nothing
resolved, nothing was sent" — covering both a `None` return from the
resolver and the resolver raising.

The two failure modes map to opposite outcomes and live in separate
try/except scopes: each sender owns its own resolution scope, then hands off
to a shared private transport, `_send_telegram_transport(chat, message, *,
logger_prefix)`, once resolution has already succeeded. A raising or
`None`-returning resolver means `False` with **no subprocess call**, decided
entirely in the sender before the shared transport is ever reached; a
transport failure after a destination resolved means `True`, decided inside
the one shared `subprocess.run` call both senders route through. A single
blanket handler spanning both calls would invert this — mapping a raising
resolver to `True` (or letting the exception escape).

## Suppression reaches the caller

An alert can now go nowhere: a project with no configured `Eng:` group gets
silence instead of a misrouted page. Both `expectation_reconciler` and
`sdlc_progress` thread a `False` return into their reflection's `findings`
(an `alert-suppressed: ...` entry) without incrementing their
`escalated` counter — so "no message arrived" stays legible in the
persisted `summary` instead of silently looking identical to a delivered
page. `docs_auditor` set this precedent in PR #3077.

## The five consumer modules

| Module | Sender | Destination |
|---|---|---|
| `reflections/expectation_reconciler.py` | `send_eng_telegram` | the orphaned expectation's own project |
| `reflections/sdlc_progress.py` | `send_eng_telegram` | the stalled lane's own project |
| `reflections/sentry_triage.py` | `send_host_eng_telegram` | this host checkout (digest spans every Sentry project) |
| `reflections/stall_advisory.py` | `send_host_eng_telegram` | this host checkout (classifies sessions globally) |
| `scripts/memory_consolidation.py` | `resolve_host_eng_chat` only, own transport | this host checkout |

`scripts/memory_consolidation.py::_flag_contradiction` is the one caller that
does **not** adopt a `send_*` wrapper: it has a stronger error contract than
either sender offers (`check=True` transport, routing `CalledProcessError` —
and any other exception — to a fallback log write so a contradiction survives
a bridge outage on disk), so only the *resolver* moved. Keying that log
write off `send_host_eng_telegram`'s return value would retire the
guarantee while looking like it preserved it, since the helper swallows
transport failures into `True` and returns `False` only for an unresolved
destination — for this caller, structurally unreachable (no `repo_root`, so
rung 3 of `resolve_host_eng_chat` always returns `FALLBACK_ENG_CHAT`). Its
own suppression branch (`resolve_host_eng_chat()` → `None`) skips the send
**and** the log write: nothing was attempted and nothing failed.

`reflections/docs_auditor.py::_send_telegram_notification` is the sixth
Telegram sender in the tree and is deliberately not in the table above — it
predates this module (#2754) and keeps its own transport for a reason
distinct from `memory_consolidation`'s: it is parameterized by `repo_root`,
addressing whichever repo it audited (including a foreign one), which the
two host-only callers must not have. `_resolve_notify_chat`, the resolver
half, does delegate to `resolve_host_eng_chat` — see
[Docs Auditor](docs-auditor.md).

## Out of scope

- `valor-telegram`'s `resolve_chat` name-ambiguity cascade — untouched.
- `bridge/routing.py` and `_resolve_persona`'s `Eng:` prefix convention —
  read to confirm the convention, not changed.
- Per-project `sentry_triage` digests and a per-project `stall_advisory` —
  both would be reflection redesigns, not routing fixes.
