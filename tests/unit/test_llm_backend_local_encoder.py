"""The local encoder leg of ``run_typed`` (#3420, lane B).

``agent/llm/backends/local_encoder.py::call`` is the body ``run_typed`` runs
for a task the router resolves to ``Backend.LOCAL_ENCODER``. These tests
drive it directly and through ``run_typed`` with the ONNX runtime replaced
at the leg's one seam, ``_load_runtime`` (``tests.helpers.llm_fakes.
FakeEncoderRuntime``), and the served head redirected to ``tmp_path``.

What is pinned:

* The success path for a ``bool`` field and a ``Literal`` field, with
  ``confidence`` filled from the winning softmax score, and ``system``
  accepted and ignored.
* Every Failure Path row: the broad ``except Exception`` around the ONNX
  run re-raised as ``LLMCallError(reason="transport")`` with the cause
  chained and one ``logger.error``; a missing extra; a checksum mismatch on
  a temp models dir with one flipped byte; a missing head; each shape
  violation with zero ONNX runs; an empty prompt; a one-class head and a
  wrong-shape ``W`` refused at load; a 2,000-word input truncated to 512
  ids and finishing with a 384-d result.
* The deadline re-check runs before any load, and Race 1: twenty
  concurrent first calls load the runtime exactly once.
* Module scope is stdlib only and no ``asyncio.wait_for`` appears anywhere
  in the leg (hotfix #1055).
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from agent.llm import LLMCallError, LLMTask, run_typed
from agent.llm import wrapper as wrapper_mod
from agent.llm.backends import local_encoder as leg
from agent.llm.router import Route
from agent.llm.tasks import Backend, TaskKind
from config.models import LOCAL_ENCODER_DIM, LOCAL_ENCODER_FILES, local_encoder_models_dir
from tests.helpers.llm_fakes import FakeEncoderRuntime

SITE = "test.encoder"
LOCAL = LLMTask(site=SITE, kind=TaskKind.CLASSIFICATION, backend=Backend.LOCAL_ENCODER)
ROUTE = Route(Backend.LOCAL_ENCODER, SITE)


class BoolOut(BaseModel):
    needs_response: bool
    confidence: float = 0.0


class LiteralOut(BaseModel):
    verdict: Literal["bind", "skip", "escalate"]
    confidence: float
    note: str = ""


class NoConfidence(BaseModel):
    needs_response: bool


class TwoClosedSets(BaseModel):
    needs_response: bool
    verdict: Literal["True", "False"]


class RequiredExtra(BaseModel):
    needs_response: bool
    reason: str


class OtherClasses(BaseModel):
    verdict: Literal["yes", "no"]


def _head(classes: list[str], *, favour: int = 0, w_rows: int = LOCAL_ENCODER_DIM) -> leg.Head:
    """A head whose class ``favour`` wins on the fake's axis-0 unit vector."""
    k = len(classes)
    weights = [[0.0] * k for _ in range(w_rows)]
    if w_rows:
        weights[0] = [3.0 if j == favour else 0.0 for j in range(k)]
    return leg.Head(
        site=SITE,
        classes=classes,
        W=weights,
        b=[0.0] * k,
        embedding_model="Xenova/bge-small-en-v1.5",
        embedding_revision="ea104dacec62c0de699686887e3f920caeb4f3e3",
        embedding_sha256=leg.LOCAL_ENCODER_FILES["onnx/model_int8.onnx"],
        run_id="run-test",
        n_train=10,
        n_train_real=0,
        reference_model="claude-haiku-test",
        created_at="2026-09-21T00:00:00Z",
        fit_settings={"epochs": 300, "lr": 0.5, "l2": 1e-3},
    )


def _write_head(heads_dir: Path, head: leg.Head, site: str = SITE) -> Path:
    heads_dir.mkdir(parents=True, exist_ok=True)
    path = heads_dir / f"{site}.json"
    path.write_text(json.dumps(head.to_dict(), indent=2, sort_keys=True))
    return path


