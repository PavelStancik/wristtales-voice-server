"""``GET /wristtales/capabilities`` (0.7.0): server, version, TTS models, Whisper.

No model is ever loaded; the Whisper check is a pure filesystem lookup in the
HuggingFace cache, which these tests point at a temp directory.
"""

import pytest
from fastapi.testclient import TestClient

import wristtales_voice_server as wvs

WHISPER_DIR = "models--mlx-community--whisper-large-v3-turbo-asr-fp16"


@pytest.fixture(autouse=True)
def clean_hf_env(tmp_path, monkeypatch):
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    # No 8bit convert unless a test makes one.
    monkeypatch.setattr(wvs, "MODEL_DIR", tmp_path / "models")


def make_snapshot(hub, *, weights=True, dangling=False, config=True):
    snap = hub / WHISPER_DIR / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    if config:
        (snap / "config.json").write_text("{}")
    if weights:
        blob = hub / WHISPER_DIR / "blobs" / "deadbeef"
        blob.parent.mkdir(parents=True)
        if not dangling:
            blob.write_bytes(b"x")
        (snap / "model.safetensors").symlink_to(blob)
    return snap


def get():
    return TestClient(wvs.app).get("/wristtales/capabilities")


def test_exact_shape_and_version(tmp_path):
    body = get().json()
    assert set(body) == {"server", "version", "tts", "transcription"}
    assert body["server"] == "wristtales-voice-server"
    assert body["version"] == (wvs.VERSION_FILE).read_text().strip()
    assert body["tts"] == {"models": ["higgs-v3-bf16"]}
    assert body["transcription"] == {
        "available": False,
        "model": "mlx-community/whisper-large-v3-turbo-asr-fp16",
    }


def test_whisper_present_in_hf_home(tmp_path):
    make_snapshot(tmp_path / "hf" / "hub")
    assert get().json()["transcription"]["available"] is True


def test_hf_hub_cache_wins_over_hf_home(tmp_path, monkeypatch):
    make_snapshot(tmp_path / "hf" / "hub")  # in HF_HOME, but...
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(other))  # ...this one is used
    assert get().json()["transcription"]["available"] is False
    make_snapshot(other)
    assert get().json()["transcription"]["available"] is True


def test_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    make_snapshot(tmp_path / "xdg" / "huggingface" / "hub")
    assert get().json()["transcription"]["available"] is True


def test_default_location_is_home_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setattr(wvs.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert wvs.hf_hub_cache_dir() == tmp_path / "home" / ".cache" / "huggingface" / "hub"


@pytest.mark.parametrize(
    "kwargs", [{"weights": False}, {"dangling": True}, {"config": False}]
)
def test_incomplete_snapshot_is_not_available(tmp_path, kwargs):
    make_snapshot(tmp_path / "hf" / "hub", **kwargs)
    assert get().json()["transcription"]["available"] is False


def test_empty_model_dir_without_snapshots(tmp_path):
    (tmp_path / "hf" / "hub" / WHISPER_DIR).mkdir(parents=True)
    assert get().json()["transcription"]["available"] is False


def test_8bit_listed_only_when_convert_exists(tmp_path):
    convert = tmp_path / "models" / "higgs-v3-8bit"
    convert.mkdir(parents=True)
    assert get().json()["tts"]["models"] == ["higgs-v3-bf16"]
    (convert / "model.safetensors").write_bytes(b"x")
    assert get().json()["tts"]["models"] == ["higgs-v3-bf16", "higgs-v3-8bit"]


def test_does_not_load_any_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("capabilities must not load a model")

    monkeypatch.setattr(wvs.model_provider, "load_model", boom)
    assert get().status_code == 200
