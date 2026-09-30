"""CorpusSizeBaseline Popoto model — durable-corpus-size ring for the
memory-quality-audit corpus-collapse detector (issue #2438).

Single-row model (fixed key ``"singleton"``) holding a bounded ring of recent
``[durable_corpus_size, recorded_at]`` samples, most-recent-last. Read/updated
once per `memory-quality-audit` reflection run
(`reflections/memory/memory_quality_audit.py`).

Why a ring and not a single scalar: a single last-seen field would let one
already-collapsed sample silently become the new "normal" baseline — the
specific failure mode described as Risk 2 in
`docs/plans/subconscious-memory-corpus-collapse-guardrails.md`. The ring lets
the detector compare each new observation against the recent *high-water
mark* (`max(size for size, _ in ring)`), which one collapsed sample cannot
suppress.

NOT the INCR-only gate counter (`models/memory_gate.py::_increment_gate_counter`)
— that structure exposes only atomic INCR/GET and cannot hold a value that
must shrink after legitimate pruning or be re-baselined. This model supports
arbitrary SET via normal Popoto `instance.save()` semantics.
"""

from __future__ import annotations

from popoto import KeyField, ListField, Model

# Fixed singleton key — one row tracks the whole corpus.
SINGLETON_KEY = "singleton"


class CorpusSizeBaseline(Model):
    """Single-row baseline ring of recent durable Memory corpus sizes.

    Fields:
        key: Fixed identifier ("singleton") for the single-instance pattern.
        ring: List of ``[durable_corpus_size: int, recorded_at: float]`` pairs,
            oldest-first. Callers (the audit reflection) are responsible for
            capping the length at ``CORPUS_BASELINE_RING_SIZE`` on append —
            this model does not enforce a cap itself so the cap constant can
            live alongside the rest of the audit's tunables.
    """

    key = KeyField(default=SINGLETON_KEY)
    ring = ListField(default=[])

    @classmethod
    def get_or_create(cls) -> CorpusSizeBaseline:
        """Get the singleton baseline record, creating an empty one if absent."""
        existing = cls.query.filter(key=SINGLETON_KEY)
        if existing:
            return existing[0]
        return cls.create(key=SINGLETON_KEY, ring=[])

    def max_size(self) -> int | None:
        """Return the high-water mark size in the ring, or None if the ring is empty."""
        if not self.ring:
            return None
        return max(int(size) for size, _recorded_at in self.ring)

    def append_sample(self, size: int, recorded_at: float, ring_size: int) -> None:
        """Append a (size, recorded_at) sample, dropping the oldest beyond ring_size.

        Read-modify-write on the single row — safe under the current
        single-worker-machine reflection scheduler (this reflection never
        runs concurrently with itself); see Risk 2 / the re-critique's
        concurrency concern in the plan doc for the explicit invariant.
        """
        current = [list(pair) for pair in (self.ring or [])]
        current.append([int(size), float(recorded_at)])
        if ring_size > 0 and len(current) > ring_size:
            current = current[-ring_size:]
        self.ring = current
        self.save()
