"""A replay's workspace, provisioned by the kernel (`core/workspace.py`):
this script fetches the repository into a cache of its own (through `gh`
for a GitHub repository, as Tom) and writes the project spec the kernel
provisions the task from. The kernel then makes the clone at the base, the
bare origin, the kernel mirror, a Postgres cluster and Redis of the task's
own (password auth, its own ports), and the sandbox profiles, under the
`work_dir` setting.

    .venv/bin/python scripts/replay_workspace.py OWNER/NAME BASE_SHA RUN_NAME \
        [--services postgres,redis | none] [--rebuild]
    .venv/bin/python scripts/replay_workspace.py --teardown RUN_NAME

OWNER/NAME may also be a local git repository's path (the smoke's toy repo).

Layout under the `demo_dir` setting (`core/settings.py`, override $VALOR_DEMO):

    cache/<owner>__<name>.git   bare clone from GitHub, shared by every run of
                                the repository; fetched by SHA when a base is
                                missing. A turn cannot read it.
    runs/<run>/project.toml     the spec the kernel provisions the task from
    runs/<run>/replay.json      what was built, and once started, the task
                                and where the kernel put its workspace
    runs/<run>/harness.json     the task's harness settings, for the judge

`teardown` stops the run's task, removes its workspace through the kernel
(`python -m core workspace remove`, which stops its Postgres and Redis and
frees their ports), and deletes the run directory.

Live spend: none. Network: a clone or fetch from GitHub, run as Tom.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_common import DEMO, git, now, ok, sh

SERVICES = ("postgres", "redis")
ROOT = Path(__file__).resolve().parent.parent


# -- the cache -----------------------------------------------------------------


def _cache(repo: str, base: str) -> Path:
    """A bare clone of `repo` holding `base`, shared by every run of it."""
    local = Path(repo).expanduser()
    name = local.resolve().name.removesuffix(".git") if local.exists() else repo.replace("/", "__")
    cache = DEMO / "cache" / f"{name}.git"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        if local.exists():
            sh("git", "clone", "--quiet", "--bare", str(local.resolve()), str(cache))
        else:
            sh("gh", "repo", "clone", repo, str(cache), "--", "--quiet", "--bare")
    if not _has(cache, base):
        if local.exists():
            git(cache, "fetch", "--quiet", str(local.resolve()), "+refs/heads/*:refs/heads/*")
        else:
            git(
                cache,
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "fetch",
                "--quiet",
                f"https://github.com/{repo}.git",
                base,
            )
    if not _has(cache, base):
        raise SystemExit(f"{base} is not a commit of {repo}")
    return cache


def _has(repo: Path, sha: str) -> bool:
    return ok("git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}")


def build(
    repo: str,
    base: str,
    run_name: str,
    services: list[str],
    *,
    max_output_tokens: int | None = None,
    rebuild: bool = False,
    kind: str = "plain",
    setup: list[str] | None = None,
    suite: str = "true",
    env: dict[str, str] | None = None,
) -> dict:
    """The run's cache and project spec; the kernel provisions the rest at
    `core start --project`. `kind`, `setup`, `suite`, and `env` come from
    the item's `project` key; without one the suite is `true`, so a delivery
    says the suite was not run. Returns its replay.json."""
    unknown = set(services) - set(SERVICES)
    if unknown:
        raise SystemExit(f"unknown services {sorted(unknown)}; known: {', '.join(SERVICES)}")
    run = DEMO / "runs" / run_name
    if rebuild:
        teardown(run_name)
    cache = _cache(repo, base)
    base = git(cache, "rev-parse", f"{base}^{{commit}}")
    run.mkdir(parents=True, exist_ok=True)
    spec = run / "project.toml"
    lines = [
        f"name = {json.dumps(run_name)}",
        f"repo = {json.dumps(str(cache))}",
        f"kind = {json.dumps(kind)}",
        f"suite = {json.dumps(suite)}",
        f"services = {json.dumps(services)}",
        'target_branch = "main"',
    ]
    if setup:
        lines.append(f"setup = {json.dumps(list(setup))}")
    if env:
        lines.append("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }")
    if max_output_tokens:
        lines.append(f"max_output_tokens = {int(max_output_tokens)}")
    spec.write_text("\n".join(lines) + "\n")
    replay_json = run / "replay.json"
    info = json.loads(replay_json.read_text()) if replay_json.exists() else {}
    info.update(
        {
            "run": run_name,
            "repo": repo,
            "base": base,
            "services": services,
            "run_dir": str(run),
            "spec": str(spec),
            "built_at": info.get("built_at") or now(),
        }
    )
    replay_json.write_text(json.dumps(info, indent=2) + "\n")
    return info


def attach(info: dict, shown: dict) -> dict:
    """Where the kernel put the run's task (`core workspace show`), in the
    fields the driver and the judge read."""
    run = Path(info["run_dir"])
    harness = run / "harness.json"
    harness.write_text(json.dumps(shown["harness"], indent=2) + "\n")
    info.update(
        {
            "workdir": shown["workspace"],
            "origin": shown["push_url"],
            "mirror": shown["mirror"],
            "task_dir": str(Path(shown["mirror"]).parent),
            "harness_config": str(harness),
            "project": shown["project"],
        }
    )
    (run / "replay.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def _core(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args], cwd=ROOT, capture_output=True, text=True, check=False,
        env=os.environ.copy(),
    )  # fmt: skip


def teardown(run_name: str) -> None:
    run = DEMO / "runs" / run_name
    replay_json = run / "replay.json"
    task = json.loads(replay_json.read_text()).get("task_id") if replay_json.exists() else None
    if task:
        _core("stop", task, "--reason", "replay teardown")
        removed = _core(
            "workspace", "remove", task, "--by", "replay driver", "--via", "scripts/replay_workspace.py"
        )
        if removed.returncode != 0 and "has no workspace" not in removed.stderr:
            raise SystemExit(f"removing task {task}'s workspace: {removed.stderr.strip()}")
    if run.exists():
        shutil.rmtree(run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", nargs="?")
    parser.add_argument("base", nargs="?")
    parser.add_argument("run_name", nargs="?")
    parser.add_argument("--services", default="none", help="comma list of postgres, redis; or none")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--teardown", metavar="RUN_NAME")
    args = parser.parse_args()
    if args.teardown:
        teardown(args.teardown)
        print(f"run {args.teardown} deleted")
        return
    if not (args.repo and args.base and args.run_name):
        parser.error("REPO BASE RUN_NAME are required")
    services = [] if args.services == "none" else [s for s in args.services.split(",") if s]
    info = build(
        args.repo, args.base, args.run_name, services, max_output_tokens=args.max_output_tokens,
        rebuild=args.rebuild,
    )  # fmt: skip
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
