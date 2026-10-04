"""Pi's wrapper (`harnesses/pi.py`) and what it touches: the command it
builds, the prompt on stdin against the real binary, the turn's
`models.json`, the parse of Pi's event stream on recorded fixtures
(`tests/fixtures/pi/`), the version, the reviewer seat, and the blind
checkout that leaves `.pi/` out.

The contract every harness meets, Pi included, is `test_harness_contract.py`.

Live spend: none.
"""

import asyncio
import dataclasses
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from core import git as kgit
from core import workspace as kws
from core.gateway import TURN_TOKEN
from core.runs import TurnCommand
from core.settings import SEATS, resolve_seat, settings
from harnesses import claude_code, pi
from tests import scripted
from tests.scripted_upstream import Say, ScriptedUpstream
from tests.test_workspace import provision

pytestmark = pytest.mark.spend(usd=0)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pi"
installed = pytest.mark.skipif(
    pi.version(settings.pi) != pi.PINNED, reason=f"Pi {pi.PINNED} is not the installed release"
)


def _build(tmp_path, prompt="hello", base_url="http://127.0.0.1:9", **kw) -> tuple[TurnCommand, dict]:
    _task, made = provision(tmp_path)
    build = pi.workspace_turn(prompt, cwd=made.workspace, harness=made.harness, **kw)
    return build(base_url, "BRIEF TEXT", "turn-1"), made.harness


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# -- the command ----------------------------------------------------------------------------


@pytest.mark.macos
def test_the_command_runs_node_and_the_script_under_the_turn_profile_with_every_loader_off(tmp_path):
    command, harness = _build(tmp_path, resume="sess-1")
    argv = command.argv
    assert argv[0] == "/usr/bin/sandbox-exec" and _flag_value(argv, "-f") == harness["sandbox_profile"]
    after = argv[argv.index("--no-extensions") - 2 :]
    assert os.path.isabs(after[0]) and after[0] == settings.node and after[1].endswith(".js")
    for flag in ("--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
                 "--no-context-files", "--offline", "-p"):  # fmt: skip
        assert flag in argv
    assert _flag_value(argv, "--provider") == pi.PROVIDER and _flag_value(argv, "--model") == "gpt-6.1-sol"
    assert _flag_value(argv, "--mode") == "json" and _flag_value(argv, "--session") == "sess-1"
    assert _flag_value(argv, "--system-prompt") == pi.SYSTEM_PROMPT
    assert _flag_value(argv, "--append-system-prompt") == "BRIEF TEXT", "only the Brief is appended"
    assert _flag_value(argv, "--session-dir") == str(Path(harness["pi_agent_dir"]) / "sessions")
    assert command.env["PI_CODING_AGENT_DIR"] == harness["pi_agent_dir"] and command.env["PI_OFFLINE"] == "1"
    assert command.harness == "pi" and command.harness_version == pi.version(settings.pi)


@pytest.mark.macos
def test_the_prompt_is_stdin_and_never_an_argument(tmp_path):
    command, _ = _build(tmp_path, prompt="--not-a-flag @not-a-file")
    assert command.stdin == b"--not-a-flag @not-a-file"
    assert not any("not-a-flag" in a for a in command.argv)
    assert "--" not in command.argv


def test_a_turn_without_a_profile_or_agent_directory_is_refused(tmp_path):
    _task, made = provision(tmp_path)
    with pytest.raises(claude_code.Unsandboxed):
        pi.workspace_turn("x", cwd=made.workspace, harness={"pi_agent_dir": made.harness["pi_agent_dir"]})
    with pytest.raises(ValueError, match="pi_agent_dir"):
        pi.workspace_turn(
            "x", cwd=made.workspace, harness={"sandbox_profile": made.harness["sandbox_profile"]}
        )


