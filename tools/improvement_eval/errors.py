"""Harness-breakage exception for the frozen-input evaluation harness (#3216)."""

from __future__ import annotations


class InfraFailure(Exception):  # noqa: N818 -- plan-mandated name, mapped by the runner
    """The harness broke, so this run says nothing about the candidate.

    Raised for a contract-digest mismatch, an arm that would not spawn, an
    over-long arm socket path, unequal corpus digests, a baseline parity
    miss, a blocked corpus write, or any arm-worker transport failure. The
    runner maps this to ``verdict="infra_failure"``, which is disjoint from
    ``reject``: the measurement never ran, so there is no evidence against
    the candidate.
    """
