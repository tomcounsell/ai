---
name: ontologies
description: "Build or update a project's domain vocabulary in ONTOLOGIES.md. Triggered by 'ontologies', 'build vocabulary', 'name things', 'define terms', or 'what do we call X'."
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Skill: /ontologies

## Purpose
Maintain `ONTOLOGIES.md` at the repo root (next to CLAUDE.md): the canonical vocabulary of
domain concepts, which prevents naming confusion, documents bounded contexts, and exposes
a single name doing two jobs.

## When to Use
- The team uses different names for one concept, or one name for different concepts
- A review or plan doc shows terms used inconsistently or undefined
- Before naming a new module, class, or API field
- The user says "ontologies", "define this term", or "what do we call X"

## Steps
1. Read `ONTOLOGIES.md` (if present), `docs/adr/`, and grep the codebase for the term before asking anything.
2. If no term was given, ask which one. Interview the user one question at a time, grill-me style, until the definition is stable: what it means in the domain (not the code), a real example, what it is NOT, which related concept it should be contrasted with, and whether it means different things in different parts of the system.
3. If one term means two things in two modules, that is a bounded-context boundary: record both meanings explicitly and say they must not be merged.
4. Write or update the entry, grouped under a domain-area heading:
   ```markdown
   ### <Term>
   **Definition:** One precise sentence, plain language.
   **Usage example:** "When a user cancels an Order, the..."
   **Contrast with:** terms this is commonly confused with
   ```
   A new file starts with `# Ontologies — <Repo Name>` and a one-line note that it holds domain concepts, not code identifiers.
5. Write an ADR in `docs/adr/` when the naming decision changes a public API or message format, or renames a concept that appears in more than about 10 files; not for a new internal concept.
6. Commit, e.g. `docs(ontologies): add <term> to domain vocabulary`.

## Output
An updated `ONTOLOGIES.md`, plus an ADR when step 5 calls for one.

## Anti-Patterns
- Code identifiers or schema columns in ONTOLOGIES.md: it records domain meaning, not a data dictionary.
- An empty "Contrast with" on a commonly confused term: that field is the most valuable part.
