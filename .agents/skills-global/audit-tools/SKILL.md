---
name: audit-tools
description: "Audit tools and CLIs for usable interfaces, documentation, registration, meaningful tests, and error handling."
---

# Audit Tools

Discover the requested tools or the repository's tool inventory, then read [the check definitions](CHECKS.md). Map each public capability to its callable interface, typed inputs/outputs, docs, examples, tests, and console-script registration.
Run safe help/validation commands where appropriate; inspect commands before executing them if startup has side effects. Check real capability coverage rather than the mere existence of a test file. Validate manifests only where the repository uses them, and use the current CLI naming convention rather than a universal prefix.
Report PASS/WARN/FAIL per tool with concrete missing capabilities and remedies, then a concise aggregate. An audit is findings-only unless the request includes fixes. Interpret `--fix` as scoped repairs when supported, not as automatic permission to message others or open unrelated issues.
