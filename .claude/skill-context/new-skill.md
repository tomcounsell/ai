# new-skill context — this repo (ai)

## Skill placement

Global vs project-only placement and the `RENAMED_REMOVALS` rule for moves are in CLAUDE.md
("Global vs. Project-Only Skills"). A global body stays repo-agnostic; put repo specifics in
`.claude/skill-context/<name>.md`.

## Creating a project Python tool

Tools live in `tools/<tool_name>/` and ship as `valor-*` console scripts.

- Layout: `__init__.py` (implementation, REQUIRED), `README.md` (REQUIRED), `manifest.json`,
  `tests/test_<tool>.py`. `/audit-tools` checks this shape.
- Public functions return `{"result": ...}` or `{"error": ...}`; `main()` is the argparse CLI
  entry point, printing the result or exiting 1 with the error on stderr.
- Register in `pyproject.toml [project.scripts]`: `valor-tool-name = "tools.tool_name:main"`,
  then `uv pip install -e .`. Give the CLI an informative `--help`.
- Document it in `docs/tools-reference.md` as an H3 `### Tool Name (\`tools.tool_name\`)`, one
  or two sentences on when to use it, then a fenced bash block of example invocations with
  trailing `# what this does` comments.
- LLM model names come from `config/models.py` (`MODEL_FAST`, `MODEL_REASONING`,
  `MODEL_VISION`, `MODEL_IMAGE_GEN`); never hard-code a model id.
- Files a tool produces reach Telegram through the bridge's `extract_files_from_response()`,
  or explicitly with `<<FILE:/path/to/file>>`.
- Done: tests pass under `scripts/pytest-clean.sh tools/<tool_name>/tests/`, and
  `python -m ruff check` and `python -m ruff format` are clean.

Reference implementations: `tools/image_gen/` (API + file output), `tools/image_analysis/`
(vision, multi-mode), `tools/sms_reader/` (system access, CLI subcommands).
