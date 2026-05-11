"""Harness-agnostic prompt shim used by the /setup skill.

The /setup skill calls into this module via a Bash subshell when it needs
input from the user (e.g. for the vault-location picker). This module
detects the active harness and dispatches:

  1. ``VALOR_HARNESS=claude-code`` → emit a JSON instruction on stdout
     and raise ``InstallPromptDeferred``. The skill is expected to parse
     the JSON, render a native ``AskUserQuestion``, capture the answer,
     and re-invoke this module with ``--answer-file`` to deliver the
     answer back into the install flow.
  2. ``sys.stdin.isatty()`` → readline-based prompt (interactive shell).
  3. otherwise → raise ``InstallPromptUnavailable`` so the caller knows
     no input source is available and can fail loudly.

The two public entry points are :func:`ask_choice` and :func:`ask_input`.
Both are pure delegations to the harness adapter — they contain no
adapter-specific logic themselves.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any


class InstallPromptUnavailable(RuntimeError):  # noqa: N818
    """No prompt source is available (no TTY, no harness env var)."""


class InstallPromptDeferred(RuntimeError):  # noqa: N818
    """The Claude Code adapter emitted a JSON instruction; the caller
    (the /setup skill) is expected to render it via ``AskUserQuestion``
    and pass the answer back via a follow-up invocation."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask_choice(
    question: str,
    *,
    options: list[dict[str, str]],
    header: str,
) -> str:
    """Prompt the user to pick one of ``options`` by index.

    Each option is a dict with at least ``label`` and ``value`` keys;
    ``description`` is optional. Returns the chosen option's ``value``.
    Raises :class:`InstallPromptDeferred` for the Claude Code adapter
    (the JSON instruction is written to stdout first).
    """
    return _adapter().ask_choice(question, options, header)


def ask_input(
    question: str,
    *,
    header: str,
    default: str | None = None,
    validator: Callable[[str], str | None] | None = None,
) -> str:
    """Prompt the user for free-form input.

    ``default`` is returned if the user enters an empty string. ``validator``
    receives the input and returns ``None`` to accept or an error message
    to reject (TTY adapter re-prompts; Claude Code adapter ignores).
    """
    return _adapter().ask_input(question, header, default, validator)


# ---------------------------------------------------------------------------
# Adapter selection
# ---------------------------------------------------------------------------


def _adapter() -> _Adapter:
    if os.environ.get("VALOR_HARNESS") == "claude-code":
        return _ClaudeCodeAdapter()
    if sys.stdin.isatty():
        return _TTYAdapter()
    return _NoHarnessAdapter()


class _Adapter:
    """Adapter protocol — concrete subclasses implement the two methods."""

    def ask_choice(
        self, question: str, options: list[dict[str, str]], header: str
    ) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def ask_input(
        self,
        question: str,
        header: str,
        default: str | None,
        validator: Callable[[str], str | None] | None,
    ) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class _TTYAdapter(_Adapter):
    def ask_choice(self, question: str, options: list[dict[str, str]], header: str) -> str:
        print(f"\n[{header}] {question}\n")
        for i, opt in enumerate(options, start=1):
            label = opt.get("label", opt.get("value", "?"))
            description = opt.get("description")
            if description:
                print(f"  {i}. {label} — {description}")
            else:
                print(f"  {i}. {label}")
        while True:
            raw = input(f"\nEnter choice [1-{len(options)}]: ").strip()
            try:
                idx = int(raw)
            except ValueError:
                print(f"  invalid: '{raw}' is not a number, try again")
                continue
            if 1 <= idx <= len(options):
                return options[idx - 1]["value"]
            print(f"  invalid: {idx} is out of range [1-{len(options)}], try again")

    def ask_input(
        self,
        question: str,
        header: str,
        default: str | None,
        validator: Callable[[str], str | None] | None,
    ) -> str:
        prompt = f"\n[{header}] {question}"
        if default is not None:
            prompt += f"\n[default: {default}]"
        prompt += "\n> "
        while True:
            raw = input(prompt).strip()
            if not raw and default is not None:
                return default
            if validator is not None:
                err = validator(raw)
                if err is not None:
                    print(f"  invalid: {err}, try again")
                    continue
            return raw


