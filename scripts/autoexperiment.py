#!/usr/bin/env python3
"""Autonomous prompt optimization via cheap LLM hypothesis generation.

Runs overnight to iteratively improve system prompts by:
1. Extracting the current prompt from a source file
2. Running an eval to get a baseline score
3. Asking a cheap LLM to propose an improvement
4. Applying the change and re-running the eval
5. Keeping improvements, reverting regressions

Cost: ~$0.001 per iteration via OpenRouter ultra-cheap models.
Budget: Configurable ceiling (default $2.00) to prevent runaway spending.
Safety: Git branch isolation, auto-revert on regression, STOP sentinel file.

Usage:
    python scripts/autoexperiment.py --target observer --iterations 50 --budget 2.0
    python scripts/autoexperiment.py --target summarizer --dry-run
    python scripts/autoexperiment.py --list-targets
"""

import argparse
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests

from config.models import MODEL_EXPERIMENT, OPENROUTER_HAIKU

logger = logging.getLogger(__name__)

# OpenRouter endpoint
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Approximate costs per 1K tokens for budget tracking (conservative estimates)
COST_PER_1K_INPUT = 0.0001  # $0.0001 per 1K input tokens
COST_PER_1K_OUTPUT = 0.0003  # $0.0003 per 1K output tokens


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ExperimentTarget:
    """Definition of a prompt optimization target."""

    name: str  # e.g., "observer-routing"
    file_path: str  # e.g., "bridge/observer.py"
    extract_fn: Callable[[str], str]  # Extract prompt from file content
    inject_fn: Callable[[str, str], str]  # Inject modified prompt into file
    eval_fn: Callable[[], float]  # Run evaluation, return score 0-1
    metric_direction: str  # "higher" or "lower"
    description: str  # Human-readable description
    model: str = field(default_factory=lambda: MODEL_EXPERIMENT)


@dataclass
class ExperimentResult:
    """Result of a single experiment iteration."""

    iteration: int
    hypothesis: str
    diff: str
    baseline_score: float
    new_score: float
    kept: bool
    cost_usd: float
    timestamp: str


# ---------------------------------------------------------------------------
# OpenRouter API helper
# ---------------------------------------------------------------------------


def call_openrouter(
    prompt: str,
    model: str = MODEL_EXPERIMENT,
    system: str | None = None,
    max_tokens: int = 2048,
) -> tuple[str, float]:
    """Call OpenRouter API and return (content, estimated_cost_usd).

    Args:
        prompt: User message content.
        model: OpenRouter model ID.
        system: Optional system prompt.
        max_tokens: Maximum response tokens.

    Returns:
        Tuple of (response_content, estimated_cost_usd).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    cost = (input_tokens / 1000 * COST_PER_1K_INPUT) + (output_tokens / 1000 * COST_PER_1K_OUTPUT)

    return content, cost


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def load_jsonl(path: str) -> list[dict]:
    """Load a JSONL file, returning a list of dicts."""
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def append_jsonl(path: str, data: dict) -> None:
    """Append a single JSON object as a line to a JSONL file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(data) + "\n")


# ---------------------------------------------------------------------------
# Extract / Inject functions for each target
# ---------------------------------------------------------------------------

# Pattern to match triple-quoted string assigned to a variable.
# Handles both regular triple-quote and backslash-continued opening.
_PROMPT_VAR_PATTERN = r'({var_name}\s*=\s*""")\\?\n(.*?)(""")'


def _extract_prompt_var(file_content: str, var_name: str) -> str:
    """Extract a triple-quoted string variable from file content.

    Handles both `\"\"\"\\\\\\n` (backslash-newline continuation) and plain `\"\"\"\\n`.
    """
    pattern = re.compile(
        rf'{var_name}\s*=\s*"""\\?\n?(.*?)"""',
        re.DOTALL,
    )
    match = pattern.search(file_content)
    if not match:
        raise ValueError(f"Could not find {var_name} in file content")
    return match.group(1)


