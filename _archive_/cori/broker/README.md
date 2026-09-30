# Broker credentials

The broker holds the person's own tokens, few by design (tech stack §7).
Every one lives in the macOS Keychain under service `cori`.

## The table

`broker/credentials.py` keys credentials by the pair of space and target,
never by the target alone: a credential that belongs to a target alone is a
credential every space can reach (blind-spot finding 9). A pair with no row
is a refusal, which covers both an unknown space and a space reaching a
target that belongs to another. The table is code rather than a manifest
field, so adding a row is a reviewed change under `.github/CODEOWNERS`.

| Space | Target | Keychain names | Used by |
|---|---|---|---|
| `psyoptimal` | `github.com/yudame` | `github_token` | `broker/push_branch.py` |
| `psyoptimal` | `mailto:tom@yuda.me` | `google_oauth_client_id`, `google_oauth_client_secret`, `gmail_refresh_token` | `broker/gmail.py` |

A row names Keychain entries and never holds a value. The values are read at
use through `infra.secrets.read_secret`, so no secret can reach a traceback,
a ledger row, a log line, or a Brief.

## GitHub: `github_token` (prereqs item 13)

Fine-grained personal access token at
https://github.com/settings/personal-access-tokens/new:

- Resource owner: `yudame`
- Repository access: only `yudame/psyoptimal` and `yudame/cori-sandbox`
- Permissions: Contents read and write; Pull requests read and write;
  Metadata read (added automatically)

Then:

    security add-generic-password -s cori -a github_token -w '<token>' -U
    uv run pytest -q tests/test_push_branch.py

`broker/push_branch.py` pushes only to branches under `cori/` and refuses
`main`, `master`, and anything else before touching the network.

## Gmail: `google_oauth_client_id`, `google_oauth_client_secret`, `gmail_refresh_token` (prereqs item 12)

One-time, in the Google Cloud console (https://console.cloud.google.com):

1. Create a project, say `cori`.
2. APIs and Services > Library > enable the Gmail API.
3. APIs and Services > OAuth consent screen: Internal, under the
   `yuda.me` Workspace organization, app name `cori`, your address as the
   contact. Internal means every user of the organization is already
   allowed, so there is no test-user list and the refresh token has no
   seven-day expiry (prereqs item 12).
4. Credentials > Create credentials > OAuth client ID > Desktop app. Copy
   the client id and secret.

Then:

    security add-generic-password -s cori -a google_oauth_client_id -w '<id>' -U
    security add-generic-password -s cori -a google_oauth_client_secret -w '<secret>' -U
    uv run python -m broker.gmail authorize      # consent in the browser as tom@yuda.me
    uv run python -m broker.gmail recent psyoptimal.com
    uv run pytest -q tests/test_gmail.py

`authorize` asks for `gmail.readonly` only and stores the refresh token as
`gmail_refresh_token`. The read is scoped by the space's routing rule,
`from:@psyoptimal.com`.

## Reconcile after a crash

A kill between the intent row and the closing row leaves an effect whose
state only the target knows. The reconcile pass asks each target by
idempotency key and closes every open intent:

    uv run python -m broker reconcile

One line per close, naming the kind and the effect id, then the count. The
kinds are `recovered` when the target holds the key, `done` when it does not
and the re-run landed, `failed` when the re-run raised, and `unknown` when
the target could not be asked or holds the key in another state.

`kernel/__main__.py` runs the same pass at process start, before any
replacement Brief runs, and issues one `unknown_outcome` card per `unknown`.
The command above is that pass by hand, for reading the ledger after a crash
without starting the kernel. It issues no card.
