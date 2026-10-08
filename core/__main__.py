"""Kernel command line: `python -m core <command>`.

migrate [--db NAME]            create the role, the database, the schema, and
                               correction 1, then secure-login
secure-login                   the kernel databases' password file, both roles'
                               passwords, and the pg_hba.conf rules; idempotent
settings                       every setting, as shell assignments
start INSTRUCTION [--ceiling C] [--workspace DIR] [--parent ID]
      [--model SEAT_OR_ID] [--harness-config FILE] [--target-branch B]
      [--by B] [--role-played] [--harness {claude_code,pi}]
                               start a task; prints its id. `--model` takes a
                               seat (frontier, reviewer, light) or a model id.
                               `--parent` starts a child of that task, its
                               ceiling the parent's unless `--ceiling` asks
                               for one at or below it (default without a
                               parent: propose).
                               The task starts in judge. The merge lands on
                               `--target-branch` (default: the branch origin's
                               HEAD names) at origin's URL as it is now
start INSTRUCTION --project NAME_OR_FILE [--branch B] [--base SHA] ...
                               provision the task's workspace from a project
                               spec (clone at the base, bare origin, kernel
                               mirror, its own Postgres and Redis, setup), then
                               start it; the merge lands on the spec's
                               merge_url when Tom granted the pair (never the
                               remote's default branch), else on its own origin
start ... --replay             background work: a foreground task's step takes
                               the turn slot first
routine NAME [--restart]       run a routine (routines/NAME/routine.toml): the
                               first run registers its objective; stops at once
                               when Tom stopped it; --restart begins a fresh one
routine NAME --plist           print the launchd job for it
routines                       every routine, its last run, and its spending
workspace show TASK_ID         where the kernel provisioned the task
workspace remove TASK_ID       delete a stopped or merged task's workspace and
                               free its ports
run TASK_ID                    run the task through the state machine until it
                               needs Tom or a stage with no runner; prints one
                               status line. The judge asks the judgement port;
                               a leg pointed at its default endpoint needs its
                               key in the kernel's key file, and a missing one
                               refuses the run, naming it
judgement-keys                 copy the judgement legs' keys from the vault
                               .env into the kernel's key file (mode 600);
                               prints each name with written, kept, or
                               missing, never a value
openai-key [--name NAME]       copy the vault's NAME (default OPENAI_API_KEY)
                               into the kernel's OpenAI key file (mode 600),
                               replacing the key there; prints written,
                               kept, or missing, never a value
github-key                     copy GITHUB_PUSH_TOKEN from the vault .env into
                               the kernel's github-keys file (mode 600), the
                               same way
merge-target add URL BRANCH --note TEXT
                               Tom's grant of a (URL, branch) pair a merge
                               may land on; https only; always his
merge-target remove URL BRANCH --note TEXT --by B
                               revoke a pair
merge-target list              the granted pairs
calibrate CASES.json
                               both judgement legs alone on every labelled
                               case (at most 50), one
                               calibration task, one judgement.calibrated
                               record on the judgement stream; prints it
answer TASK_ID TEXT [--by B] [--role-played]
                               the answer to the task's open question
feedback TASK_ID TEXT [--by B] [--role-played]
                               feedback on a delivery (in merge or merged); the
                               next run patches in the same session. `--by`
                               names who wrote it (default tom); `--role-played`
                               marks a stand-in speaking for Tom
verdict TASK_ID STAGE VERDICT [--finding KIND:TEXT]...
      [--governance PATH:LINE]... [--incident T]
      [--mission-item N] [--head SHA] [--by B] [--via V] [--role-played]
                               record by hand the verdict of a stage that has
                               no runner (docs), `leg: manual`; every other
                               stage has its runner
grant TASK_ID INSTANCE --note TEXT [--incident T] [--mission-item N] [--via V]
                               Tom's tap on one governance instance of the
                               delivery; always his, never role-played
status TASK_ID                 the task as a fold over its ledger: its state,
                               loops, candidate, checks, metered spending, and
                               the attention log (questions, answers, feedback,
                               approvals, manual verdicts, grants) and its counts;
                               its parent, its children's reports, and the
                               metered spending of its whole subtree; and
                               what came after each merge (feedback after it,
                               its paths, later merges sharing them, whether
                               it was reverted or is still on its branch,
                               used marks) and the used marks of deliveries
used TASK_ID --by B [--delivery EVENT_ID] [--note TEXT] [--via V] [--role-played]
                               record that someone used the task's work:
                               `--by` names who used it; the mark names the
                               delivery its latest merge carried (or the
                               latest delivery, with no merge, or
                               `--delivery`); a role-played mark is listed
                               and not counted as use
ledger TASK_ID                 every ledger row of the task
stop TASK_ID [--reason TEXT]   stop the task and every task under it now,
                               wherever their turns run
pending                        act-class effects held for Tom
approve EFFECT_ID --note TEXT [--by B] [--via V] [--role-played]
                               Tom's tap on one held effect
release EFFECT_ID              perform a held effect Tom approved (a send is
                               handed to its channel's bridge)
correct TEXT [--by] [--via]    record Tom's next correction (global, direct)
corrections                    every correction, in force for every turn
serve [--plist]                the resident kernel: binds Tom's messages, owes
                               notices, and runs every task's next step as
                               rows land, until killed; `--plist` prints its
                               launchd job instead
backup [--plist]               dump the kernel database to the backup disk and
                               keep the newest dumps; `--plist` prints the
                               launchd job instead
restore DUMP [--keep]          restore a dump into a scratch cluster and check
                               it against its manifest

This module is the composition root: `run`, `verdict`, `release`, and
`calibrate` wire the Claude Code harness, the judgement legs, the runners,
and the workspace performers into the kernel. Nothing else in `core/`
imports outside it.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import resource
import signal
from pathlib import Path

from core import (
    backup,
    broker,
    checks,
    corrections,
    credentials,
    db,
    fresh,
    git,
    guards,
    judgement,
    judgement_sites,
    ledger,
    machine,
    outcomes,
    router,
    routines,
    session,
    targets,
    tasks,
    workspace,
)
from core import verdicts as verdicts_
from core.machine import Check, State
from core.settings import (
    JEV_KEY,
    JEV_URL,
    OPEN_WEIGHT_KEY,
    OPEN_WEIGHT_URL,
    resolve_model,
    resolve_seat,
    settings,
)


def _performers(b: tasks.Brief) -> broker.Performers:
    """The task's own performers, built from its Brief, and every channel's
    declared send (`core/bridge.py`). push_branch goes to the task's own bare
    origin; the merge pushes to the Brief's origin URL, from the kernel mirror
    with the GitHub credential when the kernel provisioned the task, and from
    the workspace with none otherwise. A declared send sizes its files in the
    task's workspace."""
    from core.bridge import declared_performers
    from tools.push_branch import Merge, PushBranch

    kernel = []
    if b.workspace:
        kernel = [
            PushBranch(b.workspace, url=b.push_url or b.origin_url, protected=b.target_branch),
            Merge(
                b.mirror or b.workspace,
                url=b.origin_url,
                branch=b.target_branch,
                credential=settings.github_keyfile if b.mirror else None,
            ),
        ]
    return broker.Performers(*kernel, *declared_performers(b.workspace))


