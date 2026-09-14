"""Tests for tools/improvement_eval/corpus.py and retrieval.py (#3216).

Covers the frozen-corpus foundation: the canonical digest that names the
corpus identity, the ORM-native export/restore round trip, the baseline
parity gate, and the two restore-fidelity tests that pin the gate against
the symmetric mutations arm-vs-arm agreement cannot see.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); every row is
written under a test-scoped ``project_key``.
"""

from __future__ import annotations

import hashlib
import json
import time
from unittest import mock

import pytest

from tools.improvement_eval.errors import InfraFailure

PK_CORPUS = "test3216corpus"
PK_FIDELITY = "test3216fidelity"
PK_CARRY = "test3216carry"


def _make_manifest(**overrides):
    manifest = {
        "popoto_export": 1,
        "model": "Memory",
        "exported_at": "2026-09-11T00:00:00+00:00",
        "matched_count": 2,
        "filter_kwargs": {"project_key": PK_CORPUS},
    }
    manifest.update(overrides)
    return manifest


def _make_jsonl(manifest, records):
    lines = [json.dumps(manifest, sort_keys=True)]
    for record in records:
        lines.append(json.dumps(record, sort_keys=True))
    return "\n".join(lines) + "\n"


def _record(key, **values):
    body = {"key": key, "values": {"content": key, "importance": 5.0}, "state": {}}
    body["values"].update(values)
    return body