@pytest.fixture(autouse=True)
def _fresh_module(monkeypatch, tmp_path):
    """Every test starts with no memoized runtime or head and a temp heads dir."""
    leg._reset_for_tests()
    monkeypatch.setattr(leg, "HEADS_DIR", tmp_path / "heads")
    yield
    leg._reset_for_tests()


@pytest.fixture
def runtime(monkeypatch) -> FakeEncoderRuntime:
    fake = FakeEncoderRuntime()
    monkeypatch.setattr(leg, "_load_runtime", lambda: fake)
    return fake


@pytest.fixture
def verified_weights(tmp_path, monkeypatch) -> tuple[Path, dict[str, bytes]]:
    """Temp weights files whose digests are the (patched) pins; no extra needed to verify."""
    models = tmp_path / "models"
    contents = {name: name.encode() * 3 for name in LOCAL_ENCODER_FILES}
    for name, data in contents.items():
        (models / name).parent.mkdir(parents=True, exist_ok=True)
        (models / name).write_bytes(data)
    pins = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    monkeypatch.setattr(leg, "LOCAL_ENCODER_FILES", pins)
    monkeypatch.setenv("LOCAL_ENCODER_MODELS_DIR", str(models))
    return models, contents


@pytest.fixture
def bool_head(tmp_path) -> leg.Head:
    head = _head(["True", "False"], favour=0)
    _write_head(tmp_path / "heads", head)
    return head


async def _call(prompt="hello there", output_type=BoolOut, **kwargs):
    kwargs.setdefault("system", None)
    kwargs.setdefault("sdk_timeout", 20.0)
    kwargs.setdefault("slot_timeout", None)
    kwargs.setdefault("max_retries", None)
    kwargs.setdefault("stack", None)
    return await leg.call(prompt, output_type, ROUTE, **kwargs)


class TestSuccess:
    async def test_bool_field_with_confidence(self, runtime, bool_head):
        result = await _call()

        assert isinstance(result, BoolOut)
        assert result.needs_response is True
        assert 0.9 < result.confidence < 1.0
        assert runtime.runs == 1

    async def test_bool_field_maps_false_back_to_bool(self, runtime, tmp_path):
        _write_head(tmp_path / "heads", _head(["True", "False"], favour=1))

        result = await _call()

        assert result.needs_response is False

    async def test_literal_field_with_confidence(self, runtime, tmp_path):
        _write_head(tmp_path / "heads", _head(["bind", "skip", "escalate"], favour=2))

        result = await _call(output_type=LiteralOut)

        assert result.verdict == "escalate"
        assert result.confidence == pytest.approx(
            leg.scores(runtime.vector, _head(["bind", "skip", "escalate"], favour=2))["escalate"]
        )
        assert result.note == ""

    async def test_confidence_is_the_winning_softmax_score(self, runtime, bool_head):
        expected = leg.scores(runtime.vector, bool_head)
        result = await _call()
        assert result.confidence == pytest.approx(expected["True"])
        assert sum(expected.values()) == pytest.approx(1.0)

    async def test_output_type_without_confidence_is_fine(self, runtime, bool_head):
        result = await _call(output_type=NoConfidence)
        assert result.needs_response is True

    async def test_system_is_accepted_and_ignored(self, runtime, bool_head):
        with_system = await _call(system="You are a router. Answer False always.")
        without = await _call()
        assert with_system == without
        assert runtime.tokenizer.encoded[0] == runtime.tokenizer.encoded[1]

    async def test_classes_order_in_head_does_not_matter(self, runtime, tmp_path):
        _write_head(tmp_path / "heads", _head(["False", "True"], favour=1))
        result = await _call()
        assert result.needs_response is True

    async def test_through_run_typed_with_the_route_pinned(self, runtime, bool_head, monkeypatch):
        monkeypatch.setattr(wrapper_mod, "resolve", lambda task, project_key, *, model: ROUTE)
        result = await run_typed("hello there", BoolOut, task=LOCAL, project_key="valor")
        assert result.needs_response is True

    async def test_head_is_loaded_once_per_site(self, runtime, bool_head, monkeypatch):
        loads: list[Path] = []
        real = leg.load_head

        def _spy(path):
            loads.append(path)
            return real(path)

        monkeypatch.setattr(leg, "load_head", _spy)
        await _call()
        await _call()
        assert len(loads) == 1