def _inject_prompt_var(file_content: str, var_name: str, new_prompt: str) -> str:
    """Replace a triple-quoted string variable in file content."""
    pattern = re.compile(
        rf'({var_name}\s*=\s*""")\\?\n?(.*?)(""")',
        re.DOTALL,
    )
    match = pattern.search(file_content)
    if not match:
        raise ValueError(f"Could not find {var_name} in file content")

    # Preserve the original opening (with or without backslash continuation)
    original_opening = file_content[match.start() : match.start(2)]
    replacement = f"{original_opening}{new_prompt}{match.group(3)}"
    return file_content[: match.start()] + replacement + file_content[match.end() :]


def extract_observer_prompt(file_content: str) -> str:
    """Extract OBSERVER_SYSTEM_PROMPT_BODY from observer.py content.

    Only the static body template is an experiment target. The dynamic
    prompt construction logic (_build_observer_system_prompt) is off-limits.
    """
    return _extract_prompt_var(file_content, "OBSERVER_SYSTEM_PROMPT_BODY")


def inject_observer_prompt(file_content: str, new_prompt: str) -> str:
    """Inject a new OBSERVER_SYSTEM_PROMPT_BODY into observer.py content."""
    return _inject_prompt_var(file_content, "OBSERVER_SYSTEM_PROMPT_BODY", new_prompt)


def extract_summarizer_prompt(file_content: str) -> str:
    """Extract SUMMARIZER_SYSTEM_PROMPT from summarizer.py content."""
    return _extract_prompt_var(file_content, "SUMMARIZER_SYSTEM_PROMPT")


def inject_summarizer_prompt(file_content: str, new_prompt: str) -> str:
    """Inject a new SUMMARIZER_SYSTEM_PROMPT into summarizer.py content."""
    return _inject_prompt_var(file_content, "SUMMARIZER_SYSTEM_PROMPT", new_prompt)


# ---------------------------------------------------------------------------
# Eval functions
# ---------------------------------------------------------------------------


def eval_observer(corpus_path: str | None = None) -> float:
    """Evaluate observer routing accuracy against eval corpus.

    Loads eval_corpus.jsonl, runs each scenario through the observer prompt
    using a cheap LLM, checks if the decision matches expected.

    Returns accuracy as float 0-1.
    """
    if corpus_path is None:
        corpus_path = "data/experiments/observer/eval_corpus.jsonl"

    corpus = load_jsonl(corpus_path)
    if not corpus:
        logger.warning("Observer eval corpus is empty")
        return 0.0

    # Read current observer prompt
    observer_path = "bridge/observer.py"
    with open(observer_path) as f:
        file_content = f.read()
    prompt_text = extract_observer_prompt(file_content)

    correct = 0
    total = 0

    for scenario in corpus:
        input_data = scenario.get("input", {})
        expected = scenario.get("expected", "")

        # Build eval prompt
        if isinstance(input_data, dict):
            message = input_data.get("message", "")
            session_state = json.dumps(input_data.get("session_state", {}))
            eval_prompt = (
                f"Given this system prompt:\n{prompt_text}\n\n"
                f"Worker output:\n{message}\n\n"
                f"Session state:\n{session_state}\n\n"
                f"What should the decision be? Reply with exactly one word: STEER or DELIVER"
            )
        else:
            eval_prompt = (
                f"Given this system prompt:\n{prompt_text}\n\n"
                f"Input:\n{input_data}\n\n"
                f"What should the decision be? Reply with exactly one word: STEER or DELIVER"
            )

        try:
            response, _ = call_openrouter(eval_prompt, model=OPENROUTER_HAIKU, max_tokens=10)
            decision = response.strip().upper()
            # Extract just STEER or DELIVER from response
            if "STEER" in decision:
                decision = "STEER"
            elif "DELIVER" in decision:
                decision = "DELIVER"

            if decision == expected.upper():
                correct += 1
            total += 1
        except Exception as e:
            logger.warning(f"Observer eval error for scenario: {e}")
            total += 1

    return correct / total if total > 0 else 0.0


