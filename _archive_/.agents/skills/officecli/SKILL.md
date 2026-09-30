---
name: officecli
description: "Create, inspect, or edit Office files through officecli when that CLI is requested or appropriate to the existing workflow."
---

# Officecli

Check `officecli --version` and its own `--help` first. Commands in [the command reference](COMMANDS.md) may exceed the fleet's pinned version; use only the installed binary's supported schema. Fleet upgrades belong in `scripts/update/officecli.py`, not an unpinned curl installer.
Use the installed document, spreadsheet, or presentation skill for format-specific creation and render/verification practices. Preserve a user-requested officecli workflow. Quote bracketed element paths in the shell, inspect before changing, validate the saved file, and render it to catch layout defects. An unspecified doc means Markdown, so do not invoke Office tooling merely because the user said “a doc”.