@installed
@pytest.mark.parametrize("prompt", ["-starts with a dash", "@starts-with-an-at-sign", "--", "plain words"])
def test_the_real_binary_receives_each_prompt_whole(tmp_path, prompt):
    async def go():
        upstream = await ScriptedUpstream([Say("ok")]).start()
        try:
            command, _ = _build(tmp_path, prompt=prompt, base_url=upstream.url)
            proc = await asyncio.create_subprocess_exec(
                *command.argv, env=command.env, cwd=command.cwd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            out, err = await asyncio.wait_for(proc.communicate(command.stdin), 120)
            return proc.returncode, out, err, upstream.requests
        finally:
            await upstream.stop()

    code, out, err, requests = asyncio.run(go())
    assert code == 0, err.decode()[-400:]
    assert len(requests) == 1 and prompt in requests[0].text
    assert not pi.parse(out)["is_error"]


# -- the turn's configuration ---------------------------------------------------------------


@pytest.mark.macos
def test_models_json_points_at_the_gateway_with_the_placeholder_key_and_the_briefs_output_cap(tmp_path):
    _command, harness = _build(tmp_path, base_url="http://127.0.0.1:4242/t/tok")
    models = json.loads((Path(harness["pi_agent_dir"]) / "models.json").read_text())
    provider = models["providers"][pi.PROVIDER]
    assert provider["baseUrl"] == "http://127.0.0.1:4242/t/tok/openai/v1"
    assert provider["apiKey"] == TURN_TOKEN and provider["api"] == "openai-responses"
    (model,) = provider["models"]
    assert (
        model["id"] == "gpt-6.1-sol"
        and model["contextWindow"] == pi.context_window("gpt-6.1-sol") == 1_050_000 - 128_000
    )
    assert model["maxTokens"] == pi.DEFAULT_MAX_OUTPUT_TOKENS
    settings_ = json.loads((Path(harness["pi_agent_dir"]) / "settings.json").read_text())
    assert settings_["defaultProvider"] == pi.PROVIDER and settings_["defaultModel"] == "gpt-6.1-sol"


def test_context_window_is_found_by_the_price_tables_id_matching():
    assert pi.context_window("gpt-6.1-sol-2026-01-15") == pi.context_window("gpt-6.1-sol")
    with pytest.raises(ValueError):
        pi.context_window("gpt-6.1-sol-pro")


@pytest.mark.macos
def test_max_output_tokens_comes_from_the_harness_settings(tmp_path):
    _task, made = provision(tmp_path)
    harness = {**made.harness, "max_output_tokens": 1234}
    pi.workspace_turn("x", cwd=made.workspace, harness=harness)("http://127.0.0.1:9", "b", "t")
    models = json.loads((Path(made.harness["pi_agent_dir"]) / "models.json").read_text())
    assert models["providers"][pi.PROVIDER]["models"][0]["maxTokens"] == 1234


def test_the_cost_fields_are_the_gateways_default_tier_rates_when_it_has_them():
    from core import spending

    cost = pi._cost("gpt-6.1-sol")
    if not hasattr(spending, "openai_prices"):
        assert cost == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    else:
        base = spending.openai_prices("gpt-6.1-sol")["tiers"]["default"]["base"]
        assert cost["input"] == base["input"] / 1_000_000 and cost["output"] == base["output"] / 1_000_000


@pytest.mark.macos
def test_a_link_planted_in_the_agent_directory_is_replaced_not_followed(tmp_path):
    _task, made = provision(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("untouched")
    agent = Path(made.harness["pi_agent_dir"])
    (agent / "models.json").symlink_to(secret)
    os.link(secret, agent / "settings.json")
    pi.workspace_turn("x", cwd=made.workspace, harness=made.harness)("http://127.0.0.1:9", "b", "t")
    assert secret.read_text() == "untouched"
    assert not (agent / "models.json").is_symlink() and not stat.S_ISLNK(
        (agent / "models.json").lstat().st_mode
    )
    assert (agent / "settings.json").stat().st_nlink == 1


def test_pi_state_is_denied_to_every_turn_and_provisioned_per_task(tmp_path):
    assert ".pi" in kws.HOME_DENIED
    _task, made = provision(tmp_path)
    assert Path(made.harness["pi_agent_dir"]).is_dir()


# -- the event stream -----------------------------------------------------------------------


def _parse(name: str) -> dict:
    return pi.parse((FIXTURES / f"{name}.jsonl").read_bytes())


def test_parse_reads_a_plain_reply():
    got = _parse("plain")
    assert got["is_error"] is False and got["session_id"] and got["text"] == "ready"
    assert got["num_turns"] == 1 and got["compaction"] == []


def test_parse_counts_the_turns_of_a_tool_call():
    got = _parse("tool_call")
    assert got["is_error"] is False and got["num_turns"] == 2 and got["text"] == "done"


@pytest.mark.parametrize("name", ["error", "aborted", "no_session"])
def test_parse_marks_an_errored_aborted_or_session_less_stream(name):
    assert _parse(name)["is_error"] is True


def test_parse_of_nothing_is_an_error():
    assert pi.parse(b"")["is_error"] is True and pi.parse(b"not json\n")["is_error"] is True


def test_parse_lists_a_compaction_the_stream_ends_in_the_middle_of():
    (event,) = _parse("compaction")["compaction"]
    assert event == {"reason": "threshold", "finished": False}


def test_parse_lists_a_finished_compaction():
    (event,) = _parse("compaction_ended")["compaction"]
    assert event["finished"] is True and event["tokens_before"] == 300000 and event["tokens_after"] == 12000
    assert event["aborted"] is False


# -- versions -------------------------------------------------------------------------------


def test_version_reads_the_package_that_is_the_parent_of_dist(tmp_path):
    pkg = tmp_path / "node_modules" / "@mariozechner" / "pi-coding-agent"
    (pkg / "dist").mkdir(parents=True)
    (pkg / "dist" / "cli.js").write_text("")
    (pkg / "package.json").write_text(json.dumps({"name": pi.PACKAGE, "version": "9.9.9"}))
    link = tmp_path / "pi"
    link.symlink_to(pkg / "dist" / "cli.js")
    assert pi.version(str(link)) == "9.9.9"
    other = tmp_path / "other"
    (other / "dist").mkdir(parents=True)
    (other / "dist" / "cli.js").write_text("")
    (other / "package.json").write_text(json.dumps({"name": "something-else", "version": "1.0.0"}))
    assert pi.version(str(other / "dist" / "cli.js")) is None
    assert pi.version(str(tmp_path / "missing")) is None


def test_claude_code_version_is_the_installed_versions_directory_name(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    binary = versions / "2.9.9"
    binary.write_text("")
    link = tmp_path / "claude"
    link.symlink_to(binary)
    assert claude_code.version(str(link)) == "2.9.9"
    loose = tmp_path / "loose-claude"
    loose.write_text("")
    assert claude_code.version(str(loose)) is None


# -- seats ----------------------------------------------------------------------------------


def test_the_openai_reviewer_seat_is_pi_on_the_gateways_priced_model():
    assert resolve_seat("reviewer_openai") == ("pi", "gpt-6.1-sol")
    assert SEATS["reviewer"][0] == "claude_code"
    assert resolve_seat("some-model-id") == ("claude_code", "some-model-id")


# -- the blind checkout ---------------------------------------------------------------------


@pytest.mark.macos
def test_a_blind_checkout_leaves_pi_configuration_out_of_the_tree_but_in_the_diff(tmp_path):
    _task, made = provision(tmp_path)
    ws = Path(made.workspace)
    (ws / ".pi").mkdir()
    (ws / ".pi" / "settings.json").write_text('{"compaction": {"enabled": false}}')
    scripted.git(ws, "add", "-f", ".pi")
    sha = scripted.commit(ws, "x.txt", "x\n", "adds pi settings")
    lay = kws.Layout(Path(made.mirror).parent)
    kws.fetch_into_mirror(
        made.mirror, ws, sha, "refs/valor/candidates/t", made.harness["sandbox_profile"], "f"
    )
    dest = kws.fresh_dir(lay.checks / "c1") / "repo"
    commits = kws.blind_checkout(made.mirror, made.base_sha, sha, dest)
    assert not (dest / ".pi").exists() and (dest / "x.txt").exists()
    diff = kgit.trusted(dest, "diff", "--no-ext-diff", "--no-renames", commits["base"], commits["candidate"])
    assert ".pi/settings.json" in diff
    status = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout
    assert status.strip() == ""


@pytest.mark.macos
def test_a_blind_checkout_leaves_out_a_pi_link_to_a_directory_too(tmp_path):
    _task, made = provision(tmp_path)
    ws = Path(made.workspace)
    (ws / "cfg").mkdir()
    (ws / "cfg" / "settings.json").write_text('{"shellPath": "/tmp/evil"}')
    (ws / ".pi").symlink_to("cfg")
    scripted.git(ws, "add", "-f", ".pi", "cfg")
    sha = scripted.commit(ws, "x.txt", "x\n", "adds a pi link")
    lay = kws.Layout(Path(made.mirror).parent)
    kws.fetch_into_mirror(
        made.mirror, ws, sha, "refs/valor/candidates/t", made.harness["sandbox_profile"], "f"
    )
    dest = kws.fresh_dir(lay.checks / "c1") / "repo"
    kws.blind_checkout(made.mirror, made.base_sha, sha, dest)
    assert not (dest / ".pi").exists() and not (dest / ".pi").is_symlink()
    assert (dest / "cfg" / "settings.json").exists()


def test_the_pi_install_is_denied_to_writes_wherever_it_is(tmp_path, monkeypatch):
    install = tmp_path / "valor-pi-x"
    cli = install / "node_modules" / "@mariozechner" / "pi-coding-agent" / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    monkeypatch.setattr(kws, "settings", dataclasses.replace(settings, pi=str(cli)))
    assert str(install.resolve()) in kws.pi_install()
    profile = kws.profile(rw=[tmp_path / "work"], work=tmp_path / "work")
    assert str(install.resolve()) in profile


def test_a_pi_with_no_node_modules_has_its_own_directory_denied(tmp_path, monkeypatch):
    real = tmp_path / "stand" / "alone" / "pi"
    real.parent.mkdir(parents=True)
    real.write_text("")
    monkeypatch.setattr(kws, "settings", dataclasses.replace(settings, pi=str(real)))
    assert str(real.parent.resolve()) in kws.pi_install()
    profile = kws.profile(rw=[tmp_path / "work"], work=tmp_path / "work")
    assert str(real.parent.resolve()) in profile


def test_the_resolved_target_and_the_link_that_names_it_are_both_denied(tmp_path, monkeypatch):
    target = tmp_path / "inst" / "node_modules" / "p" / "cli.js"
    target.parent.mkdir(parents=True)
    target.write_text("")
    link = tmp_path / "bin" / "pi"
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, pi=str(link), node="/opt/homebrew/bin/node")
    )
    assert kws.pi_install()[:2] == [str((tmp_path / "inst").resolve()), str(link.parent)]


def test_node_defaults_to_an_absolute_path_in_homebrews_prefix_whatever_the_path(monkeypatch, tmp_path):
    fake = tmp_path / "node"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.delenv("VALOR_NODE", raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert type(settings)().node == "/opt/homebrew/bin/node"
    monkeypatch.setenv("VALOR_NODE", "/elsewhere/node")
    assert type(settings)().node == "/elsewhere/node"


def test_pi_defaults_to_homebrews_prefix(monkeypatch):
    monkeypatch.delenv("VALOR_PI", raising=False)
    assert type(settings)().pi == "/opt/homebrew/bin/pi"


# -- the install cannot be replaced from above, through a link, or through node ------------------


def _under_profile(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    path = tmp_path / "turn.sb"
    path.write_text(kws.profile(rw=[work], work=work))
    sandbox = ["/usr/bin/sandbox-exec", "-D", "GATEWAY_PORT=1", "-D", "VALOR_TURN=t", "-f", str(path)]
    return subprocess.run(
        [*sandbox, "/bin/sh", "-c", script], capture_output=True, text=True, cwd=work, check=False
    )


def _pi_layout(root: Path) -> Path:
    """`docs/pi.md`'s layout: `<root>/valor-pi-X/node_modules/.bin/pi`."""
    cli = root / "valor-pi-x" / "node_modules" / "p" / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    link = root / "valor-pi-x" / "node_modules" / ".bin" / "pi"
    link.parent.mkdir(parents=True)
    link.symlink_to(cli)
    return link


@pytest.mark.macos
def test_a_turn_cannot_rename_an_ancestor_of_the_install_and_put_its_own_tree_there(tmp_path, monkeypatch):
    link = _pi_layout(tmp_path / "cache")
    monkeypatch.setattr(kws, "settings", dataclasses.replace(settings, pi=str(link)))
    out = _under_profile(
        tmp_path,
        f"touch {tmp_path}/work/ok && touch {tmp_path}/cache/sibling && mv {tmp_path}/cache {tmp_path}/old",
    )
    assert out.returncode != 0 and "Operation not permitted" in out.stderr
    assert (tmp_path / "work" / "ok").exists() and (tmp_path / "cache" / "sibling").exists()
    assert link.is_symlink() and not (tmp_path / "old").exists()
    for inside in (tmp_path / "cache" / "valor-pi-x", tmp_path / "cache" / "valor-pi-x" / "node_modules"):
        assert _under_profile(tmp_path, f"mv {inside} {inside}.old").returncode != 0


@pytest.mark.macos
def test_a_link_reached_through_a_symlinked_parent_is_denied_by_its_resolved_directory(tmp_path, monkeypatch):
    real = tmp_path / "real"
    inst = _pi_layout(tmp_path / "inst")
    (real / "bin").mkdir(parents=True)
    (real / "bin" / "pi").symlink_to(inst)
    (tmp_path / "spelled").symlink_to(real)
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, pi=str(tmp_path / "spelled" / "bin" / "pi"))
    )
    assert str((real / "bin").resolve()) in kws.pi_install()
    assert str(tmp_path / "spelled" / "bin") not in kws.pi_install()
    out = _under_profile(tmp_path, f"ln -sf /usr/bin/true {tmp_path}/spelled/bin/pi")
    assert out.returncode != 0 and "Operation not permitted" in out.stderr
    assert (real / "bin" / "pi").resolve() == inst.resolve()
    assert _under_profile(tmp_path, f"mv {tmp_path}/spelled {tmp_path}/other").returncode != 0


@pytest.mark.macos
def test_a_node_outside_homebrew_is_denied_like_the_install(tmp_path, monkeypatch):
    node = tmp_path / "nvm" / "versions" / "v1" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    monkeypatch.setattr(kws, "settings", dataclasses.replace(settings, node=str(node)))
    assert str(node.parent.resolve()) in kws.pi_install()
    for script in (
        f"echo evil > {node}",
        f"rm {node}",
        f"mv {tmp_path}/nvm {tmp_path}/nvm.old",
        f"mv {tmp_path}/nvm/versions {tmp_path}/nvm/other",
    ):
        out = _under_profile(tmp_path, script)
        assert out.returncode != 0 and "Operation not permitted" in out.stderr, script
    assert node.read_text() == "#!/bin/sh\n"
    assert _under_profile(tmp_path, f"touch {tmp_path}/nvm/new").returncode == 0


@pytest.mark.macos
@pytest.mark.parametrize("which", ["pi", "node"])
def test_a_two_hop_chain_is_held_at_the_middle_link_and_its_directory(tmp_path, monkeypatch, which):
    real = tmp_path / "pfx" / "lib" / "real.js"
    real.parent.mkdir(parents=True)
    real.write_text("good")
    mid = tmp_path / "mid"
    mid.mkdir()
    (mid / which).symlink_to(real)
    top = tmp_path / "top" / which
    top.parent.mkdir()
    top.symlink_to(mid / which)
    other = "node" if which == "pi" else "pi"
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, **{which: str(top), other: "/opt/homebrew/bin/x"})
    )
    assert str(mid / which) in kws.pi_install_held()
    assert str(mid) in kws.pi_install_held()
    for script in (
        f"ln -sf /usr/bin/true {mid}/{which}",
        f"rm {mid}/{which}",
        f"mv {mid} {tmp_path}/mid.old",
    ):
        out = _under_profile(tmp_path, script)
        assert out.returncode != 0 and "Operation not permitted" in out.stderr, script
    assert (mid / which).resolve() == real.resolve()