class TestCanonicalCorpusDigest:
    def test_is_stable_across_exported_at_changes(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest

        records = [_record("Memory:b"), _record("Memory:a")]
        first = _make_jsonl(_make_manifest(), records)
        second = _make_jsonl(_make_manifest(exported_at="2026-09-12T00:00:00+00:00"), records)
        assert (
            hashlib.sha256(first.encode()).hexdigest()
            != hashlib.sha256(second.encode()).hexdigest()
        )
        assert canonical_corpus_digest(first) == canonical_corpus_digest(second)

    def test_is_stable_across_record_order(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest

        records = [_record("Memory:b"), _record("Memory:a")]
        first = _make_jsonl(_make_manifest(), records)
        second = _make_jsonl(_make_manifest(), list(reversed(records)))
        assert (
            hashlib.sha256(first.encode()).hexdigest()
            != hashlib.sha256(second.encode()).hexdigest()
        )
        assert canonical_corpus_digest(first) == canonical_corpus_digest(second)

    def test_is_stable_across_intra_record_field_order(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest

        manifest_line = json.dumps(_make_manifest(), sort_keys=True)
        body = {"key": "Memory:a", "values": {"b": 1, "a": 2}, "state": {}}
        first = manifest_line + "\n" + json.dumps(body, sort_keys=True) + "\n"
        shuffled = '{"state": {}, "key": "Memory:a", "values": {"b": 1, "a": 2}}\n'
        second = manifest_line + "\n" + shuffled
        assert canonical_corpus_digest(first) == canonical_corpus_digest(second)

    def test_empty_corpus_has_a_valid_deterministic_digest(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest

        text = _make_jsonl(_make_manifest(matched_count=0), [])
        digest = canonical_corpus_digest(text)
        assert len(digest) == 64
        assert digest == canonical_corpus_digest(text)

    def test_manifest_change_surfaces_in_the_digest(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest

        records = [_record("Memory:a")]
        first = _make_jsonl(_make_manifest(matched_count=1), records)
        second = _make_jsonl(_make_manifest(matched_count=2), records)
        assert canonical_corpus_digest(first) != canonical_corpus_digest(second)

    def test_canonical_manifest_is_byte_equal_for_equivalent_exports(self):
        from tools.improvement_eval.corpus import canonical_manifest

        records = [_record("Memory:a")]
        first = _make_jsonl(_make_manifest(), records)
        second = _make_jsonl(_make_manifest(exported_at="2030-01-01T00:00:00+00:00"), records)
        assert canonical_manifest(first) == canonical_manifest(second)


def _seed_memory(project_key, content, importance=5.0, source="agent"):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3216",
        project_key=project_key,
        content=content,
        importance=importance,
        source=source,
    )
    assert record.save() is not False
    return record


class TestExportRestore:
    def test_empty_export_proceeds_to_a_valid_digest(self):
        from tools.improvement_eval.corpus import canonical_corpus_digest, export_corpus

        export = export_corpus(PK_CORPUS + "empty")
        assert export.record_count == 0
        assert len(export.digest) == 64
        assert export.digest == canonical_corpus_digest(export.jsonl_text)

    def test_roundtrip_preserves_the_canonical_digest(self):
        from tools.improvement_eval.corpus import (
            canonical_corpus_digest,
            export_corpus,
            restore_corpus,
        )

        _seed_memory(PK_CORPUS, "lighthouse beacon at dusk")
        _seed_memory(PK_CORPUS, "grocery errands for tomorrow")
        export = export_corpus(PK_CORPUS)
        assert export.record_count == 2

        restore_corpus(export.jsonl_text)

        from models.memory import Memory

        reexport = Memory.export_records(project_key=PK_CORPUS)
        assert canonical_corpus_digest(reexport.data) == export.digest

    def test_provenance_names_count_time_and_sha(self):
        from models.verifying_artifact_store import verifying_artifact_store
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK_CORPUS, "provenance check content")
        export = export_corpus(PK_CORPUS)
        assert export.provenance["record_count"] == export.manifest["matched_count"] == 1
        assert export.provenance["exported_at"]
        assert export.provenance["git_sha"]
        assert export.provenance["corpus_digest"] == export.digest
        assert verifying_artifact_store.load(export.corpus_ref).decode("utf-8") == (
            export.jsonl_text
        )
        loaded_provenance = json.loads(verifying_artifact_store.load(export.provenance_ref))
        assert loaded_provenance["corpus_digest"] == export.digest


class TestBaselineParity:
    def _baseline(self):
        from tools.improvement_eval.retrieval import RankedBaseline

        return RankedBaseline(ids=["id-a", "id-b"], corpus_digest="digest-1")

    def test_matching_ids_and_digest_pass(self):
        from tools.improvement_eval.retrieval import baseline_parity

        assert baseline_parity(["id-a", "id-b"], self._baseline(), "digest-1") is None

    def test_reordered_ids_raise_infra_failure(self):
        from tools.improvement_eval.retrieval import baseline_parity

        with pytest.raises(InfraFailure):
            baseline_parity(["id-b", "id-a"], self._baseline(), "digest-1")

    def test_digest_mismatch_raises_even_with_matching_ids(self):
        from tools.improvement_eval.retrieval import baseline_parity

        with pytest.raises(InfraFailure):
            baseline_parity(["id-a", "id-b"], self._baseline(), "digest-2")


def _retrieve_ids(query_text, project_key, limit=10):
    from tools.improvement_eval.retrieval import retrieve_ranked_ids

    with mock.patch("agent.memory_retrieval._retrieve_memories_hybrid", return_value=[]):
        return retrieve_ranked_ids(query_text, project_key, limit=limit)


class TestRestoreFidelityWithoutSkipAutoNow:
    """A restore that re-stamps relevance must move the ranking the gate checks.

    Both arms would suffer the mutation symmetrically, so arm-vs-arm
    agreement cannot see it. The recorded baseline can: the fixture spaces
    two records 60 days apart in decay time, so re-stamping every relevance
    timestamp to import time erases the spacing the baseline ranking rests
    on. The test asserts that drift first, then asserts the gate fires; a
    gate that cannot see its mutant is decoration.
    """

    OLD_CONTENT = "fidelity old record lorem ipsum"
    NEW_CONTENT = "fidelity new record dolor sit"
    QUERY = "zxqvkw qvxj"

    def _seed_spaced_pair(self):
        old_ts = time.time() - 60 * 86400
        with mock.patch("time.time", return_value=old_ts):
            old = _seed_memory(PK_FIDELITY, self.OLD_CONTENT)
        new = _seed_memory(PK_FIDELITY, self.NEW_CONTENT)
        return old, new

    def test_restore_without_skip_auto_now_fails_baseline_parity(self):
        from tools.improvement_eval.corpus import (
            export_corpus,
            restore_corpus,
        )
        from tools.improvement_eval.retrieval import RankedBaseline, baseline_parity

        old, new = self._seed_spaced_pair()
        assert str(old.memory_id) != str(new.memory_id)
        export = export_corpus(PK_FIDELITY)
        baseline_ids = _retrieve_ids(self.QUERY, PK_FIDELITY)
        assert baseline_ids[0] == str(new.memory_id)
        baseline = RankedBaseline(ids=baseline_ids, corpus_digest=export.digest)
        assert baseline_parity(list(baseline_ids), baseline, export.digest) is None

        restore_corpus(export.jsonl_text)
        from models.memory import Memory

        by_id = {str(m.memory_id): m for m in Memory.query.filter(project_key=PK_FIDELITY)}
        # Save in baseline order so the baseline-oldest record ends up newest:
        # the mutant erases the 60-day relevance spacing (an import writes in
        # arbitrary set order), and this order is one faithful execution of it.
        for memory_id in baseline_ids:
            assert by_id[memory_id].save() is not False

        mutant_ids = _retrieve_ids(self.QUERY, PK_FIDELITY)
        assert mutant_ids != baseline_ids
        with pytest.raises(InfraFailure):
            baseline_parity(mutant_ids, baseline, export.digest)

        # The shipped restore carries the timestamps: it undoes the mutant
        # above and reproduces the baseline. A restore that re-stamped
        # relevance at import would leave the digest moved and this red.
        restore_corpus(export.jsonl_text)
        assert canonical_digest_unchanged(export)
        assert (
            baseline_parity(_retrieve_ids(self.QUERY, PK_FIDELITY), baseline, export.digest) is None
        )

    def test_proper_restore_keeps_parity(self):
        from tools.improvement_eval.corpus import export_corpus, restore_corpus
        from tools.improvement_eval.retrieval import RankedBaseline, baseline_parity

        self._seed_spaced_pair()
        export = export_corpus(PK_FIDELITY)
        baseline_ids = _retrieve_ids(self.QUERY, PK_FIDELITY)
        baseline = RankedBaseline(ids=baseline_ids, corpus_digest=export.digest)

        restore_corpus(export.jsonl_text)

        assert (
            baseline_parity(_retrieve_ids(self.QUERY, PK_FIDELITY), baseline, export.digest) is None
        )
        assert canonical_digest_unchanged(export)

    def test_empty_export_reaches_infra_failure_through_parity_not_a_crash(self):
        from tools.improvement_eval.corpus import export_corpus
        from tools.improvement_eval.retrieval import RankedBaseline, baseline_parity

        export = export_corpus(PK_FIDELITY + "empty")
        baseline = RankedBaseline(ids=["missing-id"], corpus_digest=export.digest)
        with pytest.raises(InfraFailure):
            baseline_parity(
                _retrieve_ids(self.QUERY, PK_FIDELITY + "empty"), baseline, export.digest
            )


def canonical_digest_unchanged(export):
    from models.memory import Memory
    from tools.improvement_eval.corpus import canonical_corpus_digest

    reexport = Memory.export_records(project_key=export.project_key)
    return canonical_corpus_digest(reexport.data) == export.digest


class FakeEmbeddingProvider:
    """Deterministic stand-in for an embedding provider, keyed by exact text."""

    model = "fake-test-provider"
    dimensions = 2

    def __init__(self, mapping, default=(0.0, 1.0), model="fake-test-provider"):
        self.mapping = mapping
        self.default = default
        self.model = model

    def embed(self, texts, input_type=None):
        return [list(self.mapping.get(text, self.default)) for text in texts]


class TestRestoreFidelityWithoutCarry:
    """A restore that re-embeds under a rotated provider must move the ranking.

    The consequence of dropping ``on_embedding_mismatch="carry"`` is that
    vectors are recomputed under whatever provider is current instead of
    carried. The fixture emulates exactly that: records carry vectors from
    provider v1, the mutant re-embeds every record under provider v2, and
    the cosine signal that decided the baseline ranking collapses. Drift is
    asserted before the gate assertion.
    """

    CONTENT_A = "carry fidelity lorem ipsum"
    CONTENT_B = "carry fidelity dolor sit"
    QUERY = "alpha query terms absent from every record"

    def _patch_provider(self, monkeypatch, mapping, default=(0.0, 1.0), model="fake-test-provider"):
        import popoto.fields.embedding_field as ef

        provider = FakeEmbeddingProvider(mapping, default=default, model=model)
        monkeypatch.setattr(ef, "_default_embedding_provider", provider)
        return provider

    def test_restore_without_carry_fails_baseline_parity(self, monkeypatch, tmp_path):
        from tools.improvement_eval.corpus import export_corpus, restore_corpus
        from tools.improvement_eval.retrieval import RankedBaseline, baseline_parity

        monkeypatch.setenv("POPOTO_CONTENT_PATH", str(tmp_path / "content"))
        v1 = {
            self.CONTENT_A: (1.0, 0.0),
            self.CONTENT_B: (0.0, 1.0),
            self.QUERY: (1.0, 0.0),
        }
        self._patch_provider(monkeypatch, v1)
        record_a = _seed_memory(PK_CARRY, self.CONTENT_A)
        record_b = _seed_memory(PK_CARRY, self.CONTENT_B)

        export = export_corpus(PK_CARRY)
        baseline_ids = _retrieve_ids(self.QUERY, PK_CARRY)
        assert baseline_ids[0] == str(record_a.memory_id)
        baseline = RankedBaseline(ids=baseline_ids, corpus_digest=export.digest)

        # The shipped restore runs under a rotated provider (a different
        # fingerprint, so popoto sees a provenance mismatch) and must carry
        # the exported v1 vectors rather than re-embed under v2. Dropping
        # ``carry`` either refuses the import or recomputes the vectors, and
        # either way the v1 query no longer reproduces the baseline.
        self._patch_provider(monkeypatch, {}, default=(0.0, 1.0), model="fake-test-provider-v2")
        restore_corpus(export.jsonl_text)
        self._patch_provider(monkeypatch, v1)
        assert canonical_digest_unchanged(export)
        assert baseline_parity(_retrieve_ids(self.QUERY, PK_CARRY), baseline, export.digest) is None

        self._patch_provider(monkeypatch, {}, default=(0.0, 1.0))
        from models.memory import Memory

        # Re-save oldest-first so recency still favors B: the only
        # perturbation the gate may see is the recomputed vectors.
        by_id = {str(m.memory_id): m for m in Memory.query.filter(project_key=PK_CARRY)}
        for memory_id in (str(record_a.memory_id), str(record_b.memory_id)):
            record = by_id[memory_id]
            record.content = record.content + " mutant"
            assert record.save() is not False

        self._patch_provider(monkeypatch, v1)
        mutant_ids = _retrieve_ids(self.QUERY, PK_CARRY)
        assert mutant_ids != baseline_ids
        with pytest.raises(InfraFailure):
            baseline_parity(mutant_ids, baseline, export.digest)
