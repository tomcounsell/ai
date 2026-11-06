---
tracking: none
slug: list-semicolons
type: plan
status: planned
critique_rounds: 0
review_rounds: 0
---

# Bullets without semicolons

Tom (2026-11-06): "why are there semicolons at the end of bullet points?"
Punctuation only; no wording changes and no check, gate, hook or guard.

## Change (one commit)

Every list below follows a colon lead-in and every item is a lowercase
fragment, so each item ends with no punctuation at all: the trailing `;` goes
and so does the final item's `.`.

- `README.md` lines 10-16, the "On the product and project side, he:" list
  (7 items; the last ends "send." today).
- `site/alignment.html` 122-125 (the kernel's rollout steps, 4 items).
- `site/chat.html` 63-65 (where the session hands work, 3 items).
- `site/durability.html` 82-86 (what a starting kernel settles, 5 items).
- `site/product.html` 72-76 (readiness checks, 5 items).

A sweep of every `<li>` in `site/` and every bullet in `README.md` found no
other item ending in `;` or `, and`. Mission, governance and hard-rules text
is untouched.

## Out of scope

Any wording change; other punctuation; the README's other sections.

## Publish

After the kernel merges to `main`: run `scripts/deploy-site.sh` from the
merged `main` (Tom approved the site going live; this is a punctuation fix to
it), then `curl` the four live pages on valorengels.com and confirm no `<li>`
text ends in `;` and the edited items read as on `main`.

## Tests

`tests/test_site.py` and the full suite stay green. The sweep above, rerun
on the commit (`<li>` text and README bullets ending `;`), returns nothing;
the same sweep against the live HTML returns nothing. No new test: this is a
copy edit, not behaviour.