def _harnesses() -> dict:
    from harnesses import claude_code, pi

    return {"claude_code": claude_code, "pi": pi}


def _turn_for(prompt: str, resume: str | None, b: tasks.Brief):
    return _harnesses()[b.harness_name].workspace_turn(
        prompt, cwd=b.workspace, resume=resume, model=b.model, harness=b.harness
    )


def _fresh_for(prompt: str, checkout: str, model: str, harness: dict, harness_name: str = "claude_code"):
    return _harnesses()[harness_name].workspace_turn(prompt, cwd=checkout, model=model, harness=harness)


async def _working(ctx: router.Context) -> dict:
    return await session.run(
        ctx.gateway, ctx.task_id, _turn_for, dsn=ctx.dsn, alive=ctx.alive, performers=ctx.performers
    )


def port(keyfile: str | None = None) -> judgement.JudgementPort:
    """The judgement port with both legs. A leg at its default endpoint
    reads its key from the kernel's key file, and a missing key raises
    `credentials.MissingKey` naming it, so the run refuses to start; a leg
    pointed at a loopback endpoint (tests, the emulator's forced arms) gets
    a placeholder and needs no key; any other endpoint is refused."""
    from tools.jev import Jev
    from tools.open_weight import OpenWeight

    keyfile = keyfile or settings.judgement_keyfile
    jev_key = judgement.endpoint_key(
        settings.jev_url, JEV_URL, lambda: credentials.read_key(keyfile, JEV_KEY)
    )
    ow_key = judgement.endpoint_key(
        settings.open_weight_url, OPEN_WEIGHT_URL, lambda: credentials.read_key(keyfile, OPEN_WEIGHT_KEY)
    )
    return judgement.JudgementPort(
        {"jev": Jev(settings.jev_url, jev_key), "open_weight": OpenWeight(settings.open_weight_url, ow_key)}
    )


def runners(judgement_port: judgement.JudgementPort | None) -> dict:
    """The runner for each state and check this kernel can run.
    Review asks governance through `judgement_port`, as test asks breadth.
    Docs has none while governance's entry check fails: its verdict is
    recorded by hand (`verdict`)."""
    return {
        State.JUDGE: judgement_sites.judge_runner(judgement_port),
        State.CLARIFY: _working,
        State.PLAN: _working,
        State.BUILD: _working,
        State.PATCH: _working,
        State.CRITIQUE: fresh.critique_runner(_fresh_for),
        Check.TEST: checks.test_runner(judgement_port),
        Check.REVIEW: fresh.review_runner(_fresh_for, judgement_port),
    }


# The stages that have a runner, for the manual verdict's refusal.
RUNNERS: dict = runners(None)


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.4f}"