@pytest.mark.macos
@pytest.mark.parametrize("which", ["pi", "node"])
def test_a_symlinked_directory_inside_the_links_target_is_held(tmp_path, monkeypatch, which):
    pfx = tmp_path / "pfx"
    (pfx / "bin").mkdir(parents=True)
    store = tmp_path / "store" / "mods"
    store.mkdir(parents=True)
    (store / "cli.js").write_text("good")
    (pfx / "lib").symlink_to(store)
    link = pfx / "bin" / which
    link.symlink_to("../lib/cli.js")
    other = "node" if which == "pi" else "pi"
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, **{which: str(link), other: "/opt/homebrew/bin/x"})
    )
    assert str(pfx / "lib") in kws.pi_install_held()
    evil = tmp_path / "evilmods"
    evil.mkdir()
    for script in (
        f"rm {pfx}/lib && ln -s {evil} {pfx}/lib",
        f"mv {pfx} {tmp_path}/pfx.old",
        f"echo bad > {store}/cli.js",
    ):
        out = _under_profile(tmp_path, script)
        assert out.returncode != 0 and "Operation not permitted" in out.stderr, script
    assert (pfx / "lib").resolve() == store.resolve()
    assert (store / "cli.js").read_text() == "good"


@pytest.mark.macos
@pytest.mark.parametrize("which", ["pi", "node"])
def test_a_link_before_dotdot_in_a_targets_path_is_held_as_the_kernel_walks_it(tmp_path, monkeypatch, which):
    real = tmp_path / "r" / "x"
    cli = tmp_path / "r" / "inst" / "node_modules" / "p" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("good")
    real.mkdir(exist_ok=True)
    mid = tmp_path / "m" / "mid"
    mid.mkdir(parents=True)
    (mid / "sub").symlink_to(real)
    (mid / which).symlink_to("sub/../inst/node_modules/p/cli.js")
    top = tmp_path / "top" / which
    top.parent.mkdir()
    top.symlink_to(mid / which)
    other = "node" if which == "pi" else "pi"
    monkeypatch.setattr(
        kws, "settings", dataclasses.replace(settings, **{which: str(top), other: "/opt/homebrew/bin/x"})
    )
    assert str(mid / "sub") in kws.pi_install_held()
    evil = tmp_path / "e" / "x"
    evil.mkdir(parents=True)
    out = _under_profile(tmp_path, f"rm {mid}/sub && ln -s {evil} {mid}/sub")
    assert out.returncode != 0 and "Operation not permitted" in out.stderr
    assert Path(os.path.realpath(top)) == cli.resolve()
