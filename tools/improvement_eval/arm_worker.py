"""Arm-side entry point for the frozen-input harness (#3216).

Run as ``python -m tools.improvement_eval.arm_worker`` in a child process
whose env dict carries ``REDIS_URL=unix://<arm.sock>`` (plus the arm's own
``POPOTO_CONTENT_PATH``, ``VALOR_PROJECT_KEY``,
``POPOTO_EMBEDDING_INVALIDATION=none``, and ``RETRIEVAL_MODE=current`` so
retrieval ranks through the four-signal RRF path). Inside this process, and
only inside this process, popoto's canonical pool is the arm's private
server, so every Redis touch here goes through the ORM against the arm.

Protocol: one JSON job spec on stdin, one JSON response on stdout.

- ``{"mode": "restore", "jsonl": ..., "project_key": ...}``: restore the
  frozen corpus, arm the writer guard, and report the re-export digest.
- ``{"mode": "retrieve", "jsonl": ..., "project_key": ...,
  "query_text": ..., "limit": ...}``: restore, arm, retrieve, and report
  the ranked ids with the digests. ``rrf_k`` and ``min_rrf_score`` are
  forwarded to ``retrieve_memories`` only when the job carries them; an
  absent key leaves the retrieval path at its own default.
- ``{"mode": "digest", "jsonl": ..., "project_key": ...}``: restore, arm,
  and report the re-export digest plus the canonical remaining manifest
  (the runner asserts byte-equality between arms).
- ``{"mode": "agent_run", "jsonl": ..., "project_key": ...,
  "tasks": [{"id": ..., "prompt": ...}], "manifest": {"model": ...},
  "bounds": {"timeout_s": ..., "max_turns": ..., "spend_cap": ...}}``:
  restore, arm, run one bounded agent session per trial under this
  process's arm child env (private Redis socket, scratch content path,
  arm project key), and report the per-trial outcomes plus the
  candidate manifest. The task set and manifest are validated at load,
  before the corpus restore, so a malformed job never touches Redis.
  A ``"scratch_path"`` key in the job is refused: the scratch dir is
  derived from the arm's own content path, never taken from the spec.

Every mode ends with the teardown digest re-check: the digest taken right
after restore must equal the digest taken after the job's reads, or a
write slipped past the wrapper and the arm is invalid.

An optional ``"clock_skew_s"`` in a retrieve job shifts this process's
``time.time`` during the retrieve step only. It exists so a test can query
an arm under a clock 30 days forward and prove the retrieval path does
not read the decay clock. It travels in the job spec rather than the
environment, and the runner's ``ARM_PARAM_KEYS`` allowlist keeps it out of
every job it builds from a protocol, so neither the ambient environment nor
a frozen contract can skew a real arm's clock; only
``run_arm_job(clock_skew_s=...)`` sets it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from agent.llm.tasks import Backend, LLMTask, TaskKind

from .errors import InfraFailure
from .judges.rubric import extract_verdict

# Thinking: an experiment arm's agent session over OpenRouter (raw HTTP,
# lazy import). Fail-safe: none; the worker raises and the trial is recorded
# as an error.
OPENROUTER_ARM = LLMTask(
    site="improvement_eval.openrouter_arm",
    kind=TaskKind.THINKING,
    backend=Backend.ANTHROPIC,
)

JOB_MODES = ("restore", "retrieve", "digest", "agent_run")

#: Bounds keys an ``agent_run`` job may carry. All are optional; the
#: session seam applies its own defaults for absent keys.
AGENT_RUN_BOUND_KEYS = ("timeout_s", "max_turns", "spend_cap")


@contextmanager
def _skewed_clock(skew: float):
    """Shift ``time.time`` for the retrieve step by ``skew`` seconds."""
    if not skew:
        yield
        return
    real_time = time.time
    time.time = lambda: real_time() + skew
    try:
        yield
    finally:
        time.time = real_time


def _restore_and_arm(jsonl_text: str) -> None:
    from . import writer_guard
    from .corpus import restore_corpus

    restore_corpus(jsonl_text)
    writer_guard.arm()
    return None


def _arm_scratch_dir() -> str:
    """Derive this arm's scratch dir from its own content path.

    The dir lives under ``POPOTO_CONTENT_PATH`` (which the arena sets to
    the arm's private tmpdir, unique per arm), so concurrent arms never
    share session artifacts. The job spec cannot override it.
    """
    import os

    content_dir = os.environ.get("POPOTO_CONTENT_PATH", "")
    if not content_dir:
        raise InfraFailure("agent_run job needs POPOTO_CONTENT_PATH in the arm child env")
    scratch = os.path.join(content_dir, "scratch-agent-run")
    os.makedirs(scratch, exist_ok=True)
    return scratch


def _validate_agent_run_job(job: dict) -> tuple[list, dict, dict]:
    """Check the task set, manifest, and bounds; refuse before any restore."""
    if "scratch_path" in job:
        raise InfraFailure("agent_run job must not carry 'scratch_path'; derived from the arm")
    tasks = job.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise InfraFailure("agent_run job requires a non-empty 'tasks' list")
    for task in tasks:
        if not isinstance(task, dict):
            raise InfraFailure("agent_run job 'tasks' entries must be mappings")
        if not isinstance(task.get("id"), str) or not task["id"].strip():
            raise InfraFailure("agent_run job tasks need a non-blank 'id'")
        if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
            raise InfraFailure(f"agent_run task {task.get('id')!r} needs a non-blank 'prompt'")
    manifest = job.get("manifest")
    if not isinstance(manifest, dict):
        raise InfraFailure("agent_run job requires a 'manifest' mapping")
    if not isinstance(manifest.get("model"), str) or not manifest["model"].strip():
        raise InfraFailure("agent_run job manifest needs a non-blank 'model'")
    bounds = job.get("bounds") or {}
    if not isinstance(bounds, dict):
        raise InfraFailure("agent_run job 'bounds' must be a mapping")
    for key in bounds:
        if key not in AGENT_RUN_BOUND_KEYS:
            raise InfraFailure(
                f"agent_run job has unknown bound {key!r}; expected {AGENT_RUN_BOUND_KEYS}"
            )
    try:
        coerced = {k: float(v) for k, v in bounds.items()}
    except (TypeError, ValueError) as exc:
        raise InfraFailure(f"agent_run job bounds are not numbers: {exc}") from exc
    return tasks, manifest, coerced


#: The headless ``claude -p`` argv for one trial session, prompt appended
#: last. ``--tools=`` disables every built-in tool and ``--strict-mcp-config``
#: loads no MCP server, so the session cannot open the repo or the frozen
#: corpus and de-blind itself; with no tools the call is a single model turn
#: on the subscription. Each flag is one ``--flag=value`` argv element.
SUBSCRIPTION_SESSION_ARGV = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--tools=",
    "--strict-mcp-config",
]

#: Wall-clock cap (seconds) on one trial session when the job names no
#: ``timeout_s`` bound. Five minutes covers a slow turn and turns a hung
#: subprocess into a raised error (a harness error upstream), never a stall.
SUBSCRIPTION_SESSION_TIMEOUT_S = 300.0

#: Verdict tokens a trial session may close with. The prompt instructs the
#: session to end on exactly one of these; the rubric judges score it.
SESSION_VERDICTS = ("FREEZE", "HOLD")

#: Manifest-model prefix routing a trial session to the cheap provider.
#: ``claude-subscription`` (or any unprefixed model) runs one headless
#: ``claude -p`` turn; ``openrouter:<model-id>`` reaches that OpenRouter
#: model id through the existing ``OPENROUTER_API_KEY`` over the
#: OpenAI-compatible chat-completions route lane 5's cheap-inference
#: investigation exercised (``keyless_integrated``: no vault wait).
OPENROUTER_MODEL_PREFIX = "openrouter:"

#: Cap on one cheap-provider completion. The cheap model reasons before it
#: answers and the trace counts against this cap, so a short cap truncates
#: the turn before any decision line: the trial needs room for the trace
#: plus the verdict. Per-trial cost stays well under the frozen spend cap.
OPENROUTER_MAX_TOKENS = 4096


def _checkout_root() -> Path:
    """The checkout this worker runs from: where frozen skill files live."""
    return Path(__file__).resolve().parents[2]


def _resolve_skill_text(skill: str, prompt_hash: str | None) -> str:
    """Read the frozen skill text the manifest names, pinned by ``prompt_hash``.

    A blank skill is the prior capability: no text, no pin. A named skill
    needs its ``SKILL.md`` under ``.claude/skills`` (else
    ``.claude/skills-global``) and a ``prompt_hash`` equal to the file's
    sha256; anything short of that refuses with :class:`InfraFailure`
    before any session spawns.
    """
    if not (skill or "").strip():
        return ""
    name = skill.strip()
    if not (prompt_hash or "").strip():
        raise InfraFailure(
            f"agent_run manifest names skill {name!r} with no prompt_hash; "
            "a skill session needs its frozen pin"
        )
    root = _checkout_root()
    locations = [
        root / ".claude" / "skills" / name / "SKILL.md",
        root / ".claude" / "skills-global" / name / "SKILL.md",
    ]
    path = next((p for p in locations if p.is_file()), None)
    if path is None:
        raise InfraFailure(
            f"agent_run manifest names skill {name!r} but no SKILL.md "
            f"exists under .claude/skills or .claude/skills-global"
        )
    text = path.read_text()
    digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != prompt_hash.strip():
        raise InfraFailure(
            f"agent_run manifest prompt_hash {prompt_hash!r} does not match "
            f"the frozen skill text {digest}; the skill moved after freezing"
        )
    return text


def _compose_session_prompt(task: dict, manifest: dict, skill_text: str) -> str:
    """The one prompt the trial session sees: persona, skill, task, verdict."""
    parts = []
    persona = (manifest.get("persona") or "").strip()
    if persona:
        parts.append(f"You are a {persona} research session.")
    if skill_text:
        parts.append(
            "Follow this skill. It is the frozen capability under evaluation:\n\n" + skill_text
        )
    parts.append(task["prompt"])
    parts.append(
        "End your reply with exactly one line of the form `VERDICT: <word>` "
        f"where <word> is one of {', '.join(SESSION_VERDICTS)}, then stop."
    )
    return "\n\n".join(parts)


def _run_session_via_subscription(prompt: str, timeout_s: float) -> str:
    """Run one headless ``claude -p`` turn; return its result text.

    Runs from an empty temporary directory with every tool disabled (see
    :data:`SUBSCRIPTION_SESSION_ARGV`): the only thing the session sees is
    the prompt. A nonzero exit raises for the worker to report as a harness
    error; empty output is a malformed session output, not a transport
    failure, so it comes back as ``""`` for the trial to score zero --
    matching the OpenRouter path.
    """
    with tempfile.TemporaryDirectory(prefix="agent-run-trial-") as empty_cwd:
        completed = subprocess.run(
            [*SUBSCRIPTION_SESSION_ARGV, prompt],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=empty_cwd,
        )
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip()[-500:]
        raise RuntimeError(f"claude -p exited {completed.returncode}: {tail}")
    if not (completed.stdout or "").strip():
        return ""
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError(f"claude -p returned non-JSON output: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return payload["result"]
    return completed.stdout


def _run_session_via_openrouter(prompt: str, timeout_s: float, model_id: str) -> str:
    """Run one chat completion on the cheap provider; return its text.

    The OpenAI-compatible route lane 5's investigation exercised: POST the
    model id and the prompt to the chat-completions endpoint with the
    existing ``OPENROUTER_API_KEY``. A missing key refuses with
    :class:`InfraFailure` before anything is posted; any transport or
    shape failure raises so the worker reports an error and the trial
    becomes a harness error, never a scored zero.
    """
    import os

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise InfraFailure(
            "agent_run openrouter session needs OPENROUTER_API_KEY in the arm child env"
        )
    import requests

    from config.models import OPENROUTER_URL

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost",
                "X-Title": "Valor Agent-Run Arm",
            },
            json={
                "model": model_id,
                "max_tokens": OPENROUTER_MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout_s,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        raise RuntimeError(f"openrouter session on {model_id!r} failed: {exc}") from exc
    choices = (result or {}).get("choices") if isinstance(result, dict) else None
    if not choices:
        raise RuntimeError(f"openrouter session on {model_id!r} returned no choices")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    # A 200 with choices but no usable text is a malformed session output,
    # not a transport failure: the trial comes back undecided and the
    # rubric scores it zero, matching the subscription path.
    return ""


def _default_transport_for(model: str):
    """The session transport the manifest model names.

    Returns a ``(prompt, timeout_s)`` callable: the OpenRouter transport
    pinned to the model id behind the ``openrouter:`` prefix, or the
    subscription transport for anything else.
    """
    if isinstance(model, str) and model.startswith(OPENROUTER_MODEL_PREFIX):
        model_id = model[len(OPENROUTER_MODEL_PREFIX) :].strip()
        if not model_id:
            raise InfraFailure(
                "agent_run manifest model names the openrouter route with no model id"
            )
        return lambda prompt, timeout_s: _run_session_via_openrouter(prompt, timeout_s, model_id)
    return _run_session_via_subscription


def run_agent_trial(
    task: dict, manifest: dict, bounds: dict, project_key: str, _complete=None
) -> dict:
    """Run one bounded agent session for a task; return its outcome.

    This process already runs under the arm child env, so the session
    inherits the private Redis socket, the scratch content path, and the
    arm project key. The manifest shapes the prompt (persona preamble plus
    the frozen skill text when it names a skill, pinned by ``prompt_hash``);
    ``bounds["timeout_s"]`` caps the turn and must be positive (a zero or
    negative timeout is a frozen-contract defect, never a silent default).
    ``bounds["max_turns"]`` is accepted and carried but reserved, not
    enforced: the subscription transport runs one ``claude -p`` turn with
    tools disabled, so there is no second turn to bound. The ``VERDICT:``
    line parses into the ``passed`` bit: a session with no decision line
    comes back with ``passed=False`` for the rubric to score zero, while a
    transport failure raises for the worker to report as a harness error.
    ``_complete`` injects the session transport (tests); the default follows
    the manifest model to the subscription or the cheap OpenRouter route,
    and a missing or blank model is refused outright.
    """
    model = manifest.get("model")
    if not isinstance(model, str) or not model.strip():
        raise InfraFailure(f"agent_run manifest needs a non-blank model, got {model!r}")
    skill_text = _resolve_skill_text(manifest.get("skill") or "", manifest.get("prompt_hash"))
    prompt = _compose_session_prompt(task, manifest, skill_text)
    timeout_raw = (bounds or {}).get("timeout_s")
    try:
        raw = timeout_raw if timeout_raw is not None else SUBSCRIPTION_SESSION_TIMEOUT_S
        timeout_s = float(raw)
    except (TypeError, ValueError) as exc:
        raise InfraFailure(f"agent_run bounds timeout_s is not a number: {exc}") from exc
    if timeout_s <= 0:
        raise InfraFailure(f"agent_run bounds timeout_s must be positive, got {timeout_raw!r}")
    complete = _complete or _default_transport_for(model)
    output = complete(prompt, timeout_s)
    if not isinstance(output, str):
        output = str(output)
    scratch = _arm_scratch_dir()
    return {
        "task_id": task["id"],
        "output": output,
        "passed": extract_verdict(output) in SESSION_VERDICTS,
        "model": manifest.get("model"),
        "scratch": scratch,
    }


def _arm_digest(project_key: str):
    from models.memory import Memory

    from .corpus import canonical_corpus_digest, canonical_manifest

    reexport = Memory.export_records(project_key=project_key)
    jsonl_text = reexport.data or ""
    return canonical_corpus_digest(jsonl_text), canonical_manifest(jsonl_text)


def handle_job(job: dict) -> dict:
    """Execute one job spec and return the response payload (status excluded)."""
    from . import writer_guard

    mode = job.get("mode")
    if mode not in JOB_MODES:
        raise InfraFailure(f"unknown arm job mode {mode!r}; expected one of {JOB_MODES}")
    agent_tasks: list | None = None
    agent_manifest: dict | None = None
    agent_bounds: dict = {}
    if mode == "agent_run":
        agent_tasks, agent_manifest, agent_bounds = _validate_agent_run_job(job)
    jsonl_text = job.get("jsonl")
    if not isinstance(jsonl_text, str) or not jsonl_text.strip():
        raise InfraFailure(f"arm job mode {mode!r} requires a non-empty 'jsonl' field")
    project_key = job.get("project_key")
    if not project_key:
        raise InfraFailure(f"arm job mode {mode!r} requires a 'project_key' field")

    _restore_and_arm(jsonl_text)
    from .corpus import canonical_corpus_digest

    expected_input_digest = canonical_corpus_digest(jsonl_text)
    digest_after_restore, _ = _arm_digest(project_key)

    response = {
        "restore_digest": digest_after_restore,
        "digest": digest_after_restore,
        "input_digest": expected_input_digest,
    }

    if mode == "retrieve":
        from .retrieval import retrieve_ranked_ids

        query_text = job.get("query_text", "")
        limit = int(job.get("limit", 10))
        params: dict = {}
        try:
            if "rrf_k" in job:
                params["rrf_k"] = int(job["rrf_k"])
            if "min_rrf_score" in job:
                params["min_rrf_score"] = float(job["min_rrf_score"])
        except (TypeError, ValueError) as exc:
            raise InfraFailure(f"arm job rrf_k/min_rrf_score is not a number: {exc}") from exc
        try:
            skew = float(job.get("clock_skew_s") or 0.0)
        except (TypeError, ValueError) as exc:
            raise InfraFailure(f"arm job 'clock_skew_s' is not a number: {exc}") from exc
        with _skewed_clock(skew):
            response["ids"] = retrieve_ranked_ids(query_text, project_key, limit=limit, **params)

    if mode == "agent_run":
        assert agent_tasks is not None and agent_manifest is not None
        response["trials"] = [
            run_agent_trial(task, agent_manifest, agent_bounds, project_key) for task in agent_tasks
        ]
        response["candidate_manifest"] = agent_manifest

    digest_final, manifest_final = _arm_digest(project_key)
    writer_guard.verify_digest_unchanged(
        digest_after_restore, digest_final, context=f"arm {mode} job"
    )
    response["digest"] = digest_final
    response["manifest"] = manifest_final
    return response


def main() -> int:
    """Read one job on stdin, write one response on stdout."""
    try:
        job = json.loads(sys.stdin.read())
    except ValueError as exc:
        sys.stdout.write(json.dumps({"status": "error", "error": f"unparseable job: {exc}"}))
        return 0
    if not isinstance(job, dict):
        sys.stdout.write(json.dumps({"status": "error", "error": "job must be a JSON object"}))
        return 0
    try:
        payload = handle_job(job)
    except Exception as exc:
        sys.stdout.write(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}))
        return 0
    payload["status"] = "ok"
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