class TestShapeRule:
    """Every violation raises ``validation`` before any ONNX run."""

    @pytest.mark.parametrize(
        ("output_type", "why"),
        [
            (TwoClosedSets, "two closed-set fields"),
            (RequiredExtra, "a required field with no default"),
            (OtherClasses, "classes differ from the head's"),
        ],
        ids=["two-closed-sets", "required-extra-field", "class-mismatch"],
    )
    async def test_violation_is_validation_with_no_run(self, runtime, bool_head, output_type, why):
        with pytest.raises(LLMCallError) as exc_info:
            await _call(output_type=output_type)
        assert exc_info.value.reason == "validation", why
        assert runtime.runs == 0, why

    async def test_no_closed_set_field_is_validation(self, runtime, bool_head):
        class Prose(BaseModel):
            answer: str = ""

        with pytest.raises(LLMCallError) as exc_info:
            await _call(output_type=Prose)
        assert exc_info.value.reason == "validation"
        assert runtime.runs == 0

    async def test_literal_classes_must_equal_the_head_set(self, runtime, tmp_path):
        _write_head(tmp_path / "heads", _head(["bind", "skip"]))
        with pytest.raises(LLMCallError) as exc_info:
            await _call(output_type=LiteralOut)
        assert exc_info.value.reason == "validation"
        assert runtime.runs == 0


class TestEmptyAndLongInput:
    @pytest.mark.parametrize("prompt", ["", "   ", "\n\t"])
    async def test_empty_prompt_is_validation(self, runtime, bool_head, prompt):
        with pytest.raises(LLMCallError) as exc_info:
            await _call(prompt=prompt)
        assert exc_info.value.reason == "validation"
        assert runtime.runs == 0

    async def test_two_thousand_words_are_truncated_and_finish(self, runtime, bool_head):
        prompt = " ".join(f"w{i}" for i in range(2000))
        result = await _call(prompt=prompt)
        assert isinstance(result, BoolOut)
        assert len(runtime.tokenizer.encoded[0]) <= 512
        assert runtime.feeds[0]["input_ids"].shape == (1, 512)
        vector = leg._embed(prompt)
        assert vector.shape == (LOCAL_ENCODER_DIM,)


