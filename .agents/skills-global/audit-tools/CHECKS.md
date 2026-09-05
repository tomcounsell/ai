# Tool audit checks

Apply these to the repository's actual structure and conventions. Report evidence and
consequence rather than enforcing filenames or counting matching words.

1. **manifest-exists**: If the repo uses manifests, validate identity, capabilities,
   and registration against implementation. A repo without manifests is not a failure.
2. **readme-exists**: Find usable documentation for purpose, installation, usage, and
   interface. A central tools reference can satisfy this; exact headings are immaterial.
3. **tests-exist**: Locate meaningful tests wherever the repo keeps them. An empty
   test directory is not coverage, and a central test suite is valid.
4. **inputs-documented**: Verify public parameters, types, constraints, and examples
   against actual signatures and CLI parsing.
5. **output-types**: Verify return/error shapes, including partial results, pagination,
   streaming, or queue-versus-delivery distinctions.
6. **examples**: Inspect realistic usage examples for valid commands, arguments, and
   expected results. Do not execute live mutations just to test an example.
7. **error-docs**: Check callers can distinguish absence, failure, retryable conditions,
   and success. Assess redaction and whether retries duplicate side effects.
8. **test-coverage**: Map each actual capability to observable assertions. Similar test
   names do not establish coverage; trace the exercised implementation and boundaries.
9. **tests-passing**: Run relevant checks through the repo's test runner. In Valor use
   `scripts/pytest-clean.sh`, never bare pytest. Report unavailable integrations as
   untested, not passing; preserve failure output and exit status.
10. **cli-quality**: Inspect declared console-script registration and real `--help`.
    Check discoverable arguments, defaults, examples, and error codes; no universal
    command prefix or minimum line count is required.

Use FAIL for broken contracts or required capabilities, WARN for material usability or
coverage gaps, and INFO for useful improvements. Distinguish a suspected defect from a
confirmed one. Include standalone modules when they expose a public tool; skip caches
and templates. Use [the Codex procedure](SKILL.md) for scope and reporting.