def _status_line(task_id: str, out: dict) -> str:
    state = out.get("state") or {}
    spent = f"metered spending: {_usd(state.get('spent_usd_micros', 0))}"
    status = out["status"]
    if status == "waiting":
        q = next(a for a in state["attention"] if a["kind"] == "question" and a["answer"] is None)
        return (
            f"QUESTION for Tom (task {task_id}, question {q['question_id']}; {spent}):\n\n{q['question']}\n\n"
            f'answer with: python -m core answer {task_id} "..."'
        )
    if status == "delivered":
        d = state.get("delivery") or {}
        lines = [
            f"DELIVERED, {d.get('outcome', 'delivered')} (task {task_id}; {spent}):\n\n{state['delivered']}"
        ]
        for f in d.get("findings") or []:
            lines.append(f"- [{f['source']}, {f['kind']}] {f['text']}")
        for i in [g for g in state.get("governance", []) if not g["granted"]]:
            lines.append(
                f"\ngovernance awaiting Tom: instance {i['id']} in {i['path']}\n"
                f'  grant with: python -m core grant {task_id} {i["id"]} --note "..."'
            )
        held = [e for e, s in state.get("effects", {}).items() if s == "pending"]
        for effect_id in held:
            lines.append(
                f"\nheld for Tom: effect {effect_id}\n"
                f'  approve with: python -m core approve {effect_id} --note "..."\n'
                f"  then:         python -m core release {effect_id}"
            )
        return "\n".join(lines)
    if status == "calibration task":
        return f"CALIBRATION TASK (task {task_id}): a calibration task runs no stage"
    if status == "no runner":
        missing = out["missing"]
        stage = state.get("state")
        how = "; ".join(f"python -m core verdict {task_id} {m} VERDICT" for m in missing)
        return f"NO RUNNER (task {task_id}, in {stage}; {spent}): no runner for {', '.join(missing)} yet; record by hand: {how}"
    turn = out.get("turn") or {}
    detail = {
        "stopped": "stopped",
        "merged": "merged",
        "legacy": "this task predates the state machine; it is read-only",
        "already running": "another run of this task is in progress",
        "lock lost": "the run's lock connection died; run again",
        "failed": f"the turn failed: {turn.get('result')}"
        + (f"; its stderr is in {turn['stderr']}" if turn.get("stderr") else "")
        + (
            f"; recorded as step.failed, row {state['failed_step']['row']}"
            if state.get("failed_step")
            else ""
        ),
    }[status]
    return f"{status.upper()} (task {task_id}; {spent}): {detail}"


async def _serve() -> None:
    from core import serve

    try:
        judgement_port = port()
    except (credentials.MissingKey, ValueError) as exc:
        raise SystemExit(f"serve refused: {exc}") from None
    await serve.serve(runners(judgement_port), _performers)


async def _run_task(task_id: str) -> str:
    from core.gateway import ClaudeLogin, Gateway, OpenAIKey

    async with await db.connect() as conn:
        await tasks.brief(conn, task_id)  # KeyError for an unknown task
    from harnesses import claude_code

    try:
        judgement_port = port()
    except (credentials.MissingKey, ValueError) as exc:
        raise SystemExit(f"run refused: {exc}") from None
    gateway = Gateway(credential=ClaudeLogin(), openai_credential=OpenAIKey())
    await gateway.start()
    try:
        out = await router.run(gateway, task_id, runners(judgement_port), performers=_performers)
    except claude_code.Unsandboxed as exc:
        raise SystemExit(f"task {task_id}: {exc}") from None
    finally:
        await gateway.close()
    return _status_line(task_id, out)


async def _provision(task_id: str, spec, ports: dict[str, int], base: str | None):
    """`workspace.provision` in `git.threaded`, with no time limit on its
    git calls or setup commands. An interrupt of `start` (Ctrl-C, or
    SIGTERM or SIGHUP of the kernel while it provisions) cancels it, which
    interrupts them, and the thread is waited for, so `provision:<task>` is
    held until its cleanup is done; a second signal or cancel during that
    cleanup waits for it too. The handlers stay until then: without them a
    second signal would end the kernel with the cleanup half done."""
    loop = asyncio.get_running_loop()
    me = asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, me.cancel)
    try:
        return await git.threaded(workspace.provision, task_id, spec, ports, base=base)
    finally:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(sig)


# What refuses a start, each answered `start refused:` with its reason.
START_REFUSED = (ValueError, tasks.UnknownParent, tasks.TaskStopped, tasks.CalibrationTask)


async def _start_task(conn, args, **fields) -> str:
    """Start a root, or with `--parent` a child (`tasks.start_child`).
    `--replay` marks it background work; `marker` is laid into `task.started`."""
    marker = fields.pop("marker", None)
    if getattr(args, "replay", False):
        fields["replay"] = True
    if args.parent:
        return await tasks.start_child(
            conn,
            args.parent,
            ceiling=args.ceiling,
            marker=marker,
            by=args.by,
            role_played=args.role_played,
            **fields,
        )
    brief = tasks.Brief(max_effect_class=args.ceiling or "propose", **fields)
    return await tasks.start(conn, brief, marker=marker, by=args.by, role_played=args.role_played)