class TestRuntimeFailures:
    async def test_onnx_exception_is_transport_with_cause_and_one_error_log(
        self, runtime, bool_head, caplog
    ):
        class BoomError(RuntimeError):
            pass

        runtime.error = BoomError("session fell over")
        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.local_encoder"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call()

        assert exc_info.value.reason == "transport"
        assert isinstance(exc_info.value.__cause__, BoomError)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "local_encoder leg transport" in errors[0].getMessage()

    async def test_missing_extra_is_transport_naming_the_extra(
        self, verified_weights, bool_head, monkeypatch
    ):
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        monkeypatch.setitem(sys.modules, "tokenizers", None)

        with pytest.raises(LLMCallError) as exc_info:
            await _call()

        assert exc_info.value.reason == "transport"
        assert "classification-local extra not installed" in str(exc_info.value)

    async def test_missing_extra_falls_back_to_anthropic_through_the_wrapper(
        self, verified_weights, bool_head, monkeypatch, caplog
    ):
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        monkeypatch.setitem(sys.modules, "tokenizers", None)
        anthropic_calls: list[dict] = []

        async def _anthropic(prompt, output_type, route, **kwargs):
            anthropic_calls.append({"route": route, **kwargs})
            return output_type(needs_response=False, confidence=0.5)

        monkeypatch.setitem(wrapper_mod._LEGS, Backend.ANTHROPIC, _anthropic)
        fallback = Route(Backend.ANTHROPIC, "claude-test")
        monkeypatch.setattr(
            wrapper_mod,
            "resolve",
            lambda task, project_key, *, model: Route(
                Backend.LOCAL_ENCODER, SITE, fallback=fallback
            ),
        )

        with caplog.at_level(logging.WARNING, logger="agent.llm.wrapper"):
            result = await run_typed("hello there", BoolOut, task=LOCAL, project_key="valor")

        assert result.needs_response is False
        assert len(anthropic_calls) == 1
        expected = (
            f"llm_fallback site={SITE} primary=local_encoder fallback=anthropic reason=transport"
        )
        assert any(r.getMessage().startswith(expected) for r in caplog.records)

    def test_checksum_mismatch_refuses_naming_the_file_and_the_script(self, verified_weights):
        """Mutation: one flipped byte in a weights file and the loader refuses."""
        models, contents = verified_weights
        flipped = bytearray(contents["onnx/model_int8.onnx"])
        flipped[0] ^= 0x01
        (models / "onnx/model_int8.onnx").write_bytes(bytes(flipped))

        with pytest.raises(LLMCallError) as exc_info:
            leg._load_runtime()

        assert exc_info.value.reason == "transport"
        assert "onnx/model_int8.onnx" in str(exc_info.value)
        assert "scripts/download_local_encoder_models.py" in str(exc_info.value)

    def test_missing_weights_file_refuses_naming_the_file_and_the_script(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_ENCODER_MODELS_DIR", str(tmp_path / "empty"))

        with pytest.raises(LLMCallError) as exc_info:
            leg._load_runtime()

        assert exc_info.value.reason == "transport"
        assert "onnx/model_int8.onnx" in str(exc_info.value)
        assert "scripts/download_local_encoder_models.py" in str(exc_info.value)

    def test_loader_configures_truncation_threads_and_cpu_provider(
        self, verified_weights, monkeypatch
    ):
        """With the modules faked, the real loader path verifies checksums first and then
        configures the session and tokenizer exactly as the spike did."""
        import os
        import types

        models, _ = verified_weights
        seen: dict = {}

        class _Options:
            intra_op_num_threads = None

        class _Session:
            def __init__(self, path, sess_options=None, providers=None):
                seen["path"] = path
                seen["threads"] = sess_options.intra_op_num_threads
                seen["providers"] = providers

        class _Tokenizer:
            @staticmethod
            def from_file(path):
                seen["tokenizer_path"] = path
                tok = _Tokenizer()
                return tok

            def enable_truncation(self, max_length, **kwargs):
                seen["truncation"] = max_length

        fake_ort = types.SimpleNamespace(SessionOptions=_Options, InferenceSession=_Session)
        fake_tok = types.SimpleNamespace(Tokenizer=_Tokenizer)
        monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
        monkeypatch.setitem(sys.modules, "tokenizers", fake_tok)

        runtime = leg._load_runtime()

        assert runtime.session is not None and runtime.tokenizer is not None
        assert seen["path"] == str(models / "onnx/model_int8.onnx")
        assert seen["tokenizer_path"] == str(models / "tokenizer.json")
        assert seen["threads"] == min(4, os.cpu_count() or 1)
        assert seen["providers"] == ["CPUExecutionProvider"]
        assert seen["truncation"] == 512


class TestHeads:
    async def test_missing_head_is_transport_naming_the_file(self, runtime):
        with pytest.raises(LLMCallError) as exc_info:
            await _call()
        assert exc_info.value.reason == "transport"
        assert f"agent/llm/backends/heads/{SITE}.json" in str(exc_info.value)
        assert runtime.runs == 0

    def test_round_trip_through_to_dict_and_from_dict(self):
        head = _head(["a", "b"])
        assert leg.Head.from_dict(head.to_dict()) == head

    def test_load_head_accepts_a_valid_file(self, tmp_path):
        path = _write_head(tmp_path / "heads", _head(["a", "b"]))
        assert leg.load_head(path) == _head(["a", "b"])

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda d: d.update(classes=["only"], W=[[0.0]] * LOCAL_ENCODER_DIM, b=[0.0]),
            lambda d: d.update(W=d["W"][:-1]),
            lambda d: d.update(W=[row[:-1] for row in d["W"]]),
            lambda d: d.update(b=d["b"] + [0.0]),
            lambda d: d.update(embedding_sha256="0" * 64),
            lambda d: d.pop("W"),
        ],
        ids=[
            "one-class",
            "W-too-few-rows",
            "W-too-few-columns",
            "b-wrong-length",
            "embedding-sha-mismatch",
            "missing-W",
        ],
    )
    def test_load_head_refuses_a_malformed_head(self, tmp_path, mutate):
        d = _head(["a", "b"]).to_dict()
        mutate(d)
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(d))

        with pytest.raises(LLMCallError) as exc_info:
            leg.load_head(path)
        assert exc_info.value.reason == "validation"

    def test_load_head_refuses_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(LLMCallError) as exc_info:
            leg.load_head(path)
        assert exc_info.value.reason == "validation"

    def test_served_head_path(self):
        assert leg.served_head_path("routing.needs_response").name == "routing.needs_response.json"
        assert leg.served_head_path("x").parent == leg.HEADS_DIR

    def test_scores_is_a_softmax_over_the_head(self):
        head = _head(["a", "b", "c"], favour=1)
        vector = [1.0] + [0.0] * (LOCAL_ENCODER_DIM - 1)
        out = leg.scores(vector, head)
        assert set(out) == {"a", "b", "c"}
        assert sum(out.values()) == pytest.approx(1.0)
        assert out["b"] > out["a"] == out["c"]


