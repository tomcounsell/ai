---
name: do-investigation-issue
description: "Capture an unverified anomaly or integration gap as a GitHub investigation issue with evidence and next experiments."
---

# Do Investigation Issue

Resolve the repository and search for an existing investigation. Distinguish observed symptoms from suspected causes. Collect the component, raw evidence, affected behavior, potential impact, attempted remedies, and a concrete checklist of files or experiments for the next investigator.
Use a title such as `Reliability risk: <component> — <finding>`, `Integration failure: ...` for an observed outage, or `Gap: ...` for a missing capability. Apply `investigation` when it exists; do not assert a bug classification before confirming a defect.
Read [the issue template](TEMPLATE.md), fill its relevant sections, and use a unique temporary body file whose exact contents you verify. Publish with `--body-file` or a structured tool only when issue creation is authorized, then read back the result. Report the URL and the central uncertainty. Do not apply a speculative remediation as part of capturing the investigation.