async def _start_project(conn, args) -> str:
    """Provision the task's workspace from its project spec, then start it.
    The `workspace:ports` lock is held only while the ports are chosen and
    recorded in the task's directory; the provisioning itself (clone,
    cluster, setup) holds `provision:<task>`, which tells a sweep that the
    directory, with no task row yet, is not an orphan."""
    if args.workspace or args.harness_config or args.target_branch:
        raise SystemExit(
            "--project provisions the workspace; it takes no --workspace, --harness-config, or --target-branch"
        )
    try:
        spec = workspace.Spec.load(args.project)
        if args.branch:
            spec = dataclasses.replace(spec, branch=args.branch, target_branch=args.branch)
    except workspace.Refused as exc:
        raise SystemExit(f"start refused: {exc}") from None
    if spec.merge_url and (spec.target_branch or spec.branch):
        said = await targets.check(conn, spec.merge_url, spec.target_branch or spec.branch)
        if said:
            raise SystemExit(f"start refused: {said}")
    task_id = ledger.new_id()
    lock = f"provision:{task_id}"
    await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended('workspace:ports', 0))")
        try:
            taken = await workspace.taken_ports(conn)
            ports: dict[str, int] = {}
            if "postgres" in spec.services:
                ports["postgres"] = workspace.choose_port(settings.pg_ports, taken)
            if "redis" in spec.services:
                ports["redis"] = workspace.choose_port(settings.redis_ports, taken)
            workspace.reserve(task_id, ports)
        except workspace.Refused as exc:
            raise SystemExit(f"start refused: {exc}") from None
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended('workspace:ports', 0))")
        try:
            made = await _provision(task_id, spec, ports, args.base)
        except workspace.Refused as exc:
            raise SystemExit(f"start refused: {exc}") from None
        try:
            # The target branch is known here when the spec named none.
            said = await targets.check(conn, made.origin_url, made.target_branch)
            if said:
                raise ValueError(said)
            return await _start_task(
                conn,
                args,
                id=task_id,
                instruction=args.instruction,
                model=resolve_model(args.model),
                harness_name=args.harness or resolve_seat(args.model)[0],
                **made.brief_fields(),
                **getattr(args, "extra", {}),
            )
        except BaseException as exc:
            await git.threaded(workspace.remove, task_id)
            if isinstance(exc, START_REFUSED):
                raise SystemExit(f"start refused: {exc}") from None
            raise
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock,))


async def _routine_project(
    conn, *, parent, instruction, routine, ceiling, model, project, branch, marker
) -> str:
    """A routine run's task on a provisioned workspace: the same start as
    `start --project`, a child of the routine's objective."""
    args = argparse.Namespace(
        parent=parent,
        ceiling=ceiling,
        by="routine",
        role_played=False,
        project=project,
        branch=branch,
        base=None,
        workspace=None,
        harness_config=None,
        target_branch=None,
        instruction=instruction,
        model=model,
        harness=None,
        replay=False,
        extra={"routine": routine, "marker": marker},
    )
    return await _start_project(conn, args)


def _routine_runners() -> dict:
    from routines.emulator import runner as emulator

    return {routines.EXPIRY_RUNNER: routines.expiry_runner, "emulator": emulator.run}


async def _workspace(conn, args) -> str:
    """`workspace show TASK` prints where the kernel provisioned it;
    `workspace remove TASK` (Tom's) deletes it, only once the task is
    stopped or merged, and frees its ports. It also deletes the directory
    a provisioning that died left for a task with no workspace
    (`_unfinished`)."""
    try:
        b = await tasks.brief(conn, args.task_id)
    except KeyError:
        return await _orphan(conn, args)
    if not b.mirror:
        if args.workspace_command == "remove" and os.path.lexists(workspace.layout(args.task_id).root):
            return await _unfinished(conn, args)
        raise SystemExit(f"task {args.task_id} has no workspace the kernel provisioned")
    if args.workspace_command == "show":
        keys = (
            "workspace",
            "mirror",
            "push_url",
            "origin_url",
            "target_branch",
            "base_sha",
            "harness",
            "project",
        )
        return json.dumps({k: getattr(b, k) for k in keys}, indent=2)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{args.task_id}")
        f = machine.fold(await ledger.read(conn, args.task_id))
        if f.state not in (State.STOPPED, State.MERGED):
            raise SystemExit(
                f"task {args.task_id} is in {f.state}; only a stopped or merged task's workspace is removed"
            )
        await git.threaded(workspace.remove, args.task_id, workspace.Layout(Path(b.mirror).parent))
        await ledger.append(
            conn,
            args.task_id,
            "workspace.removed",
            {"path": str(Path(b.mirror).parent), "provenance": ledger.provenance(args.by, args.via, False)},
        )
    return f"removed the workspace of task {args.task_id}"


