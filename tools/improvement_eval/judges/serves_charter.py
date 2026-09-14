"""The serves-charter judge for the frozen-input evaluation harness (#3216).

Scores one candidate output against ``ImprovementCharter.text``. The shape
copies ``tools.cross_vendor_judge`` rather than inventing one: a reserved
judge id proven disjoint from the roster, a status-discriminated envelope
(``ok`` versus ``skipped``), and typed coercion of every response field with
a refusal (``skipped``) instead of a fabricated verdict when the response is
unparseable.

Charter section 12 digest rule: the prompt carries the charter text verbatim
and the envelope carries the digest that text hashed to, so the verdict can
be re-read years later against the exact authority it was measured under.
The charter text and digest arrive as opaque caller-supplied strings (the
runner pins them via ``ImprovementCharter.pinned()``); this module never
imports the charter model and never writes it.

Charter section 7 provider routing: ``tools.improvement_eligibility``'s
``is_open_source`` decides. True routes the judge to a non-Claude provider
within the inference budget; False keeps it on the Claude subscription. The
verdict is one input to the consensus envelope and gates nothing on its own.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)

#: Reserved judge id, disjoint from "code-quality", "risk", "cross-vendor".
SERVES_CHARTER_JUDGE_ID = "serves-charter"

#: Provider selected for open-source projects: any provider within budget.
OPEN_SOURCE_PROVIDER = "openai"

#: Provider selected for client work: the Claude subscription only.
SUBSCRIPTION_PROVIDER = "claude-subscription"

#: Wall-clock cap (seconds) on one headless ``claude -p`` judge call. A judge
#: verdict is a single bounded prompt; five minutes covers a slow model turn
#: and turns a hung subprocess into a ``skipped`` envelope instead of a stall.
SUBSCRIPTION_JUDGE_TIMEOUT_S = 300.0

_SYSTEM_PROMPT = """\
You are judging whether a candidate improvement serves the charter quoted
below. Respond with ONLY a valid JSON object — no prose, no markdown fences,
just the raw JSON.

Required JSON schema (respond with exactly this structure):
{
  "serves_charter": <true or false>,
  "blockers": <integer count of charter conflicts>,
  "confidence": <float 0.0-1.0>,
  "reasoning_summary": "<brief rationale naming the charter section>"
}

Rules:
- "serves_charter" must be exactly true or false.
- "blockers" must be a non-negative integer (not a boolean).
- "confidence" must be a float between 0.0 and 1.0.
- "reasoning_summary" must be a non-empty string.
"""

_USER_TEMPLATE = """\
Charter (the sole authority; quote no other document):

{charter_text}

---

Candidate output from blinded arm {blinded_arm_id}:

{candidate_output}

---

