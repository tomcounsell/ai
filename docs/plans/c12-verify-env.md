---
status: built
---

# C12: three bugs from the cutover sweep

Stakes 1. Findings 3, 4 and 8 of `d4-cutover-sweep-record.md`. Each fix is
code and a test of documented behavior; no check, gate, hook, guard or
validator is added.

## 1. `pg_config` missing

Evidence: pso-b's hidden tests did not run; its judge verification built
psycopg2 with `uv sync` and found no `pg_config`. The verification runs on
the Mac under the turn profile, with the PATH `harness_env` gives
(`bin`, git, `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`, `/opt/homebrew/bin`).
Homebrew links only `pg_config-18` there; `pg_config` is in
`settings.pg_bin`.

Fix: `harness_env` appends `settings.pg_bin` to PATH for a project with the
`postgres` service. A task provisioned before this keeps its recorded PATH.
The container is unaffected: `VM_PATH` holds `/usr/lib/postgresql/18/bin`
and the image has `libpq-dev`. cut-a's clarify failure was the same Mac
verification.

## 2. `CREATE EXTENSION vector` refused

`app` is `CREATEDB`, not a superuser, and `vector` is not trusted. Fix: a
project spec key `extensions` (names, identifier-shaped, postgres required).
`_init_postgres` has the superuser create each in `template1` before
`CREATE DATABASE app`, so database `app` and the test databases `app`
creates hold them. `app` gains no privilege. The key is carried in the
project copy, `spec_of`, the container spec, and the emulator item's
`project` key. The VM's `run.sh` does the same with `\connect template1`.

Image change (the lead rebuilds under the machine lock): `Containerfile`
installs `postgresql-18-pgvector=0.8.7-1.pgdg12+1` (pgdg bookworm, arm64,
the version Homebrew has); `run.sh` changed too, so the base digest changes.
Container confirmation: after rebuild, a `container`-marked run of a project
with `extensions = ["vector"]` and a migration that runs
`CREATE EXTENSION vector`; the host path is tested here with `file_fdw`
(also untrusted) in `test_an_extension_the_project_names_...`.

Items to change outside the repo: cut-a's item needs
`"project": {"extensions": ["vector"], ...}`; the lead owns the item files.

## 3. `hidden_tests` reads the pipe's tail

`judge.verify` runs each command as `bash -o pipefail -c`, so a pipeline
ending in `grep` reports the tests' failing exit. Test:
`test_a_verify_command_ending_in_a_pipe_reports_the_tests_own_exit`.

## Questions for Tom

None. Assumed: the extension list is the operator's project spec, so naming
one is Tom's decision, as roles are.