async def _unfinished(conn, args) -> str:
    """The directory of a task whose provisioning died before its
    `workspace.provisioned` row: removed once the task is stopped or
    merged, and its provisioning is not live, with what it left running."""
    lay = workspace.layout(args.task_id)
    key = f"provision:{args.task_id}"
    async with conn.transaction():
        await ledger.lock(conn, f"task:{args.task_id}")
        f = machine.fold(await ledger.read(conn, args.task_id))
        if f.state not in (State.STOPPED, State.MERGED):
            raise SystemExit(
                f"task {args.task_id} is in {f.state}; only a stopped or merged task's workspace is removed"
            )
        got = await (
            await conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        ).fetchone()
        if not got[0]:
            raise SystemExit(f"{lay.root} is being provisioned now")
        await git.threaded(workspace.remove, args.task_id, lay)
        await ledger.append(
            conn,
            args.task_id,
            "workspace.removed",
            {"path": str(lay.root), "provenance": ledger.provenance(args.by, args.via, False)},
        )
    return f"removed the unfinished workspace of task {args.task_id}"


async def _orphan(conn, args, *, after_lock=None) -> str:
    """A task directory with no task row: a provisioning that died before
    its start. `remove` deletes it once its provisioning is not live."""
    try:
        lay = workspace.layout(args.task_id)
    except workspace.Refused as exc:
        raise SystemExit(str(exc)) from None
    if not lay.root.is_dir():
        raise SystemExit(f"no task {args.task_id}")
    if args.workspace_command == "show":
        return json.dumps({"orphan": str(lay.root)}, indent=2)
    key = f"provision:{args.task_id}"
    got = await (
        await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
    ).fetchone()
    if not got[0]:
        raise SystemExit(f"{lay.root} is being provisioned now")
    try:
        if after_lock is not None:
            await after_lock()  # tests drive the window in which a task row can appear
        if await (
            await conn.execute("SELECT 1 FROM documents WHERE kind = 'task' AND id = %s", (args.task_id,))
        ).fetchone():
            raise SystemExit(f"{args.task_id} became a task; remove it as one once it is stopped or merged")
        await git.threaded(workspace.remove, args.task_id, lay)
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    return f"removed {lay.root}, which no task row names"


def _instance(spec: str, args) -> verdicts_.InstanceSpec:
    path, _, line = spec.rpartition(":")
    if not path or not line.isdigit():
        raise SystemExit(f"--governance takes PATH:LINE, not {spec!r}")
    return verdicts_.InstanceSpec(path, int(line), args.summary or "", args.incident, args.mission_item)


async def _verdict(conn, args) -> str:
    stage = verdicts_.MANUAL_STAGES.get(args.stage)
    if stage is None and args.stage in {k.value for k in RUNNERS}:
        raise SystemExit(f"{args.stage} has a runner; its verdict is the runner's to record")
    if stage is None:
        raise SystemExit(f"no manual verdict for {args.stage}; one of {', '.join(verdicts_.MANUAL_STAGES)}")
    verdicts_.manual_allowed(stage, RUNNERS)
    who = {"by": args.by, "via": args.via, "role_played": args.role_played}
    performers = _performers(await tasks.brief(conn, args.task_id))
    await verdicts_.record_check(
        conn,
        args.task_id,
        stage,
        args.verdict,
        findings=args.finding,
        governance=[_instance(g, args) for g in args.governance],
        **who,
    )
    await verdicts_.ensure_merge(conn, performers, args.task_id)
    state = await tasks.status(conn, args.task_id)
    return f"recorded {args.stage} {args.verdict}; task {args.task_id} is in {state['state']}"


