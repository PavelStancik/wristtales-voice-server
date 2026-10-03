"""Inline reference audio (``ref_audio_base64``, 0.6.0).

The sandboxed Binder cannot give this process a path it may open, so it sends
the WAV bytes inline. These tests never load a model: the batch route is
driven through the real FastAPI ``app`` with the inference broker faked, and
the single-route middleware is exercised both on a tiny stand-in app (to see
exactly what the downstream handler receives) and, for the error paths, on
the real ``app``.
"""

import base64
import os
import stat
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import wristtales_voice_server as wvs

WAV = b"RIFF$\x00\x00\x00WAVEfmt " + bytes(range(40))
WAV_B64 = base64.b64encode(WAV).decode("ascii")
MODEL = "bosonai/higgs-audio-v3-tts-4b"


@pytest.fixture(autouse=True)
def ref_dir(tmp_path, monkeypatch):
    """Point the temp-file directory at a per-test tmp dir."""
    directory = tmp_path / "ref"
    monkeypatch.setattr(wvs, "ref_audio_dir", lambda: directory)
    return directory


@pytest.fixture
def batch(monkeypatch):
    """Real app, fake broker. Returns (client, captured submit kwargs list)."""
    submitted = []

    class FakeBroker:
        def submit(self, **kwargs):
            submitted.append(kwargs)
            return object()

    async def fake_await(handle):
        return {"results": []}

    monkeypatch.setattr(wvs, "get_inference_broker", lambda: FakeBroker())
    monkeypatch.setattr(wvs, "_ensure_batch_adapter_registered", lambda: None)
    monkeypatch.setattr(wvs, "_install_model_aliases", lambda: None)
    monkeypatch.setattr(wvs, "_await_batch_result", fake_await)
    return TestClient(wvs.app), submitted


def _body(**extra):
    return {"model": MODEL, "inputs": ["Ahoj."], **extra}


# --- batch route -----------------------------------------------------------


def test_batch_base64_uses_decoded_temp_file_and_skips_exists_check(batch, ref_dir):
    client, submitted = batch
    resp = client.post(
        "/v1/audio/speech/batch",
        json=_body(ref_audio="/definitely/not/here.wav", ref_audio_base64=WAV_B64),
    )
    assert resp.status_code == 200
    req = submitted[0]["payload"].request
    assert os.path.dirname(req.ref_audio) == str(ref_dir)
    assert req.ref_audio.endswith(".wav")
    with open(req.ref_audio, "rb") as fh:
        assert fh.read() == WAV
    assert req.ref_audio_base64 is None  # the big string is not carried along


def test_batch_base64_alone_works(batch):
    client, submitted = batch
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=WAV_B64))
    assert resp.status_code == 200
    assert open(submitted[0]["payload"].request.ref_audio, "rb").read() == WAV


def test_batch_ref_audio_path_still_works_unchanged(batch, tmp_path):
    client, submitted = batch
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"x")
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio=str(clip)))
    assert resp.status_code == 200
    assert submitted[0]["payload"].request.ref_audio == str(clip)


def test_batch_missing_path_still_400s(batch):
    client, submitted = batch
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio="/nope.wav"))
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]
    assert submitted == []


def test_batch_without_any_reference_still_works(batch):
    client, submitted = batch
    assert client.post("/v1/audio/speech/batch", json=_body()).status_code == 200
    assert submitted[0]["payload"].request.ref_audio is None


@pytest.mark.parametrize("bad", ["!!!not base64!!!", "QUJD*", "abc"])
def test_batch_invalid_base64_is_400(batch, bad):
    client, submitted = batch
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=bad))
    assert resp.status_code == 400
    assert "base64" in resp.json()["detail"]
    assert submitted == []


@pytest.mark.parametrize("empty", ["", "   "])
def test_batch_empty_base64_is_400(batch, empty):
    client, submitted = batch
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=empty))
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]
    assert submitted == []


