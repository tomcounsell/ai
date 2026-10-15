---
tracking: none
slug: c19-sentry-route
type: build
status: waiting (rewritten after strip.md merges as "Valor's Sentry login in the turn")
critique_rounds: 0
review_rounds: 0
---

# C19: `sentry-cli` works in a turn on a project that uses Sentry

Stakes: a credential, critique 2 and review 2. From Tom's ruling of
2026-10-15 ([valor-rebuild-feedback.md](valor-rebuild-feedback.md),
"Tom's ruling on Sentry"). It adds no check, gate, hook, validator, round,
review step, refusal, or approval. It gives a turn read access to Sentry
the way the OpenAI route gives it a model: the credential stays with the
kernel.

## The problem

Cuttlefish and psyoptimal report to Sentry (`sentry-sdk` in their
dependencies; popoto lists it too, to be confirmed). A turn fixing a bug
in one cannot see its errors. A turn holds no credential
([docs/machine.md](../machine.md), Keychain), so the token cannot go in
its environment.

## What changes

1. **The key.** `python -m core sentry-key [--name NAME]` copies the
   vault's token (default `SENTRY_PERSONAL_TOKEN`, since reading issues
   needs `event:read`, which an organization token lacks; the build
   confirms the scope with one request) to `sentry-key` in the kernel key
   directory, mode 600, printing `written`, `kept`, or `missing`, never a
   value. A row in the machine.md Keychain table.
2. **A gateway route.** Tails starting `sentry/` go to
   `https://sentry.io/` with the kernel's token set and the turn's
   `authorization` dropped. `GET` and `HEAD` only (`read`); any other
   method is a 403 with a body that says so. Each call is recorded as the
   other routes' calls are, at no charge.
3. **The project spec.** `projects/*.toml` gains `sentry = "<org>/<project>"`.
   A task whose project names one gets `SENTRY_URL` pointing at the
   turn's gateway `sentry/` tail, a placeholder `SENTRY_AUTH_TOKEN`, and
   `SENTRY_ORG` and `SENTRY_PROJECT`. The build reads the slugs from
   Sentry's API with the key and writes them to `cuttlefish.toml`,
   `psyoptimal.toml`, and `popoto.toml` where each has a project.
4. **The binary.** `sentry-cli` is installed by the setup in
   `rebuild-handoff.md` and listed in `core/binaries.py`.

## Done

- A test: the route sends the kernel's token, never the turn's, and
  answers a `POST` with 403.
- A test: a task on a project with `sentry` gets the four variables; one
  without gets none.
- On the Pirate, a turn on cuttlefish runs `sentry-cli issues list` and
  its transcript shows the project's issues.

## Leaves out

Resolving, assigning, or commenting on issues (`act`), and a Sentry triage
routine. Each waits for its second need, as the expiry rule wants.