async def _run(args) -> None:
    if args.command == "run":
        print(await _run_task(args.task_id))
        return
    if args.command == "serve":
        await _serve()
        return
    if args.command == "calibrate":
        try:
            judgement_sites.check_calibration(args.cases)
        except ValueError as exc:
            raise SystemExit(f"calibrate refused: {exc}") from None
        elsewhere = [
            url for url, default in ((settings.jev_url, JEV_URL), (settings.open_weight_url, OPEN_WEIGHT_URL))
            if url != default
        ]  # fmt: skip
        if elsewhere:
            # A record is what a judgement task lands on: only the providers make one.
            raise SystemExit(
                f"calibrate refused: a calibration asks the providers, not {', '.join(elsewhere)}"
            )
        try:
            judgement_port = port()
        except (credentials.MissingKey, ValueError) as exc:
            raise SystemExit(f"calibrate refused: {exc}") from None
        try:
            record = await judgement_sites.calibrate(judgement_port, settings.dsn(), args.cases)
        except ValueError as exc:
            raise SystemExit(f"calibrate refused: {exc}") from None
        print(json.dumps(record, indent=2))
        return
    if args.command == "routine":
        async with await db.connect() as conn:
            try:
                print(
                    await routines.run(
                        conn,
                        args.name,
                        _routine_runners(),
                        restart=args.restart,
                        start_project=_routine_project,
                        dsn=settings.dsn(),
                    )
                )
            except routines.Refused as exc:
                raise SystemExit(f"routine refused: {exc}") from None
        return
    async with await db.connect() as conn:
        if args.command == "routines":
            for rep in await routines.reports(conn):
                print(routines.listing(rep))
        elif args.command == "start" and args.project:
            print(await _start_project(conn, args))
        elif args.command == "start":
            harness = json.loads(Path(args.harness_config).read_text()) if args.harness_config else {}
            workspace = str(Path(args.workspace).resolve()) if args.workspace else None
            try:
                where = tasks.resolve_workspace(workspace, args.target_branch)
            except tasks.WorkspaceRefused as exc:
                raise SystemExit(str(exc)) from None
            try:
                started = await _start_task(
                    conn,
                    args,
                    instruction=args.instruction,
                    workspace=workspace,
                    model=resolve_model(args.model),
                    harness_name=args.harness or resolve_seat(args.model)[0],
                    harness=harness,
                    **where,
                )
            except START_REFUSED as exc:
                raise SystemExit(f"start refused: {exc}") from None
            print(started)
        elif args.command == "workspace":
            print(await _workspace(conn, args))
        elif args.command == "answer":
            try:
                question_id = await session.answer(
                    conn, args.task_id, args.text, by=args.by, role_played=args.role_played
                )
            except LookupError as exc:
                raise SystemExit(str(exc.args[0])) from None
            print(f"answered question {question_id}; continue with: python -m core run {args.task_id}")
        elif args.command == "feedback":
            try:
                feedback_id = await session.feedback(
                    conn, args.task_id, args.text, by=args.by, role_played=args.role_played
                )
            except LookupError as exc:
                raise SystemExit(str(exc.args[0])) from None
            print(f"feedback {feedback_id} recorded; continue with: python -m core run {args.task_id}")
        elif args.command == "verdict":
            try:
                print(await _verdict(conn, args))
            except (LookupError, ValueError) as exc:
                raise SystemExit(str(exc.args[0] if exc.args else exc)) from None
        elif args.command == "grant":
            try:
                guard_id = await guards.grant(
                    conn,
                    args.task_id,
                    args.instance,
                    note=args.note,
                    incident=args.incident,
                    mission_item=args.mission_item,
                    via=args.via,
                )
            except LookupError as exc:
                raise SystemExit(str(exc.args[0])) from None
            print(f"granted {args.instance} as {guard_id}; continue with: python -m core run {args.task_id}")
        elif args.command == "status":
            state = await tasks.status(conn, args.task_id)
            state["metered_spending"] = _usd(state["spent_usd_micros"])
            state["tree_metered_spending"] = _usd(state["tree_spent_usd_micros"])
            try:
                b = await tasks.brief(conn, args.task_id)
            except KeyError:
                raise SystemExit(f"no task {args.task_id}") from None
            state.update(await outcomes.after_merge(conn, b, await ledger.read(conn, args.task_id)))
            print(json.dumps(state, indent=2))
        elif args.command == "used":
            try:
                used_id = await outcomes.mark_used(
                    conn,
                    args.task_id,
                    by=args.by,
                    via=args.via,
                    role_played=args.role_played,
                    note=args.note,
                    delivery=args.delivery,
                )
            except LookupError as exc:
                raise SystemExit(str(exc.args[0])) from None
            print(f"used {used_id} recorded on task {args.task_id}")
        elif args.command == "ledger":
            print(ledger.render(await ledger.read(conn, args.task_id)))
        elif args.command == "stop":
            try:
                written = await tasks.stop_tree(conn, args.task_id, reason=args.reason)
            except tasks.CalibrationTask as exc:
                raise SystemExit(str(exc)) from None
            if not written:
                print("already stopped")
            else:
                print("stopped" + (f" (and {written - 1} descendants)" if written > 1 else ""))
        elif args.command == "pending":
            for effect in await broker.pending(conn):
                print(
                    f"{effect['effect_id']}  {effect['task_id']}  {effect['action_type']} -> {effect['target']}"
                    f"  {json.dumps(effect['payload'], sort_keys=True)}"
                )
        elif args.command == "approve":
            print(
                await broker.approve(
                    conn,
                    args.effect_id,
                    note=args.note,
                    by=args.by,
                    via=args.via,
                    role_played=args.role_played,
                )
            )
        elif args.command == "release":
            row = await (
                await conn.execute(
                    "SELECT task_id FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                    (args.effect_id,),
                )
            ).fetchone()
            if row is None:
                raise SystemExit(f"no held effect {args.effect_id}")
            performers = _performers(await tasks.brief(conn, row[0]))
            try:
                outcome = await broker.release(conn, performers, args.effect_id)
            except (broker.Refused, broker.NotApproved, tasks.TaskStopped) as exc:
                raise SystemExit(f"release refused: {exc}") from None
            print(
                f"{outcome.kind} {json.dumps(outcome.result, sort_keys=True)}"
                + (f" {outcome.error}" if outcome.error else "")
            )
        elif args.command == "merge-target":
            print(await _merge_target(conn, args))
        elif args.command == "correct":
            c = await corrections.record(conn, args.text, by=args.by, via=args.via)
            print(f"correction {c['number']} recorded, ledger row {c['event_id']}")
        elif args.command == "corrections":
            print(corrections.render(await corrections.in_force(conn)))


