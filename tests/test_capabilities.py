"""``GET /wristtales/capabilities`` (0.7.0): server, version, TTS models, Whisper.

No model is ever loaded; the Whisper check is a pure filesystem lookup in the
HuggingFace cache, which these tests point at a temp directory.
"""

import json

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


def _link(hub, model_dir, snap, name, content=b"x", dangling=False):
    """Put ``name`` in the snapshot as a symlink into blobs/, like huggingface_hub."""
    blob = hub / model_dir / "blobs" / ("blob-" + name)
    blob.parent.mkdir(parents=True, exist_ok=True)
    if not dangling:
        blob.write_bytes(content)
    (snap / name).symlink_to(blob)


def make_snapshot(
    hub,
    *,
    weights=True,
    dangling=False,
    config=True,
    tokenizer=("tokenizer.json", "tokenizer_config.json"),
    shards=None,
    missing_shards=(),
    model_dir=WHISPER_DIR,
):
    """A cached snapshot. ``shards`` makes it sharded: the index names every
    shard, and those in ``missing_shards`` are left out."""
    snap = hub / model_dir / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    if config:
        (snap / "config.json").write_text("{}")
    for name in tokenizer:
        (snap / name).write_text("{}")
    if shards:
        weight_map = {f"layer{i}.w": shard for i, shard in enumerate(shards)}
        (snap / "model.safetensors.index.json").write_text(
            json.dumps({"metadata": {}, "weight_map": weight_map})
        )
        for shard in shards:
            if shard not in missing_shards:
                _link(hub, model_dir, snap, shard)
    elif weights:
        _link(hub, model_dir, snap, "model.safetensors", dangling=dangling)
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


PARTIAL_SNAPSHOTS = {
    "no weights": {"weights": False},
    "dangling weights": {"dangling": True},
    "no config": {"config": False},
    "missing tokenizer.json": {"tokenizer": ("tokenizer_config.json",)},
    "missing tokenizer_config.json": {"tokenizer": ("tokenizer.json",)},
    "no tokenizer at all": {"tokenizer": ()},
    "sharded, one shard missing": {
        "shards": ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"],
        "missing_shards": ["model-00002-of-00002.safetensors"],
    },
}


@pytest.mark.parametrize("kwargs", PARTIAL_SNAPSHOTS.values(), ids=PARTIAL_SNAPSHOTS.keys())
def test_partial_snapshot_is_not_available(tmp_path, kwargs):
    make_snapshot(tmp_path / "hf" / "hub", **kwargs)
    assert get().json()["transcription"]["available"] is False


def test_complete_sharded_snapshot_is_available(tmp_path):
    make_snapshot(
        tmp_path / "hf" / "hub",
        shards=["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"],
    )
    assert get().json()["transcription"]["available"] is True


def test_unreadable_shard_index_is_not_available(tmp_path):
    snap = make_snapshot(tmp_path / "hf" / "hub")
    (snap / "model.safetensors.index.json").write_text("{not json")
    assert get().json()["transcription"]["available"] is False


def test_a_second_complete_snapshot_is_enough(tmp_path):
    hub = tmp_path / "hf" / "hub"
    make_snapshot(hub, weights=False)  # abc123: partial
    good = hub / WHISPER_DIR / "snapshots" / "def456"
    good.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
        (good / name).write_text("{}")
    assert get().json()["transcription"]["available"] is True


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