def test_batch_oversize_is_413(batch, monkeypatch):
    client, submitted = batch
    monkeypatch.setattr(wvs, "MAX_REF_AUDIO_BYTES", 16)
    monkeypatch.setattr(wvs, "_MAX_REF_AUDIO_B64_CHARS", 24)
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=WAV_B64))
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"]
    assert submitted == []


def test_batch_decoded_size_cap_is_enforced_exactly(batch, monkeypatch):
    client, _ = batch
    monkeypatch.setattr(wvs, "MAX_REF_AUDIO_BYTES", len(WAV) - 1)
    # leave the base64-length pre-check roomy so the decoded check is what fires
    resp = client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=WAV_B64))
    assert resp.status_code == 413


# --- temp files ------------------------------------------------------------


def test_identical_clip_reuses_the_same_file(ref_dir):
    first = wvs.materialize_reference_audio(WAV_B64)
    mtime = os.stat(first).st_mtime_ns
    second = wvs.materialize_reference_audio(WAV_B64)
    assert first == second
    assert [p.name for p in ref_dir.iterdir()] == [os.path.basename(first)]
    assert os.stat(first).st_mtime_ns >= mtime


def test_different_clips_get_different_files(ref_dir):
    other = base64.b64encode(WAV + b"!").decode()
    assert wvs.materialize_reference_audio(WAV_B64) != wvs.materialize_reference_audio(other)


def test_temp_file_is_private(ref_dir):
    path = wvs.materialize_reference_audio(WAV_B64)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(ref_dir).st_mode) == 0o700
    assert not [p for p in ref_dir.iterdir() if p.name.startswith(".tmp-")]


def test_truncated_file_is_rewritten(ref_dir):
    path = wvs.materialize_reference_audio(WAV_B64)
    open(path, "wb").write(b"half")
    wvs.materialize_reference_audio(WAV_B64)
    assert open(path, "rb").read() == WAV


def test_startup_cleanup_removes_only_stale_files(ref_dir):
    ref_dir.mkdir()
    old = ref_dir / "old.wav"
    fresh = ref_dir / "fresh.wav"
    old_tmp = ref_dir / ".tmp-leftover"
    subdir = ref_dir / "subdir"
    for p in (old, fresh, old_tmp):
        p.write_bytes(b"x")
    subdir.mkdir()
    long_ago = time.time() - 25 * 3600
    for p in (old, old_tmp, subdir):
        os.utime(p, (long_ago, long_ago))
    just_inside = time.time() - 23 * 3600
    os.utime(fresh, (just_inside, just_inside))

    assert wvs.cleanup_stale_ref_audio() == 2
    assert not old.exists() and not old_tmp.exists()
    assert fresh.exists()
    assert subdir.is_dir()


def test_cleanup_without_directory_is_a_noop(ref_dir):
    assert not ref_dir.exists()
    assert wvs.cleanup_stale_ref_audio() == 0


def test_payload_is_never_logged(batch, caplog):
    client, _ = batch
    with caplog.at_level("DEBUG"):
        client.post("/v1/audio/speech/batch", json=_body(ref_audio_base64=WAV_B64))
    assert WAV_B64 not in caplog.text


# --- single-route middleware ----------------------------------------------


def _stand_in_app():
    """Same middleware, handlers that just report what they received."""
    stand_in = FastAPI()
    stand_in.add_middleware(wvs.InlineReferenceAudioMiddleware)

    @stand_in.post("/v1/audio/speech")
    async def speech(request: Request):
        raw = await request.body()
        try:
            parsed = await request.json()
        except ValueError:
            return JSONResponse({"detail": "bad json"}, status_code=422)
        return {
            "json": parsed,
            "raw_len": len(raw),
            "content_length": request.headers.get("content-length"),
        }

    @stand_in.post("/v1/audio/speech/batch")
    async def other(request: Request):
        return {"json": await request.json()}

    return TestClient(stand_in)


