# Principal Context (test fixture)

Stand-in for `config/PRINCIPAL.md`, which is machine-local and absent from the
repo. The byte-stability baseline pins `PRINCIPAL_PATH` here so the principal
layer is composed identically on every host instead of varying with whatever
each machine happens to have on disk.

Deliberately shaped to exercise `load_principal_context(condensed=True)`'s
extraction: it takes the `Mission`, `Goals*` and `Projects*` sections and drops
everything else, so the section below must survive and the trailing one must not.

## Mission

Keep the persona prompt byte-stable so Anthropic's prompt cache keeps its
prefix across sessions (#1227).

## Goals (fixture)

Compose the same prompt on every machine in the fleet.

## Projects (fixture)

The byte-stability guard itself.

## Not Extracted

This section exists so the condensed extraction has something to leave behind.
If it ever shows up in the baseline, the extraction stopped working.
