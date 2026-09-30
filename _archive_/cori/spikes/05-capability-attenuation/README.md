# Spike 05: Capability attenuation as a property

Closes the tech-stack §1 property "no issued capability set is ever a
non-subset of its issuer's" and architecture §3.1 "capabilities are a subset
of the issuer's".

## Question

Can `issue(parent_caps, requested)` be a pure function that returns a subset
or refuses, such that no chain of issues through three children ever widens?

## Method

- `caps.py`: `Cap(name, effect_class, scope)` where scope is a path prefix
  (`""` is unrestricted, `repo/a` is that subtree). A parent cap covers a
  request when the name matches, the requested class is not higher, and the
  requested scope is inside the parent's. `issue()` returns the request
  unchanged if every requested cap is covered, else refuses (all or nothing).
  `attenuate()` is the alternative clip-to-parent policy, kept for comparison.
- `test_caps.py`: Hypothesis, 2,000 examples per property: `issue` never
  widens; `attenuate` never widens; a relay root -> c1 -> c2 -> c3 where each
  link issues from the previous holder (falling back to clipping on refusal)
  leaves every later holder within every earlier one; `issue` returns exactly
  the request or refuses, never a silently narrowed set. Plus hand examples
  including a grandchild trying to launder a wider scope back.

Run: `./run.sh`. No database, no API.

## Numbers

| Property | Examples | Result |
|---|---|---|
| issue never widens | 2,000 | pass |
| attenuate never widens | 2,000 | pass |
| relay through three children never widens (checked against every ancestor, not just the parent) | 2,000 | pass |
| issue is all-or-nothing | 2,000 | pass |

Runtime for the suite: 11 s.

## Surprises

1. Nothing in the property itself; a covers-relation that is reflexive and
   transitive makes the chain property follow from the single-step one. The
   value of the test is that it pins the three axes (name, class, scope) as
   the whole definition of "subset", so adding a fourth axis later (a space,
   a data class, a time bound) forces the covers-relation and the test to be
   extended together.
2. **Refuse versus clip is a real design choice.** `issue` refuses a request
   it cannot meet in full, so a Planner asking for class 2 on a class 1 node
   learns that, rather than getting class 1 and proceeding on a false belief.
   Clipping is safe by the same property but hides the disagreement.
3. A scope modelled as a path prefix maps directly onto the architecture's
   new "space" (a directory or repository). A space-scoped Brief is a cap
   whose scope is the space root, and the same covers-relation keeps
   descendants inside it.

## Recommendation

**Assumption holds.** Make the covers-relation the single definition of
"subset" in `schemas/`, put the space on the scope axis, and keep this test
as the kernel's property suite entry for capabilities. Prefer refuse over
clip at issue time so a Planner cannot mistake a narrowed grant for the one
it asked for.