def eval_summarizer(corpus_path: str | None = None) -> float:
    """Evaluate summarizer voice quality using AI judge.

    Loads eval_samples.jsonl, runs each through the summarizer prompt,
    judges output quality (direct, concise, no preamble).

    Returns average quality score 0-1.
    """
    if corpus_path is None:
        corpus_path = "data/experiments/summarizer/eval_samples.jsonl"

    corpus = load_jsonl(corpus_path)
    if not corpus:
        logger.warning("Summarizer eval corpus is empty")
        return 0.0

    # Read current summarizer prompt
    summarizer_path = "bridge/summarizer.py"
    with open(summarizer_path) as f:
        file_content = f.read()
    prompt_text = extract_summarizer_prompt(file_content)

    scores = []
    for sample in corpus:
        input_text = sample.get("input", "")
        criteria = sample.get("criteria", ["direct", "concise", "no_preamble"])

        # Generate summary using the prompt
        try:
            summary, _ = call_openrouter(
                f"Summarize this output:\n\n{input_text}",
                model=OPENROUTER_HAIKU,
                system=prompt_text,
                max_tokens=512,
            )

            # Judge the quality
            criteria_str = ", ".join(criteria)
            judge_prompt = (
                f"Rate this summary on a scale of 0.0 to 1.0 for these criteria: {criteria_str}\n\n"
                f"Summary:\n{summary}\n\n"
                f"Original:\n{input_text[:500]}\n\n"
                f"Reply with ONLY a decimal number between 0.0 and 1.0"
            )
            score_response, _ = call_openrouter(judge_prompt, model=OPENROUTER_HAIKU, max_tokens=10)
            # Parse score
            score_match = re.search(r"(\d+\.?\d*)", score_response)
            if score_match:
                score = min(1.0, max(0.0, float(score_match.group(1))))
                scores.append(score)
        except Exception as e:
            logger.warning(f"Summarizer eval error: {e}")

    return sum(scores) / len(scores) if scores else 0.0


# ---------------------------------------------------------------------------
# ExperimentRunner
# ---------------------------------------------------------------------------