class TestDeadline:
    async def test_expired_deadline_raises_before_any_load(self, monkeypatch, bool_head):
        loads = []
        monkeypatch.setattr(leg, "_load_runtime", lambda: loads.append(1))
        monkeypatch.setattr(leg, "monotonic", lambda: 100.0)

        with pytest.raises(LLMCallError) as exc_info:
            await _call(deadline=100.4)

        assert exc_info.value.reason == "timeout"
        assert loads == []

    async def test_remaining_budget_lets_the_call_run(self, runtime, bool_head, monkeypatch):
        monkeypatch.setattr(leg, "monotonic", lambda: 100.0)
        result = await _call(deadline=105.0)
        assert result.needs_response is True


class TestRaceOneLoadUnderConcurrency:
    async def test_twenty_concurrent_first_calls_load_once(self, bool_head, monkeypatch):
        builds = 0
        lock = threading.Lock()
        fake = FakeEncoderRuntime()

        def _slow_build():
            nonlocal builds
            with lock:
                builds += 1
            time.sleep(0.05)
            return fake

        monkeypatch.setattr(leg, "_build_runtime", _slow_build)

        results = await asyncio.gather(*(_call() for _ in range(20)))

        assert builds == 1
        assert all(r.needs_response is True for r in results)
        assert fake.runs == 20


@pytest.mark.skipif(
    not all((local_encoder_models_dir() / f).is_file() for f in LOCAL_ENCODER_FILES),
    reason="pinned encoder weights not downloaded on this machine",
)
class TestRealWeights:
    """Real integration: the pinned int8 model through the real loader."""

    def test_embed_is_a_unit_vector_of_the_pinned_width(self):
        pytest.importorskip("onnxruntime")
        pytest.importorskip("tokenizers")
        vector = leg._embed("hello world")
        assert vector.shape == (LOCAL_ENCODER_DIM,)
        assert float((vector**2).sum()) == pytest.approx(1.0, abs=1e-4)

    def test_two_thousand_words_finish(self):
        pytest.importorskip("onnxruntime")
        pytest.importorskip("tokenizers")
        vector = leg._embed(" ".join(f"word{i}" for i in range(2000)))
        assert vector.shape == (LOCAL_ENCODER_DIM,)


def test_no_third_party_import_at_module_scope():
    """#3001 / #3525: nothing from onnxruntime, tokenizers, or numpy at module scope."""
    tree = ast.parse(Path(leg.__file__).read_text())
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"onnxruntime", "tokenizers", "numpy", "openai", "anthropic"}, imported


def test_no_wait_for_inside_the_leg():
    """Hotfix #1055: no coroutine-level timer anywhere in the leg."""
    tree = ast.parse(Path(leg.__file__).read_text())
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for"
    ]
    assert hits == []


def test_no_download_in_the_leg():
    source = Path(leg.__file__).read_text()
    assert not any(tok in source for tok in ("urlopen", "httpx", "requests.", "hf_hub_download"))
