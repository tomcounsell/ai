---
name: ontologies
description: "Use when building or updating a project's domain vocabulary. Triggered by 'ontologies', 'build vocabulary', 'name things', 'define terms', 'ONTOLOGIES.md', or any request to establish precise domain language for a codebase."
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Skill: /ontologies

## Purpose
Build and maintain an `ONTOLOGIES.md` at the repo root — a canonical domain vocabulary that prevents naming confusion, documents bounded contexts, and surfaces when a single name is doing two jobs.

## When to Use
- A new domain concept has appeared and the team is using different names for it
- A code review surfaces confusion about what "X" means vs "Y"
- A plan doc uses terms that are undefined or inconsistently applied
- Before naming a new module, class, or API field — check existing terms first
- When the user says "ontologies", "define this term", "what do we call X", or "ONTOLOGIES.md"

## Steps

1. **Read existing artifacts first.** Before asking any questions:
   - Read `ONTOLOGIES.md` at the repo root (if it exists)
   - Read `docs/adr/` for architectural decisions that defined terms
   - Grep for the term being discussed: `grep -r "<term>" --include="*.py" --include="*.md" -l`

2. **Identify the term(s) to clarify.** If invoked with no argument, ask: "What term or concept should we add to the ontology?" Wait for the answer.

3. **Run the interview loop via /grill-me.** Tell the user: "Running /grill-me on this term to surface the definition precisely." Ask one question at a time:
   - "What does this concept represent in the domain — not in code?"
   - "What is an example of this in production?"
   - "What is it NOT — what would a confused person mistake it for?"
   - "Is there a related concept that should be contrasted?"
   - "Does this concept have different meanings in different parts of the system?"

   After each answer, update your working definition. Stop when the definition is stable.

4. **Check for bounded-context split signal.** If the same term means two different things in two different modules, that is a bounded-context boundary. Document it explicitly:
   ```
   Note: "Session" in bridge/ means a Telegram conversation thread.
         "Session" in agent/ means an AgentSession execution record.
         These are distinct concepts that should not be merged.
   ```

5. **Write or update ONTOLOGIES.md.** Use this format:

   ```markdown
   # Ontologies — <Repo Name>

   > Canonical domain vocabulary for this repo. Updated by /ontologies.
   > Place next to CLAUDE.md. Terms are domain concepts, not code identifiers.

   ## <Domain Area>

   ### <Term>
   **Definition:** One precise sentence.
   **Usage example:** "When a user cancels an Order, the…"
   **Contrast with:** (optional) other terms this is commonly confused with
   ```

   - Group terms under domain areas (e.g., `## Sessions`, `## Messages`, `## Reflections`)
   - Use plain language in the definition — no code identifiers
   - Add a contrast-with entry whenever two terms are commonly confused

6. **Evaluate ADR criteria.** After updating the glossary, check: does this naming decision require an Architecture Decision Record?
   - Does it change public API or message format? → Yes, write an ADR in `docs/adr/`
   - Does it rename an existing concept that appears in > 10 files? → Yes, write an ADR
   - Is it a new internal concept with no external surface? → No ADR needed

7. **Commit the ONTOLOGIES.md.** Stage and commit with a message like: `docs(ontologies): add <term> to domain vocabulary`.

## Output
An updated `ONTOLOGIES.md` with new or clarified term entries. Optionally, a new ADR in `docs/adr/` if the naming decision crosses architectural boundaries.

## Anti-Patterns
- Do not add code identifiers to ONTOLOGIES.md — it documents domain concepts, not variable names.
- Do not skip step 1 — always read the existing ontology before asking questions.
- Do not create ONTOLOGIES.md with a single term — if there's only one term, add it to the existing section rather than creating a new file from scratch.
- Do not conflate ontologies with a data dictionary — ONTOLOGIES.md captures domain meaning, not schema columns.
- Do not leave the "Contrast with" field empty for terms that are commonly confused — that field is the most valuable part.