def test_middleware_rewrites_body_to_a_path():
    resp = _stand_in_app().post(
        "/v1/audio/speech",
        json={"model": MODEL, "input": "Ahoj.", "ref_audio": "/old", "ref_audio_base64": WAV_B64},
    )
    assert resp.status_code == 200
    got = resp.json()
    assert "ref_audio_base64" not in got["json"]
    assert got["json"]["input"] == "Ahoj."
    assert got["json"]["ref_audio"] != "/old"
    assert open(got["json"]["ref_audio"], "rb").read() == WAV
    assert got["content_length"] == str(got["raw_len"])  # header matches new body


def test_middleware_leaves_requests_without_the_field_untouched():
    payload = {"model": MODEL, "input": "Ahoj.", "ref_audio": "/some/path.wav"}
    resp = _stand_in_app().post("/v1/audio/speech", json=payload)
    assert resp.json()["json"] == payload


def test_middleware_ignores_other_paths_and_methods():
    client = _stand_in_app()
    payload = {"model": MODEL, "inputs": ["x"], "ref_audio_base64": WAV_B64}
    resp = client.post("/v1/audio/speech/batch", json=payload)
    assert resp.json()["json"] == payload  # not rewritten here
    assert client.get("/v1/audio/speech").status_code == 405


def test_middleware_null_field_is_treated_as_absent():
    resp = _stand_in_app().post(
        "/v1/audio/speech",
        json={"model": MODEL, "input": "x", "ref_audio": "/p.wav", "ref_audio_base64": None},
    )
    got = resp.json()["json"]
    assert got["ref_audio"] == "/p.wav" and "ref_audio_base64" not in got


def test_middleware_passes_unparseable_json_through():
    resp = _stand_in_app().post(
        "/v1/audio/speech",
        content=b'{"ref_audio_base64": nope',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422  # the handler's own verdict, not ours


def test_middleware_invalid_base64_is_400_with_detail():
    resp = _stand_in_app().post(
        "/v1/audio/speech", json={"model": MODEL, "input": "x", "ref_audio_base64": "%%%"}
    )
    assert resp.status_code == 400
    assert "base64" in resp.json()["detail"]


def test_middleware_empty_payload_is_400():
    resp = _stand_in_app().post(
        "/v1/audio/speech", json={"model": MODEL, "input": "x", "ref_audio_base64": ""}
    )
    assert resp.status_code == 400


def test_middleware_oversize_is_413(monkeypatch):
    monkeypatch.setattr(wvs, "MAX_REF_AUDIO_BYTES", 16)
    monkeypatch.setattr(wvs, "_MAX_REF_AUDIO_B64_CHARS", 24)
    resp = _stand_in_app().post(
        "/v1/audio/speech", json={"model": MODEL, "input": "x", "ref_audio_base64": WAV_B64}
    )
    assert resp.status_code == 413


def test_middleware_oversize_body_is_413_without_parsing(monkeypatch):
    monkeypatch.setattr(wvs, "_MAX_SPEECH_BODY_BYTES", 64)
    resp = _stand_in_app().post("/v1/audio/speech", json={"input": "x" * 200})
    assert resp.status_code == 413


def test_middleware_identical_clip_reuses_file():
    client = _stand_in_app()
    body = {"model": MODEL, "input": "x", "ref_audio_base64": WAV_B64}
    a = client.post("/v1/audio/speech", json=body).json()["json"]["ref_audio"]
    b = client.post("/v1/audio/speech", json=body).json()["json"]["ref_audio"]
    assert a == b


def test_real_app_has_the_middleware_installed_once():
    installed = [m for m in wvs.app.user_middleware if m.cls is wvs.InlineReferenceAudioMiddleware]
    assert len(installed) == 1
    wvs._install_inline_ref_middleware()  # idempotent
    installed = [m for m in wvs.app.user_middleware if m.cls is wvs.InlineReferenceAudioMiddleware]
    assert len(installed) == 1


def test_real_app_single_route_rejects_bad_base64_before_any_model_work():
    resp = TestClient(wvs.app).post(
        "/v1/audio/speech", json={"model": MODEL, "input": "x", "ref_audio_base64": "%%%"}
    )
    assert resp.status_code == 400
    assert "base64" in resp.json()["detail"]
