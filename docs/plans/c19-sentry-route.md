---
tracking: none
slug: c19-sentry-route
type: build
status: done (2026-10-16) as machine setup, no code
---

# C19: Valor's Sentry login in the turn

Rewritten after the strip, as strip.md said. A turn has the operator's
environment and home, so Sentry needs no kernel route: `sentry-cli` is
installed on the Cowboy and the Pirate, logged in as valor@yuda.me
(`~/.sentryclirc`, org `yudame`), and each project spec's `[env]` names its
`SENTRY_PROJECT` (tailspin, popoto, fabrikam, valor). Setup is step 6
of [rollout.md](../rollout.md).

Checked 2026-10-16: `sentry-cli issues list --project tailspin` lists
TAILSPIN-D and TAILSPIN-9 on the Pirate; `--project popoto` answers on
the Cowboy.