class ExperimentRunner:
    """Core experiment loop for autonomous prompt optimization.

    Iteratively:
    1. Runs eval to get baseline score
    2. Asks cheap LLM for a hypothesis (proposed prompt change)
    3. Applies the change
    4. Re-runs eval
    5. Keeps if improved, reverts if not

    Args:
        target: ExperimentTarget defining what to optimize.
        branch: Git branch name for experiment isolation.
        dry_run: If True, skip git operations.
        results_dir: Directory for JSONL result logs.
        stop_file: Path to STOP sentinel file.
    """

    def __init__(
        self,
        target: ExperimentTarget,
        branch: str | None = None,
        dry_run: bool = False,
        results_dir: str | None = None,
        stop_file: str | None = None,
    ):
        self.target = target
        self.branch = branch or f"experiment/{target.name}"
        self.dry_run = dry_run
        self.results_dir = results_dir or f"data/experiments/{target.name}"
        self.stop_file = stop_file or "data/experiments/STOP"
        self.total_cost = 0.0
        self._iteration = 0

    def _should_stop(self) -> bool:
        """Check if the STOP sentinel file exists."""
        return Path(self.stop_file).exists()

    def _read_source(self) -> str:
        """Read the target source file."""
        with open(self.target.file_path) as f:
            return f.read()

    def _write_source(self, content: str) -> None:
        """Write the target source file."""
        with open(self.target.file_path, "w") as f:
            f.write(content)

    def _git_commit(self, message: str) -> None:
        """Commit current changes if not in dry-run mode."""
        if self.dry_run:
            return
        try:
            subprocess.run(
                ["git", "add", self.target.file_path],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git commit failed: {e}")

    def _git_revert(self) -> None:
        """Revert changes to target file if not in dry-run mode."""
        if self.dry_run:
            return
        try:
            subprocess.run(
                ["git", "checkout", "--", self.target.file_path],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git revert failed: {e}")

    def _generate_hypothesis(self, current_prompt: str, score: float) -> str:
        """Ask the LLM to propose a prompt improvement."""
        system = (
            "You are a prompt optimization expert. Your job is to propose a specific, "
            "targeted improvement to a system prompt. Focus on ONE small change that "
            "could improve the evaluation score. Return ONLY the complete modified prompt "
            "text — no explanations, no markdown, no code fences."
        )
        user_prompt = (
            f"## Current System Prompt\n{current_prompt}\n\n"
            f"## Current Eval Score\n{score:.3f} "
            f"(target direction: {self.target.metric_direction})\n\n"
            f"## Target Description\n{self.target.description}\n\n"
            f"## Instructions\n"
            f"Propose a modified version of the system prompt that could improve the score. "
            f"Make ONE targeted change — do not rewrite the entire prompt. "
            f"Return ONLY the complete modified prompt text."
        )

        response, cost = call_openrouter(
            user_prompt,
            model=self.target.model,
            system=system,
            max_tokens=4096,
        )
        self.total_cost += cost
        return response.strip()

    def _is_improvement(self, baseline: float, new_score: float) -> bool:
        """Check if the new score is an improvement."""
        if self.target.metric_direction == "higher":
            return new_score > baseline
        else:
            return new_score < baseline

    def run_one(self) -> ExperimentResult:
        """Run a single experiment iteration.

        Returns:
            ExperimentResult with the outcome.
        """
        self._iteration += 1
        timestamp = datetime.now(UTC).isoformat()

        # Read current source and extract prompt
        source = self._read_source()
        current_prompt = self.target.extract_fn(source)

        # Get baseline score
        baseline_score = self.target.eval_fn()

        # Generate hypothesis
        hypothesis = self._generate_hypothesis(current_prompt, baseline_score)

        # Apply the change
        try:
            modified_source = self.target.inject_fn(source, hypothesis)
        except ValueError as e:
            logger.warning(f"Injection failed: {e}")
            return ExperimentResult(
                iteration=self._iteration,
                hypothesis=hypothesis[:200],
                diff="INJECTION_FAILED",
                baseline_score=baseline_score,
                new_score=baseline_score,
                kept=False,
                cost_usd=self.total_cost,
                timestamp=timestamp,
            )

        self._write_source(modified_source)

        # Evaluate
        new_score = self.target.eval_fn()

        # Decide: keep or revert
        kept = self._is_improvement(baseline_score, new_score)
        if kept:
            self._git_commit(
                f"autoexperiment: {self.target.name} iter {self._iteration} "
                f"({baseline_score:.3f} -> {new_score:.3f})"
            )
            logger.info(
                f"[autoexperiment] Kept iteration {self._iteration}: "
                f"{baseline_score:.3f} -> {new_score:.3f}"
            )
        else:
            # Revert
            self._write_source(source)
            self._git_revert()
            logger.info(
                f"[autoexperiment] Reverted iteration {self._iteration}: "
                f"{baseline_score:.3f} -> {new_score:.3f} (no improvement)"
            )

        # Build diff for logging
        diff = ""
        if current_prompt != hypothesis:
            diff = "--- baseline\n+++ hypothesis\n"
            for old_line, new_line in zip(current_prompt.splitlines(), hypothesis.splitlines()):
                if old_line != new_line:
                    diff += f"- {old_line}\n+ {new_line}\n"

        result = ExperimentResult(
            iteration=self._iteration,
            hypothesis=hypothesis[:500],  # Truncate for logging
            diff=diff[:1000],
            baseline_score=baseline_score,
            new_score=new_score,
            kept=kept,
            cost_usd=self.total_cost,
            timestamp=timestamp,
        )

        # Log result
        log_path = os.path.join(
            self.results_dir,
            f"{datetime.now(UTC).strftime('%Y%m%d')}.jsonl",
        )
        append_jsonl(log_path, asdict(result))

        return result

    def run_loop(self, n: int = 100, budget_usd: float = 2.0) -> list[ExperimentResult]:
        """Run multiple experiment iterations with budget and stop controls.

        Args:
            n: Maximum number of iterations.
            budget_usd: Maximum total cost in USD.

        Returns:
            List of ExperimentResults.
        """
        results = []

        for i in range(n):
            # Check stop conditions
            if self._should_stop():
                logger.info("[autoexperiment] STOP sentinel detected, halting")
                break

            if self.total_cost >= budget_usd:
                logger.info(
                    f"[autoexperiment] Budget exhausted: "
                    f"${self.total_cost:.4f} >= ${budget_usd:.2f}"
                )
                break

            try:
                result = self.run_one()
                results.append(result)
            except Exception as e:
                logger.error(f"[autoexperiment] Iteration {i + 1} failed: {e}")
                # Don't crash the loop on individual failures
                continue

        return results

    def report(self) -> dict:
        """Generate a summary report of experiment results."""
        log_dir = Path(self.results_dir)
        all_results = []
        for f in sorted(log_dir.glob("*.jsonl")):
            all_results.extend(load_jsonl(str(f)))

        if not all_results:
            return {"target": self.target.name, "iterations": 0, "improvements": 0}

        improvements = [r for r in all_results if r.get("kept")]
        scores = [r["new_score"] for r in all_results]

        return {
            "target": self.target.name,
            "iterations": len(all_results),
            "improvements": len(improvements),
            "best_score": max(scores) if scores else 0,
            "worst_score": min(scores) if scores else 0,
            "total_cost_usd": sum(r.get("cost_usd", 0) for r in all_results),
        }


# ---------------------------------------------------------------------------
# Target registry
# ---------------------------------------------------------------------------


def get_targets() -> dict[str, ExperimentTarget]:
    """Return all registered experiment targets."""
    return {
        "observer": ExperimentTarget(
            name="observer",
            file_path="bridge/observer.py",
            extract_fn=extract_observer_prompt,
            inject_fn=inject_observer_prompt,
            eval_fn=eval_observer,
            metric_direction="higher",
            description=(
                "Observer routing accuracy: decides STEER vs DELIVER for SDLC pipeline. "
                "Higher accuracy means fewer false deliveries and fewer missed steers."
            ),
        ),
        "summarizer": ExperimentTarget(
            name="summarizer",
            file_path="bridge/summarizer.py",
            extract_fn=extract_summarizer_prompt,
            inject_fn=inject_summarizer_prompt,
            eval_fn=eval_summarizer,
            metric_direction="higher",
            description=(
                "Summarizer voice quality: direct, concise, no preamble PM-facing summaries. "
                "Higher score means better adherence to format rules and tone."
            ),
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous prompt optimization via cheap LLM experiments"
    )
    parser.add_argument(
        "--target",
        choices=["observer", "summarizer"],
        help="Which target to optimize",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Maximum number of iterations (default: 100)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=2.0,
        help="Maximum budget in USD (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without git operations",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the hypothesis generation model",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="List available targets and exit",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print report for a target and exit",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    targets = get_targets()

    if args.list_targets:
        print("Available targets:")
        for name, target in targets.items():
            print(f"  {name}: {target.description}")
        return

    if not args.target:
        parser.error("--target is required (or use --list-targets)")

    target = targets[args.target]
    if args.model:
        target.model = args.model

    if args.report:
        runner = ExperimentRunner(target=target, dry_run=True)
        report = runner.report()
        print(json.dumps(report, indent=2))
        return

    runner = ExperimentRunner(
        target=target,
        dry_run=args.dry_run,
    )

    print(
        f"Starting autoexperiment: target={target.name}, "
        f"iterations={args.iterations}, budget=${args.budget:.2f}"
    )
    results = runner.run_loop(n=args.iterations, budget_usd=args.budget)

    # Print summary
    kept = [r for r in results if r.kept]
    print(f"\nCompleted {len(results)} iterations, {len(kept)} improvements kept")
    print(f"Total cost: ${runner.total_cost:.4f}")

    if kept:
        best = max(kept, key=lambda r: r.new_score)
        print(f"Best score: {best.new_score:.3f} (iteration {best.iteration})")


if __name__ == "__main__":
    main()
