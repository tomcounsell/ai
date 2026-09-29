# audit-tools context — this repo (ai)

- **CLI naming (`cli-quality`):** tools are console scripts named
  `valor-<name>` in `pyproject.toml [project.scripts]` (e.g. `valor-sms-reader`). A tool
  registered under any other name fails the naming convention. Run `valor-<name> --help`.
- **Test runner (`tests-passing`):** `scripts/pytest-clean.sh tools/<name>/tests/`, never bare
  pytest (see CLAUDE.md).
- **Scaffolding missing structure:** `/new-skill` covers tool creation; its context file holds
  this repo's tool layout.
