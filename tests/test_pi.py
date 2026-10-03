"""Pi's wrapper (`harnesses/pi.py`) and what it touches: the command it
builds, the prompt on stdin against the real binary, the turn's
`models.json`, the parse of Pi's event stream on recorded fixtures
(`tests/fixtures/pi/`), the version, the reviewer seat, and the blind
checkout that leaves `.pi/` out.

The contract every harness meets, Pi included, is `test_harness_contract.py`.

Live spend: none.
"""

import asyncio
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
