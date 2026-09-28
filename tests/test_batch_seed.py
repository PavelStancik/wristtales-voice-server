"""Unit tests for the optional ``seed`` field on the batch endpoint (#436).

These tests never touch a real model or GPU: ``BatchTTSExecutionAdapter``
resolves its model via ``model_provider.load_model(req.model)`` (there is no
model injected through the constructor), so tests patch
``wristtales_voice_server.model_provider.load_model`` to return a
``MagicMock`` instead, and patch ``_encode_mp3_base64`` so the real ffmpeg
encoding path (which expects genuine PCM data) is never exercised.
"""

import pytest
from pydantic import ValidationError

from wristtales_voice_server import (
    BatchSpeechRequest,
    BatchSpeechTaskPayload,
    BatchTTSExecutionAdapter,
)
from mlx_audio.server_inference import InferenceRequest


def _base_kwargs(**overrides):
    kwargs = dict(model="bosonai/higgs-audio-v3-tts-4b", inputs=["Ahoj."])
    kwargs.update(overrides)
    return kwargs


def test_seed_defaults_to_none():
    req = BatchSpeechRequest(**_base_kwargs())
    assert req.seed is None


def test_seed_accepts_value_in_range():
    req = BatchSpeechRequest(**_base_kwargs(seed=42))
    assert req.seed == 42


def test_seed_rejects_negative():
    with pytest.raises(ValidationError):
        BatchSpeechRequest(**_base_kwargs(seed=-1))


def test_seed_rejects_above_31_bit_max():
    with pytest.raises(ValidationError):
        BatchSpeechRequest(**_base_kwargs(seed=2**31))


def test_seed_accepts_31_bit_max():
    req = BatchSpeechRequest(**_base_kwargs(seed=2**31 - 1))
    assert req.seed == 2**31 - 1


class _FakeBatchItem:
    def __init__(self, sequence_idx, audio_bytes=b"fake-audio", sample_rate=24000):
        self.sequence_idx = sequence_idx
        self.audio = audio_bytes
        self.sample_rate = sample_rate


def _run_adapter(monkeypatch, req, fake_model):
    """Run BatchTTSExecutionAdapter.run_serial against a real InferenceRequest,
    with the model resolution and mp3 encoding seams faked out, and return the
    ordered ``results`` list it emits via ``request.emit_data``.
    """
    import wristtales_voice_server as wvs

    monkeypatch.setattr(
        wvs.model_provider, "load_model", lambda name: fake_model
    )
    monkeypatch.setattr(
        wvs,
        "_encode_mp3_base64",
        lambda audio, sample_rate: "encoded",
    )

    adapter = BatchTTSExecutionAdapter()
    request = InferenceRequest(
        endpoint_kind="tts_batch",
        model_name=req.model,
        payload=BatchSpeechTaskPayload(request=req),
    )
    adapter.run_serial(request)

    chunks = []
    while not request.result_queue.empty():
        chunks.append(request.result_queue.get())
    data_chunks = [c for c in chunks if c.kind == "data"]
    assert data_chunks, "adapter never emitted a data chunk"
    return data_chunks[-1].payload["results"]


def test_seed_is_forwarded_to_batch_generate(monkeypatch):
    fake_model = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    fake_model.batch_generate.return_value = iter(
        [_FakeBatchItem(0), _FakeBatchItem(1)]
    )
    req = BatchSpeechRequest(**_base_kwargs(inputs=["Ahoj.", "Nazdar."], seed=42))

    _run_adapter(monkeypatch, req, fake_model)

    _, kwargs = fake_model.batch_generate.call_args
    assert kwargs.get("seed") == 42


def test_batch_items_echo_the_applied_seed(monkeypatch):
    fake_model = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    fake_model.batch_generate.return_value = iter(
        [_FakeBatchItem(0), _FakeBatchItem(1)]
    )
    req = BatchSpeechRequest(**_base_kwargs(inputs=["Ahoj.", "Nazdar."], seed=42))

    results = _run_adapter(monkeypatch, req, fake_model)

    assert all(r["seed"] == 42 for r in results)


def test_serial_fallback_items_echo_null_seed(monkeypatch):
    from unittest.mock import MagicMock

    fake_model = MagicMock()

    def _raising_batch_generate(**kwargs):
        yield _FakeBatchItem(0)  # one item lands before the batch raises
        raise RuntimeError("batch blew up")

    fake_model.batch_generate.side_effect = lambda **kw: _raising_batch_generate(**kw)

    fake_generate_output = MagicMock()
    fake_generate_output.audio = b"serial-audio"
    fake_generate_output.sample_rate = 24000
    fake_model.generate.return_value = iter([fake_generate_output])

    req = BatchSpeechRequest(**_base_kwargs(inputs=["Ahoj.", "Nazdar."], seed=42))
    results = _run_adapter(monkeypatch, req, fake_model)

    by_index = {r["index"]: r for r in results}
    assert by_index[0]["seed"] == 42  # produced by the seeded batch stream
    assert by_index[1]["seed"] is None  # produced by the unseeded serial fallback
