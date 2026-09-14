---
name: ontologies
description: "Define or reconcile domain terms and bounded contexts in ONTOLOGIES.md when naming or meaning is ambiguous."
---

# Ontologies

Read the existing glossary, relevant ADRs, and actual uses of the terms in code and documentation. Distinguish domain concepts from variable names and schema columns.
Resolve meanings from evidence before interviewing. Where human intent is needed, ask one question about a concrete domain example or a commonly confused neighboring concept. Use $grill-me only if pressure-testing is useful, not as a mandatory detour.
Update ONTOLOGIES.md with a precise one-sentence definition, realistic usage example, and contrast with neighboring terms. Group by domain. If a term means different things in different modules, document those bounded contexts rather than merging the concepts.
Record an ADR when the naming choice changes a public contract or has substantial architectural reach. Keep small internal clarifications in the glossary. Verify references and report the naming decision and its implications.
