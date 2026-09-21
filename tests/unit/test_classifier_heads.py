"""Every committed head under ``agent/llm/backends/heads/`` matches its site (#3420).

A landed ``LOCAL_ENCODER`` site serves the head ``heads/<site>.json``; this
test is the static half of the output-type shape rule the leg enforces at
call time. Over every committed head: it loads through the leg's own
``load_head`` (shape, dimension, and the pinned embedding digest), its
``classes`` equal the closed set of the site's output type as
``tools/classification_eval/sites.py`` imports it (checked with the leg's
``_shape``, the same rule the serving path applies), and the site declares
``Backend.LOCAL_ENCODER``. Orphans are refused in both directions: a head
without a ``LOCAL_ENCODER`` declaration and a declaration without a head.

With no head committed yet the parametrized cases collect nothing, so the
``tmp_path`` cases keep the loader's accept/refuse behavior pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.llm import LLMCallError
from agent.llm.backends import local_encoder as leg
from agent.llm.tasks import Backend, declared_sites
from config.models import LOCAL_ENCODER_DIM, LOCAL_ENCODER_FILES

HEADS = sorted(leg.HEADS_DIR.glob("*.json"))
LANDED = {d.task.site for d in declared_sites() if d.task.backend is Backend.LOCAL_ENCODER}


def _head_dict(classes: list[str]) -> dict:
    k = len(classes)
    return leg.Head(
        site="test.site",
        classes=classes,
        W=[[0.0] * k for _ in range(LOCAL_ENCODER_DIM)],
        b=[0.0] * k,
        embedding_model="Xenova/bge-small-en-v1.5",
        embedding_revision="ea104dacec62c0de699686887e3f920caeb4f3e3",
        embedding_sha256=LOCAL_ENCODER_FILES["onnx/model_int8.onnx"],
        run_id="run-test",
        n_train=1,
        n_train_real=0,
        reference_model="claude-haiku-test",
        created_at="2026-09-21T00:00:00Z",
        fit_settings={},
    ).to_dict()


@pytest.mark.parametrize("path", HEADS, ids=[p.stem for p in HEADS])
class TestEveryCommittedHead:
    def test_loads_through_the_leg_with_at_least_two_classes(self, path: Path):
        head = leg.load_head(path)
        assert head.site == path.stem
        assert len(head.classes) >= 2
        assert head.embedding_sha256 == LOCAL_ENCODER_FILES["onnx/model_int8.onnx"]
        assert path.read_text() == json.dumps(head.to_dict(), indent=2, sort_keys=True)

    def test_site_declares_local_encoder(self, path: Path):
        assert path.stem in LANDED, f"{path.name} has no Backend.LOCAL_ENCODER declaration"

    def test_classes_equal_the_sites_closed_set(self, path: Path):
        from tools.classification_eval.sites import site_for  # noqa: PLC0415

        site = site_for(path.stem)
        output_type = site.candidate_output_type or site.output_type
        head = leg.load_head(path)
        shape = leg._shape(output_type, head)
        assert set(shape.values) == set(head.classes)


def test_no_orphan_heads_in_either_direction():
    """Head stems and ``LOCAL_ENCODER`` declarations are the same set."""
    assert {p.stem for p in HEADS} == LANDED


class TestLoaderOnSyntheticHeads:
    def test_accepts_a_valid_head(self, tmp_path):
        path = tmp_path / "ok.json"
        path.write_text(json.dumps(_head_dict(["True", "False"]), indent=2, sort_keys=True))
        head = leg.load_head(path)
        assert head.classes == ["True", "False"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"classes": ["only"], "W": [[0.0]] * LOCAL_ENCODER_DIM, "b": [0.0]},
            {"W": [[0.0, 0.0]] * (LOCAL_ENCODER_DIM - 1)},
            {"W": [[0.0]] * LOCAL_ENCODER_DIM},
            {"embedding_sha256": "f" * 64},
        ],
        ids=["one-class", "W-wrong-rows", "W-wrong-columns", "wrong-embedding"],
    )
    def test_refuses_an_invalid_head(self, tmp_path, overrides):
        path = tmp_path / "bad.json"
        d = _head_dict(["a", "b"])
        d.update(overrides)
        path.write_text(json.dumps(d))
        with pytest.raises(LLMCallError) as exc_info:
            leg.load_head(path)
        assert exc_info.value.reason == "validation"