Does the candidate output serve the charter above? Return the JSON object.
"""


def build_prompt(charter_text: str, candidate_output: str, blinded_arm_id: str) -> str:
    """Assemble the judge prompt carrying the charter text verbatim.

    The candidate is named only by its blinded arm id; no branch, author,
    or manifest surface may be passed in.
    """
    return (
        _SYSTEM_PROMPT
        + "\n"
        + _USER_TEMPLATE.format(
            charter_text=charter_text,
            candidate_output=candidate_output,
            blinded_arm_id=blinded_arm_id,
        )
    )


def select_provider(project_key: str) -> dict:
    """Choose the judge provider per charter section 7.

    Calls ``tools.improvement_eligibility.is_open_source`` (imported late so
    tests can patch the guard without importing this module's transport) and
    does not reimplement the decision. Open-source work may use any provider
    within the inference budget; client work stays on the subscription.
    """
    from config.settings import settings
    from tools.improvement_eligibility import is_open_source

    if is_open_source(project_key):
        return {"provider": OPEN_SOURCE_PROVIDER, "model": settings.sdlc_review_cross_vendor_model}
    return {"provider": SUBSCRIPTION_PROVIDER, "model": "claude"}


def _complete_via_openai(*, provider: str, model: str, prompt: str) -> str:
    """Run the prompt on the budgeted provider. Raises on transport failure."""
    from openai import OpenAI

    from config.settings import settings

    client = OpenAI(api_key=settings.api.openai_api_key)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        seed=42,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
        ],
    )
    return response.choices[0].message.content or "{}"


#: The headless ``claude -p`` argv for a judge call, prompt appended last.
#: ``--tools=`` disables every built-in tool and ``--strict-mcp-config``
#: loads no MCP server (none is passed), so the judge cannot open the repo
#: or ``data/improvement_content/`` and de-blind itself; with no tools the
#: call is a single model turn. Each flag is one ``--flag=value`` argv
#: element because the CLI's tool options are variadic and a bare value
#: would swallow the prompt. Verified live on the installed CLI.
SUBSCRIPTION_JUDGE_ARGV = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--tools=",
    "--strict-mcp-config",
]


def _complete_via_subscription(*, provider: str, model: str, prompt: str) -> str:
    """Run the prompt under Claude subscription auth. Raises on failure.

    Runs from an empty temporary directory so the judge's working directory
    holds nothing to read, and with every tool disabled (see
    :data:`SUBSCRIPTION_JUDGE_ARGV`): the only thing the judge sees is the
    prompt, which carries the blinded arm id and nothing else about the
    candidate.
    """
    with tempfile.TemporaryDirectory(prefix="serves-charter-judge-") as empty_cwd:
        completed = subprocess.run(
            [*SUBSCRIPTION_JUDGE_ARGV, prompt],
            capture_output=True,
            text=True,
            timeout=SUBSCRIPTION_JUDGE_TIMEOUT_S,
            cwd=empty_cwd,
        )
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        raise RuntimeError(f"claude -p exited {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError(f"claude -p returned non-JSON output: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        return payload["result"]
    return completed.stdout


def _coerce_judge_fields(raw: object) -> tuple[dict | None, str | None]:
    """Coerce the parsed model response. Returns (fields, error_reason).

    ``None`` fields with a reason means the response is unusable and the
    caller must emit ``skipped`` rather than a verdict. A missing or
    uncoercible ``serves_charter`` is always unusable: defaulting it would
    fabricate the very verdict the judge exists to produce.
    """
    if not isinstance(raw, dict):
        return None, f"Model returned unexpected type: {type(raw).__name__}"

    if "serves_charter" not in raw:
        return None, "Model response carries no serves_charter verdict"

    serves_raw = raw["serves_charter"]
    if isinstance(serves_raw, bool):
        serves = serves_raw
    elif isinstance(serves_raw, (int, float)) and not isinstance(serves_raw, bool):
        if serves_raw in (0, 1):
            serves = bool(serves_raw)
        else:
            return None, f"serves_charter field is not boolean: {serves_raw!r}"
    elif isinstance(serves_raw, str):
        lowered = serves_raw.strip().lower()
        if lowered in ("true", "yes", "aligned", "approve", "approved"):
            serves = True
        elif lowered in ("false", "no", "misaligned", "reject", "changes requested"):
            serves = False
        else:
            return None, f"serves_charter field is not boolean: {serves_raw!r}"
    else:
        return None, f"serves_charter field is not boolean: {serves_raw!r}"

    blockers_raw = raw.get("blockers")
    if blockers_raw is None:
        blockers = 0 if serves else 1
    elif isinstance(blockers_raw, bool):
        return None, f"blockers field is bool ({blockers_raw!r}), expected int"
    else:
        try:
            blockers = int(blockers_raw)
        except (TypeError, ValueError):
            return None, f"blockers field is not numeric: {blockers_raw!r}"
    blockers = max(0, blockers)

    try:
        confidence = float(raw.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    reasoning_summary = raw.get("reasoning_summary", "")
    if not isinstance(reasoning_summary, str):
        reasoning_summary = str(reasoning_summary)
    reasoning_summary = reasoning_summary.strip() or "No summary provided."

    return (
        {
            "serves_charter": serves,
            "blockers": blockers,
            "confidence": confidence,
            "reasoning_summary": reasoning_summary,
        },
        None,
    )


def _emit_skipped(reason: str, meta: dict) -> dict:
    envelope = {"status": "skipped", "reason": reason, "meta": meta}
    logger.warning("serves_charter judge skipped: %s", reason)
    return envelope


def run_serves_charter_judge(
    candidate_output: str,
    *,
    charter_text: str,
    charter_digest: str,
    project_key: str,
    blinded_arm_id: str = "arm-a",
    trial_id: str | None = None,
    _complete=None,
) -> dict:
    """Score one candidate output against the charter text.

    Returns a status-discriminated envelope. The inner judge dict carries
    ``judge_id``/``verdict``/``blockers`` and stays consumable by
    ``agent.sdlc_review_consensus.compute_consensus`` unchanged. ``_complete``
    injects the provider transport; the default routes per :func:`select_provider`.
    """
    route = select_provider(project_key)
    provider, model = route["provider"], route["model"]
    meta: dict = {
        "provider": provider,
        "model": model,
        "blinded_arm_id": blinded_arm_id,
        "trial_id": trial_id,
    }

    if not charter_text or not charter_text.strip():
        return _emit_skipped("No charter text to judge against", meta)

    prompt = build_prompt(charter_text, candidate_output, blinded_arm_id)

    complete = _complete
    if complete is None:
        if provider == SUBSCRIPTION_PROVIDER:
            complete = _complete_via_subscription
        else:
            complete = _complete_via_openai
    try:
        raw_content = complete(provider=provider, model=model, prompt=prompt)
    except Exception as exc:
        return _emit_skipped(f"Judge provider call failed: {type(exc).__name__}: {exc}", meta)

    try:
        raw = json.loads(raw_content)
    except (ValueError, TypeError) as exc:
        return _emit_skipped(f"Model returned non-JSON content: {exc}", meta)

    coerced, error_reason = _coerce_judge_fields(raw)
    if error_reason:
        return _emit_skipped(error_reason, {**meta, "raw": str(raw_content)[:500]})

    judge = {
        "judge_id": SERVES_CHARTER_JUDGE_ID,
        "verdict": "APPROVED" if coerced["serves_charter"] else "CHANGES REQUESTED",
        "blockers": coerced["blockers"],
        "confidence": coerced["confidence"],
        "reasoning_summary": coerced["reasoning_summary"],
        "charter_digest": charter_digest,
        "meta": meta,
    }
    envelope = {"status": "ok", "judge": judge}
    logger.info(
        "serves_charter judge ran: provider=%s verdict=%s charter_digest=%s",
        provider,
        judge["verdict"],
        charter_digest,
    )
    return envelope