class _ClaudeCodeAdapter(_Adapter):
    """Emit a JSON instruction the /setup skill renders via ``AskUserQuestion``."""

    def ask_choice(self, question: str, options: list[dict[str, str]], header: str) -> str:
        self._emit(
            {
                "kind": "ask_choice",
                "question": question,
                "header": header,
                "options": options,
            }
        )
        raise InstallPromptDeferred(
            "Claude Code adapter: JSON emitted on stdout; rendering deferred to skill."
        )

    def ask_input(
        self,
        question: str,
        header: str,
        default: str | None,
        validator: Callable[[str], str | None] | None,
    ) -> str:
        instruction: dict[str, Any] = {
            "kind": "ask_input",
            "question": question,
            "header": header,
        }
        if default is not None:
            instruction["default"] = default
        self._emit(instruction)
        raise InstallPromptDeferred(
            "Claude Code adapter: JSON emitted on stdout; rendering deferred to skill."
        )

    @staticmethod
    def _emit(instruction: dict[str, Any]) -> None:
        json.dump(instruction, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()


class _NoHarnessAdapter(_Adapter):
    def ask_choice(self, question: str, options: list[dict[str, str]], header: str) -> str:
        raise InstallPromptUnavailable(
            f"No prompt source available (no TTY, VALOR_HARNESS unset). "
            f"Question was: {question!r}. Re-run /setup from an interactive "
            f"shell or set VALOR_HARNESS=claude-code."
        )

    def ask_input(
        self,
        question: str,
        header: str,
        default: str | None,
        validator: Callable[[str], str | None] | None,
    ) -> str:
        raise InstallPromptUnavailable(
            f"No prompt source available (no TTY, VALOR_HARNESS unset). "
            f"Question was: {question!r}. Re-run /setup from an interactive "
            f"shell or set VALOR_HARNESS=claude-code."
        )


# ---------------------------------------------------------------------------
# CLI entry: `python -m tools.install.prompt vault-picker`
# ---------------------------------------------------------------------------

VAULT_PICKER_OPTIONS = [
    {
        "label": "~/.valor/ (Recommended)",
        "value": "~/.valor",
        "description": (
            "Hidden home directory. No sync. Single-machine, no cloud backup. "
            "Best security posture: secrets stay in <vault>/.env (0600) and "
            "the launchd plists never bake in API keys."
        ),
    },
    {
        "label": "~/Documents/Valor/",
        "value": "~/Documents/Valor",
        "description": (
            "Documents folder. Often iCloud-synced if iCloud Documents is on. "
            "TCC-restricted: install scripts will bake every .env key into "
            "each launchd plist's EnvironmentVariables dict (chmod 0600) "
            "because launchd processes can't read iCloud-synced files at "
            "runtime."
        ),
    },
    {
        "label": "~/iCloud Drive/Valor/",
        "value": "~/iCloud Drive/Valor",
        "description": (
            "Explicit iCloud Drive root. TCC-restricted: install scripts "
            "bake secrets into launchd plists (chmod 0600). Subject to "
            "file-on-demand evictions."
        ),
    },
    {
        "label": "~/Desktop/Valor/",
        "value": "~/Desktop/Valor",
        "description": (
            "Original default. iCloud-synced via Desktop. TCC-restricted: "
            "install scripts bake secrets into launchd plists (chmod 0600). "
            "Known iCloud rename bug if path is recreated."
        ),
    },
    {
        "label": "Custom path…",
        "value": "__custom__",
        "description": (
            "Free-form: I'll type one. If the path is under ~/Desktop, "
            "~/Documents, or ~/iCloud Drive, secrets will be baked into "
            "launchd plists; otherwise secrets stay in .env."
        ),
    },
]


def _vault_picker() -> str:
    choice = ask_choice(
        "Where should Valor's secrets vault live?",
        options=VAULT_PICKER_OPTIONS,
        header="Vault path",
    )
    if choice == "__custom__":
        return ask_input(
            "Enter the absolute path for your Valor vault directory",
            header="Vault path",
            default=None,
        )
    return choice


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tools.install.prompt")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("vault-picker", help="Run the /setup vault-location picker")
    args = parser.parse_args(argv)

    if args.cmd == "vault-picker":
        try:
            chosen = _vault_picker()
        except InstallPromptDeferred:
            # Adapter has emitted the JSON instruction on stdout; signal the
            # skill to defer to AskUserQuestion via a distinct exit code.
            return 78  # EX_CONFIG — "config required, ask the user"
        except InstallPromptUnavailable as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(chosen)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
