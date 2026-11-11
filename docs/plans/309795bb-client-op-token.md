# A client's own 1Password service-account token for its turns

Bug-fix scale (Tom, valor team chat, 2026-11-11): one client's 1Password
service-account token must reach only that client's turns, on the Mac that
holds it, never iCloud. The file is already in place on that Mac.

## What is built

The same rule as `claude-token.<client>` beside `claude-token`
(`core/gateway.py` `_claude`): a project whose spec names a `client`, with
`op-token.<client>` in the kernel key directory (beside `claude-token`), has
that file's contents set as `OP_SERVICE_ACCOUNT_TOKEN` in each turn's
environment, in place of Valor's.

- Where: `core/__main__.py` `_turn_for`, which already adds per-turn,
  unrecorded variables to the harness env (`_reading_env`). A new
  `_client_env(b)` beside it: `Spec.load(b.project["name"]).client` (the
  Brief's `project` dict carries no `client`; loading the spec is what
  `_reading_env` already does), then reads `op-token.<client>` from
  `Path(settings.claude_token_file).parent` when it is a file. The harness env
  is applied last in `workspace.environment`, so it outranks the kernel's own
  `os.environ` and the repository's `.env`. Chat sessions and tasks both run
  through `_turn_for`, so both get it.
- Not written into the provisioned harness (`harness_env`), since that is
  stored in the ledger with the Brief; the token is read per turn and held
  only in the turn's process environment.
- Client match is `spec.client` only, as the gateway does (one mechanism);
  every project with that client (both of its specs) gets it.
- Docs: a row in `docs/machine.md`'s secrets table and the 1Password
  paragraph; `projects/README.md`'s `client` entry; `core/README.md`'s
  `_turn_for` line.

## Out of scope

Setup/suite/lint steps (`turn_environment`) and check sessions' own
environments; a command to write the file (Valor placed it by hand); any
refusal or check. No governance is added: this adds a credential to one
client's turns and removes nothing from anyone's.

## Tests (made-up client name; the repository is public)

In `tests/test_chat_history.py`'s `turn_env` style, with the key directory
under `tmp_path` and a spec loaded from a temporary projects dir:
1. A turn of a project whose client has `op-token.<client>` gets that value
   as `OP_SERVICE_ACCOUNT_TOKEN`, over one in the kernel's environment.
2. A turn of a project with another client (and one with no client) keeps
   the kernel's value and never the client's.
3. The client's project with no `op-token.<client>` file keeps the kernel's
   value.

Full suite, merge, roll out to the four Macs, then on the Captain the client's
turn's `op vault list` shows the client's two vaults (names not committed).