async def _merge_target(conn, args) -> str:
    """`add` (always Tom's), `remove` (anyone's), `list`."""
    if args.target_command == "list":
        rows = await targets.granted(conn)
        return "\n".join(f"{r['url']}  {r['branch']}  {r['note']}" for r in rows) or "no merge targets"
    if args.target_command == "add":
        if not targets.url_ok(args.url):
            raise SystemExit(
                f"merge-target refused: {args.url} is not https://host/path (letters, digits, . _ / -; "
                "no port, user, query, fragment, or dot segment)"
            )
        if not _branch_ok(args.branch):
            raise SystemExit(f"merge-target refused: {args.branch!r} is not a branch name")
        await targets.grant(conn, args.url, args.branch, args.note, via=args.via)
        return f"granted {args.url} {args.branch}"
    await targets.revoke(conn, args.url, args.branch, args.note, by=args.by, via=args.via)
    return f"revoked {args.url} {args.branch}"


def _branch_ok(branch: str) -> bool:
    import subprocess

    from core import git

    done = subprocess.run(
        [git.binary(), "check-ref-format", "--branch", branch],
        env=git.env(),
        capture_output=True,
        check=False,
    )
    return done.returncode == 0 and not branch.startswith("-")


def _secure_login() -> dict:
    return credentials.secure_login(
        host=settings.pghost,
        port=settings.pgport,
        passfile=settings.pg_passfile,
        databases=[settings.database, settings.test_database],
        owner=settings.owner_role,
        kernel_role=settings.kernel_role,
    )


def _sync(args) -> bool:
    """The commands that need no event loop. Returns whether it ran one."""
    if args.command == "migrate":
        print(db.migrate(args.db))
        print(json.dumps(_secure_login()))
    elif args.command == "secure-login":
        print(json.dumps(_secure_login()))
    elif args.command == "settings":
        print(settings.as_shell())
    elif args.command == "judgement-keys":
        try:
            status = credentials.copy_keys(
                settings.vault_env, settings.judgement_keyfile, [JEV_KEY, OPEN_WEIGHT_KEY]
            )
        except FileNotFoundError:
            raise SystemExit(f"no vault .env at {settings.vault_env}") from None
        for name, what in status.items():
            print(f"{name}: {what}")
    elif args.command == "openai-key":
        from core.gateway import OPENAI_KEY_NAME

        try:
            status = credentials.copy_keys(
                settings.vault_env, settings.openai_keyfile, [OPENAI_KEY_NAME], {OPENAI_KEY_NAME: args.name}
            )
        except FileNotFoundError:
            raise SystemExit(f"no vault .env at {settings.vault_env}") from None
        print(f"{OPENAI_KEY_NAME} (from {args.name}): {status[OPENAI_KEY_NAME]}")
    elif args.command == "github-key":
        try:
            status = credentials.copy_keys(
                settings.vault_env, settings.github_keyfile, [credentials.GITHUB_KEY]
            )
        except FileNotFoundError:
            raise SystemExit(f"no vault .env at {settings.vault_env}") from None
        for name, what in status.items():
            print(f"{name}: {what}")
    elif args.command == "backup":
        if args.plist:
            print(backup.plist().decode(), end="")
            return True
        try:
            path, manifest, pruned = backup.dump()
        except backup.BackupError as exc:
            raise SystemExit(f"backup failed: {exc}") from None
        print(
            f"dumped {manifest['events']['rows']} events (max id {manifest['events']['max_id']}), "
            f"{manifest['documents']['rows']} documents, {manifest['dump_bytes']} bytes to {path}; "
            f"pruned {len(pruned)} files"
        )
    elif args.command == "routine" and args.plist:
        try:
            print(routines.plist(routines.load(args.name)).decode(), end="")
        except routines.Refused as exc:
            raise SystemExit(f"routine refused: {exc}") from None
    elif args.command == "serve" and args.plist:
        from core import serve

        print(serve.plist().decode(), end="")
    elif args.command == "restore":
        try:
            print(json.dumps(backup.restore(args.dump, keep=args.keep)))
        except backup.BackupError as exc:
            raise SystemExit(f"restore failed: {exc}") from None
    else:
        return False
    return True


class _Parser(argparse.ArgumentParser):
    """No abbreviated options: a removed flag (`--mode`) must be an error,
    not a prefix of another (`--model`)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


OPEN_MAX = 10240


def _raise_open_files() -> None:
    """The soft open-file limit to the hard one, so a fan-out of judgement
    calls (a socket each) is not refused by launchd's soft 256. Where the
    hard limit is unlimited and the system refuses that, setrlimit(2)'s own
    rule: `min(OPEN_MAX, rlim_max)`, `OPEN_MAX` 10240 (`sys/syslimits.h`).
    Never lowered."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    for want in (hard, min(OPEN_MAX, hard)):
        if want <= soft:
            return
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            return
        except ValueError, OSError:
            continue


