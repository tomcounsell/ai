# The workspace app role's privileges in its own cluster

Task 234852e586f4, a kernel repair. Incident: on 2026-10-07 task
6fd4e0439ac1's test check went red on
`tests.test_expiry::test_a_guard_that_fired_only_on_a_background_task_is_due_at_expiry`,
which errored at setup with `InsufficientPrivilege: permission denied to
terminate process` from `DROP DATABASE IF EXISTS ... WITH (FORCE)`
(`core/db.py:52`); its candidate changed one Markdown file.

## What was found

Probed in this task's own cluster (Postgres 18.6, role `app`):

- `app` is `CREATEDB CREATEROLE`, an inheriting member of `valor_kernel`,
  and a member of no predefined role.
- `DROP DATABASE ... WITH (FORCE)` ends every other backend on the
  database, and refuses unless the caller has the privileges of that
  backend's role or of `pg_signal_backend`. `app` has neither for an
  autovacuum worker (no role) or for a role `app` itself created
  (Postgres 16+ gives the creator ADMIN without INHERIT). Both come and go,
  so the fixture fails at random. Granting `pg_signal_backend` fixes it;
  it still cannot signal a superuser backend, and no superuser can log in
  once provisioning ends (`ALTER ROLE postgres PASSWORD NULL`).
- `SHOW data_directory` (`core/backup.py:151`) and `SHOW hba_file`
  (`core/credentials.py:171`, the credentials test) need
  `pg_read_all_settings`. Reproduced here: the backup test fails on
  `data_directory`, the credentials test on `pg_authid`.
- Past the privileges, both tests read the cluster's data directory from
  the suite's process: the backup test `stat`s it (`backup._check_disks`),
  the credentials test reads `pg_hba.conf` and `pg_authid` (superuser only).
  The turn and check profiles deny the task's `pg/` directory, and
  `tests/test_demo_sandbox.py` asserts that denial (a turn never reads its
  cluster's `pg_hba.conf`). Here `stat` on `pg/data` returns
  `Operation not permitted`. No role grant gets past that, and the
  credentials test's own comment states its premise: the machine cluster's
  owner, a superuser on the Mac.

## What will be built

1. **Provisioning grants, in both paths.** After `CREATE ROLE app`:
   `GRANT pg_signal_backend, pg_read_all_settings TO app`. In
   `workspace._init_postgres` (host, also the clusters checks get, via
   `workspace.py:2006`) and in `core/images/base/run.sh` (the VM). Nothing
   else: not `pg_read_server_files`, `pg_write_server_files`,
   `pg_execute_server_program` (each reads or runs files as the server's OS
   user, an escape from the turn sandbox), and no `SELECT` on `pg_authid`
   (it would not get the credentials test past the file read).
2. **The two sandbox-blocked tests are skipped where the suite cannot see
   its cluster's data directory**, through the existing
   `tests/denials.py` mechanism, its up-front form (`denials.met`), with
   the reason "the sandbox the suite runs under denies the cluster's data
   directory". The probe connects as the owner, reads `SHOW
   data_directory`, and `stat`s it: `PermissionError` is met; anything else
   (a privilege error included) is not met, so a missing grant still fails
   loudly. Applied to
   `test_backup::test_a_backup_directory_on_the_clusters_own_disk_is_refused`
   and `test_credentials::test_migrate_touches_no_credential_on_the_machine_cluster`.
   On the host the probe never meets, and both run as today.
   This is a reported skip in the suite's existing denial list, not a new
   check or gate: it refuses nothing and adds no step.
3. **Docs**: wherever the workspace's `app` role is described
   (`docs/data.md`, `docs/plans/m1-4a-provisioning.md`, the
   `_init_postgres` docstring), add the two grants and why.

## Out of scope

- Workspaces provisioned before this lands (this one, 6fd4e0439ac1's)
  keep the old role: once provisioning ends nobody can authenticate as a
  superuser to grant afterwards. Checks are unaffected, since each check
  provisions a fresh cluster. Re-provisioning old workspaces is not done.
- No change to any sandbox profile, the backup code, or `db.migrate`.
- `core/db.py`'s `WITH (FORCE)` stays; it is correct once the role may use it.

## Tests

In `tests/test_workspace.py`, `macos`, beside
`test_the_task_cluster_takes_passwords_only_and_the_app_role_cannot_escape`:

- **The incident**: provision with `postgres` and `roles=["valor_kernel"]`,
  start the services; as `app`, create `app_test` and a login role
  `holder`; a separate process (a Python subprocess) holds a connection to
  `app_test` as `holder`; `DROP DATABASE app_test WITH (FORCE)` as `app`
  succeeds, the database is gone, and the holder's connection is ended.
  Run at base first to show it fails there with the incident's error.
- **Same, with a spec that has no `roles`** (no `CREATEROLE`): the grant
  does not depend on the roles branch; the holder is a second `app`
  connection from the subprocess plus a direct `pg_terminate_backend` as
  the boundary case.
- **The boundary**: `app` can `SHOW data_directory`; `app` is a member of
  none of `pg_read_server_files`, `pg_write_server_files`,
  `pg_execute_server_program`; the existing escape assertions (`COPY TO
  PROGRAM`, `CREATE ROLE ... SUPERUSER`) still hold.
- **The VM path**: if `tests/test_container.py` has a provisioning test
  that runs `run.sh`'s role SQL, extend it with the membership check;
  otherwise the VM path is named unverified in the delivery.
- **The denial probe**: a unit test that `denials` reports met only on
  `PermissionError` from the `stat`, and not met on a privilege error.

Suites: `tests/test_workspace.py`, `tests/test_backup.py`,
`tests/test_credentials.py`, `tests/test_demo_sandbox.py`,
`tests/test_expiry.py` here, then the full suite; ruff. Inside this
workspace the new provisioning tests make a cluster of their own, so they
exercise the new grants even though this workspace's own role predates
them. Failures are split into pre-existing (same at base) and new.

## Stakes and rounds

Stakes: a wrong grant widens what a task's code can do inside its own
cluster, close to the sandbox's edge, and a missed one leaves checks red
for reasons outside the candidate; both are reversible, but this is the
kernel's provisioning. Critique rounds: 1. Review rounds: 2.