def main() -> None:
    _raise_open_files()
    parser = _Parser(
        prog="python -m core", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    sub.add_parser("migrate").add_argument("--db")
    start = sub.add_parser("start")
    start.add_argument("instruction")
    start.add_argument("--ceiling", choices=list(tasks.EFFECT_RANK))
    start.add_argument("--parent", help="start a child of this task, at or below its ceiling")
    start.add_argument("--workspace")
    start.add_argument("--model", default="light")
    start.add_argument("--harness", choices=("claude_code", "pi"))
    start.add_argument("--harness-config")
    start.add_argument("--target-branch")
    start.add_argument(
        "--project", help="a project spec's name (in projects_dir) or path: provision the workspace"
    )
    start.add_argument("--base", help="with --project: the base commit (default: the branch's head)")
    start.add_argument("--branch", help="with --project: the branch to start from and merge onto")
    start.add_argument(
        "--replay", action="store_true", help="background work: a foreground task's step goes first"
    )
    start.add_argument("--by", default="tom")
    start.add_argument("--role-played", action="store_true")
    sub.add_parser("run").add_argument("task_id")
    verdict = sub.add_parser("verdict")
    verdict.add_argument("task_id")
    verdict.add_argument("stage")
    verdict.add_argument("verdict")
    verdict.add_argument("--finding", action="append", default=[])
    verdict.add_argument("--governance", action="append", default=[])
    verdict.add_argument("--summary")
    verdict.add_argument("--incident")
    verdict.add_argument("--mission-item")
    verdict.add_argument("--by", default="tom")
    verdict.add_argument("--via", default="the command line")
    verdict.add_argument("--role-played", action="store_true")
    grant = sub.add_parser("grant")
    grant.add_argument("task_id")
    grant.add_argument("instance")
    grant.add_argument("--note", required=True)
    grant.add_argument("--incident")
    grant.add_argument("--mission-item")
    grant.add_argument("--via", default="the command line")
    for name in ("answer", "feedback"):
        reply = sub.add_parser(name)
        reply.add_argument("task_id")
        reply.add_argument("text")
        reply.add_argument("--by", default="tom")
        reply.add_argument("--role-played", action="store_true")
    ws_cmd = sub.add_parser("workspace").add_subparsers(dest="workspace_command", required=True)
    ws_cmd.add_parser("show").add_argument("task_id")
    remove = ws_cmd.add_parser("remove")
    remove.add_argument("task_id")
    remove.add_argument("--by", default="tom")
    remove.add_argument("--via", default="the command line")
    sub.add_parser("status").add_argument("task_id")
    used = sub.add_parser("used")
    used.add_argument("task_id")
    used.add_argument("--by", required=True, help="who used the work")
    used.add_argument("--delivery", type=int, help="the task.delivered row's event id")
    used.add_argument("--note")
    used.add_argument("--via", default="the command line")
    used.add_argument("--role-played", action="store_true")
    sub.add_parser("ledger").add_argument("task_id")
    stop = sub.add_parser("stop")
    stop.add_argument("task_id")
    stop.add_argument("--reason", default="stopped from the command line")
    sub.add_parser("pending")
    approve = sub.add_parser("approve")
    approve.add_argument("effect_id")
    approve.add_argument("--note", required=True)
    approve.add_argument("--by", default="tom")
    approve.add_argument("--via", default="the command line")
    approve.add_argument("--role-played", action="store_true")
    sub.add_parser("release").add_argument("effect_id")
    correct = sub.add_parser("correct")
    correct.add_argument("text")
    correct.add_argument("--by", default="tom")
    correct.add_argument("--via", default="the command line")
    sub.add_parser("corrections")
    sub.add_parser("secure-login")
    sub.add_parser("settings")
    sub.add_parser("judgement-keys")
    sub.add_parser("openai-key").add_argument("--name", default="OPENAI_API_KEY")
    sub.add_parser("github-key")
    target_cmd = sub.add_parser("merge-target").add_subparsers(dest="target_command", required=True)
    add_target = target_cmd.add_parser("add")
    add_target.add_argument("url")
    add_target.add_argument("branch")
    add_target.add_argument("--note", required=True)
    add_target.add_argument("--via", default="the command line")
    remove_target = target_cmd.add_parser("remove")
    remove_target.add_argument("url")
    remove_target.add_argument("branch")
    remove_target.add_argument("--note", required=True)
    remove_target.add_argument("--by", required=True)
    remove_target.add_argument("--via", default="the command line")
    target_cmd.add_parser("list")
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("cases")
    routine = sub.add_parser("routine")
    routine.add_argument("name")
    routine.add_argument("--plist", action="store_true", help="print the launchd job and exit")
    routine.add_argument("--restart", action="store_true", help="begin a fresh objective after a stop")
    sub.add_parser("routines")
    sub.add_parser("backup").add_argument("--plist", action="store_true")
    sub.add_parser("serve").add_argument("--plist", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("dump")
    restore.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if not _sync(args):
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
