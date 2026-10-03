"""WristTales voice server: mlx_audio's server plus one batch synthesis route.

Binder 1.0 is live in the Mac App Store and talks to ``POST
/v1/audio/speech``. That endpoint's behaviour must not change, so this
module never reimplements it — it imports mlx_audio's own ``app`` (and the
inference machinery behind it) untouched and registers exactly one
additional route on top: ``POST /v1/audio/speech/batch``. Same process, same
model load, same FastAPI instance. Both routes also accept the reference clip
inline as ``ref_audio_base64`` (see "Inline reference audio" below).

Run it the same way ``voice-server.sh`` ran ``mlx_audio.server`` before:

    python -m wristtales_voice_server --host 127.0.0.1 --port 8000

Every flag ``mlx_audio.server`` understands still works, because argument
parsing is delegated straight to it (see ``main()`` below).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from mlx_audio.audio_io import write as audio_write

# Reusing mlx_audio's own app object (not re-importing the module under a
# different name) is what guarantees /v1/audio/speech keeps being literally
# their code: we add a route to the same FastAPI instance they built.
from mlx_audio.server import (  # noqa: F401 (app re-exported for uvicorn's import string)
    app,
    get_inference_broker,
    model_provider,
)
from mlx_audio.server_inference import (
    BaseModelExecutionAdapter,
    InferenceHandle,
    InferenceRequest,
)

logger = logging.getLogger("wristtales.voice_server")

# ---------------------------------------------------------------------------
# Batch size ceiling.
#
# Measured on this machine (Mac mini M2 Pro, 32 GB, ~/higgs-tts/higgs-bf16-local,
# 12 Czech prose blocks, vypravec-2-expresivni.wav, temperature 0.9, warmed up):
#
#   serial generate            RTF 1.85   97.8s   1.00x
#   batch_generate, batch=4    RTF 1.63   84.8s   1.13x
#   batch_generate, batch=8    RTF 0.85   43.9s   2.17x
#
# The memory ceiling ABOVE batch=8 is unknown. `ru_maxrss` doesn't reflect
# MLX's unified-memory usage, and a larger model on this same Mac once fell
# into thrashing at a higher batch size — 44 GB of pageouts for 7.4s of
# audio, 1h44m wall. Do not raise this default without new measurements.
DEFAULT_MAX_BATCH = 8
MAX_BATCH = max(1, int(os.getenv("VOICE_SERVER_MAX_BATCH", str(DEFAULT_MAX_BATCH))))


# ---------------------------------------------------------------------------
# Inline reference audio (0.6.0)
#
# Binder is sandboxed and its voice-library clips live inside its container.
# This process can stat() them but not open() them (EPERM), so mlx_audio died
# with `miniaudio.DecodeError` AFTER it had already answered 200 on the
# streamed response, and the client only saw "cannot parse response". The
# client therefore sends the clip itself as `ref_audio_base64` (standard
# base64 of the complete WAV file). We decode it into a private temp file and
# hand the model a path it CAN open. `ref_audio` (a path) keeps working for
# older clients; when both are present, the inline bytes win.

# Decoded clips above this are refused (HTTP 413). A 30 s 24 kHz mono WAV is
# ~1.4 MB; 32 MB leaves a lot of room while bounding memory and disk.
MAX_REF_AUDIO_BYTES = 32 * 1024 * 1024
# Matching base64 length (4 chars per 3 bytes, plus padding).
_MAX_REF_AUDIO_B64_CHARS = (MAX_REF_AUDIO_BYTES + 2) // 3 * 4
# Largest request body the single-route middleware will buffer: the base64
# plus generous room for the rest of the (small) JSON.
_MAX_SPEECH_BODY_BYTES = _MAX_REF_AUDIO_B64_CHARS + 1024 * 1024
# Files untouched for longer than this are removed at startup.
REF_AUDIO_MAX_AGE_SECONDS = 24 * 60 * 60
REF_AUDIO_DIRNAME = "wristtales-voice-server-ref"
SINGLE_SPEECH_PATH = "/v1/audio/speech"


class ReferenceAudioError(Exception):
    """A client mistake in ``ref_audio_base64``; maps to an HTTP error."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def ref_audio_dir() -> Path:
    return Path(tempfile.gettempdir()) / REF_AUDIO_DIRNAME


def cleanup_stale_ref_audio(
    max_age_seconds: float = REF_AUDIO_MAX_AGE_SECONDS,
    directory: Optional[Path] = None,
) -> int:
    """Delete regular files older than ``max_age_seconds``; return the count.

    Called once at startup. Never called per request: a file may still be in
    use by a running synthesis, and identical clips are reused by hash.
    """
    directory = directory if directory is not None else ref_audio_dir()
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return 0
    cutoff = time.time() - max_age_seconds
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
                removed += 1
        except OSError as exc:  # a file vanishing under us is fine
            logger.warning("ref audio cleanup: could not remove %s (%s)", entry.path, exc)
    if removed:
        logger.info("ref audio cleanup: removed %d stale file(s)", removed)
    return removed


def materialize_reference_audio(b64: Any) -> str:
    """Decode ``ref_audio_base64`` into a temp WAV and return its path.

    The file is named by the sha256 of the bytes, so an identical clip sent
    again (every batch of a chapter carries the same narrator) is written
    once and reused. Raises ``ReferenceAudioError`` (400 / 413) on bad input.
    """
    if not isinstance(b64, str):
        raise ReferenceAudioError(400, "ref_audio_base64 must be a base64 string")
    b64 = b64.strip()
    if not b64:
        raise ReferenceAudioError(400, "ref_audio_base64 is empty")
    if len(b64) > _MAX_REF_AUDIO_B64_CHARS:
        raise ReferenceAudioError(
            413,
            f"ref_audio_base64 too large: maximum is {MAX_REF_AUDIO_BYTES} decoded bytes",
        )
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ReferenceAudioError(
            400, "ref_audio_base64 is not valid standard base64"
        ) from None
    if not data:
        raise ReferenceAudioError(400, "ref_audio_base64 decodes to zero bytes")
    if len(data) > MAX_REF_AUDIO_BYTES:
        raise ReferenceAudioError(
            413,
            f"ref_audio_base64 too large: {len(data)} bytes, maximum is "
            f"{MAX_REF_AUDIO_BYTES}",
        )

    directory = ref_audio_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / f"{hashlib.sha256(data).hexdigest()}.wav"
    try:
        if target.stat().st_size == len(data):
            os.utime(target)  # keep a reused clip from looking stale at restart
            return str(target)
    except FileNotFoundError:
        pass
    # Write beside the target and rename into place: two concurrent requests
    # with the same clip can never expose a half-written file.
    tmp = directory / f".tmp-{uuid.uuid4().hex}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    logger.info("inline reference audio: %d bytes -> %s", len(data), target.name)
    return str(target)


class InlineReferenceAudioMiddleware:
    """Pure ASGI middleware for mlx_audio's own ``POST /v1/audio/speech``.

    Their ``SpeechRequest`` has no ``ref_audio_base64`` field and drops
    unknown ones, so we rewrite the JSON body before they parse it:
    ``ref_audio_base64`` is decoded to a temp file and replaced by
    ``ref_audio`` pointing at it. Every other request passes through
    untouched (not even buffered).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != SINGLE_SPEECH_PATH
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > _MAX_SPEECH_BODY_BYTES:
                await JSONResponse(
                    {"detail": "request body too large"}, status_code=413
                )(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if b"ref_audio_base64" in body:
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None  # not ours to judge; mlx_audio answers 422
            if isinstance(parsed, dict) and "ref_audio_base64" in parsed:
                encoded = parsed.pop("ref_audio_base64")
                if encoded is not None:
                    try:
                        parsed["ref_audio"] = await asyncio.to_thread(
                            materialize_reference_audio, encoded
                        )
                    except ReferenceAudioError as exc:
                        await JSONResponse(
                            {"detail": exc.detail}, status_code=exc.status_code
                        )(scope, receive, send)
                        return
                body = json.dumps(parsed).encode("utf-8")
                scope = dict(scope)
                scope["headers"] = [
                    (k, v) for k, v in scope["headers"] if k.lower() != b"content-length"
                ] + [(b"content-length", str(len(body)).encode("ascii"))]

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


_INLINE_REF_MIDDLEWARE_INSTALLED = False


def _install_inline_ref_middleware() -> None:
    """Add the middleware once. Must run before the app serves its first request."""
    global _INLINE_REF_MIDDLEWARE_INSTALLED
    if _INLINE_REF_MIDDLEWARE_INSTALLED:
        return
    app.add_middleware(InlineReferenceAudioMiddleware)
    _INLINE_REF_MIDDLEWARE_INSTALLED = True


_install_inline_ref_middleware()


class BatchSpeechRequest(BaseModel):
    """Wire contract is fixed — the Swift client is built against this."""

    model: str
    inputs: list[str]
    # BOTH are optional because the client sends them conditionally:
    # OpenAICompatibleSynthesizer writes `ref_audio` only `if let
    # referenceAudioURL` and `ref_text` only `if let referenceText`. A user
    # who picked their own recording without a transcript therefore sends no
    # `ref_text` at all, and while it was required EVERY batch request from
    # them failed with 422 and narration never started. mlx_audio's
    # single-block /v1/audio/speech tolerates this, so it only showed up on
    # the batch route and looked like a model problem, not a schema one.
    ref_audio: Optional[str] = None
    # Inline alternative to `ref_audio` (0.6.0): standard base64 of the whole
    # WAV file. When present it wins and `ref_audio` is ignored — the
    # sandboxed Binder cannot hand this process a path it may open.
    ref_audio_base64: Optional[str] = None
    ref_text: Optional[str] = None
    temperature: float = 0.9
    top_k: int = 50
    max_new_tokens: int = 2048
    # Optional and additive (#436): when set, forwarded once to
    # batch_generate() so the whole batch's random stream is deterministic.
    # Serial-fallback items (see BatchTTSExecutionAdapter.run_serial) stay
    # unseeded on purpose and echo `null` for this field.
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


@dataclass
class BatchSpeechTaskPayload:
    request: BatchSpeechRequest


@dataclass
class _ItemResult:
    index: int
    format: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None
    seed: Optional[int] = None

    def to_json(self) -> dict[str, Any]:
        if self.error is not None:
            return {"index": self.index, "error": self.error}
        return {
            "index": self.index,
            "format": self.format,
            "audio_base64": self.audio_base64,
            "seed": self.seed,
        }


def _encode_mp3_base64(audio, sample_rate: int) -> str:
    buffer = io.BytesIO()
    audio_write(buffer, audio, sample_rate, format="mp3")
    # Base64 inflates the payload ~33% over raw mp3 bytes. Deliberate: it's
    # what keeps the Swift client trivial (one JSON decode, no multipart).
    # ~80 KB/block -> a batch of 8 is ~850 KB, fine over localhost.
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class BatchTTSExecutionAdapter(BaseModelExecutionAdapter):
    """Runs batch synthesis on the SAME broker worker thread as /v1/audio/speech.

    mlx_audio's InferenceBroker owns a single dedicated thread that holds
    the process's MLX default streams (they're thread-local — see
    InferenceBroker._run). Registering this adapter on that same broker,
    instead of spawning our own thread, is what keeps a batch job from ever
    touching the GPU concurrently with a plain /v1/audio/speech request:
    both endpoints now flow through the one worker loop, one at a time.
    """

    def run_serial(self, request: InferenceRequest) -> None:
        payload: BatchSpeechTaskPayload = request.payload
        req = payload.request

        # Reuses the same ModelProvider cache /v1/audio/speech uses — a
        # model already resident for that endpoint is NOT loaded twice here.
        model = model_provider.load_model(req.model)
        logger.info(
            "batch synth: resident model %r reused for %d item(s)",
            req.model,
            len(req.inputs),
        )

        results: dict[int, _ItemResult] = {}
        valid_indices: list[int] = []
        valid_texts: list[str] = []
        for index, text in enumerate(req.inputs):
            if not text or not text.strip():
                results[index] = _ItemResult(index=index, error="empty input text")
                continue
            valid_indices.append(index)
            valid_texts.append(text)

        if valid_texts:
            try:
                # Encode the reference once per request (~0.3s) and reuse it
                # for every item in the batch — do not re-encode per item.
                ref_audio_codes = (
                    model.encode_reference_audio(req.ref_audio)
                    if req.ref_audio is not None
                    else None
                )
                # Higgs v3's batch_generate raises on voices/instructs/speeds/
                # pitches/gender — narrator identity comes only from
                # ref_audio_codes, so none of those are passed here.
                for item in model.batch_generate(
                    texts=valid_texts,
                    ref_audio_codes=ref_audio_codes,
                    ref_text=req.ref_text,  # None passes through unchanged
                    temperature=req.temperature,
                    top_k=req.top_k,
                    max_new_tokens=req.max_new_tokens,
                    seed=req.seed,
                ):
                    # batch_generate yields out of order — sequence_idx is
                    # the position WITHIN valid_texts, not the original
                    # request index. Map it back explicitly.
                    original_index = valid_indices[item.sequence_idx]
                    results[original_index] = _ItemResult(
                        index=original_index,
                        format="mp3",
                        audio_base64=_encode_mp3_base64(item.audio, item.sample_rate),
                        seed=req.seed,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate to per-item fallback
                # We don't know which single item in the batched tensor op
                # caused this, so fall back to generating the still-missing
                # valid items one at a time. Slower, but it means one bad
                # block still can't take the rest of the batch down with it.
                logger.warning(
                    "batch_generate failed for the whole batch (%s); "
                    "falling back to serial generation for the remaining item(s)",
                    exc,
                )
                for original_index, text in zip(valid_indices, valid_texts):
                    if original_index in results:
                        continue
                    try:
                        ref_audio_codes = (
                            model.encode_reference_audio(req.ref_audio)
                            if req.ref_audio is not None
                            else None
                        )
                        last = None
                        for last in model.generate(
                            text=text,
                            ref_audio_codes=ref_audio_codes,
                            ref_text=req.ref_text,
                            temperature=req.temperature,
                            top_k=req.top_k,
                            max_new_tokens=req.max_new_tokens,
                        ):
                            pass
                        if last is None:
                            raise RuntimeError("generate() produced no output")
                        results[original_index] = _ItemResult(
                            index=original_index,
                            format="mp3",
                            audio_base64=_encode_mp3_base64(
                                last.audio, last.sample_rate
                            ),
                        )
                    except Exception as item_exc:  # noqa: BLE001
                        results[original_index] = _ItemResult(
                            index=original_index, error=str(item_exc)
                        )

        ordered = [results[i].to_json() for i in range(len(req.inputs))]
        request.emit_data({"results": ordered})
        request.emit_done()


# --------------------------------------------------------------------------
# Stable model names
#
# Binder offers three choices: bf16, 8bit and Custom... Only the third is a
# text field; the first two must be fixed, otherwise the user's settings can
# break what the installer set up. To be fixed they cannot be PATHS: an
# absolute path is only valid on the machine where it was made, and Binder
# knows nothing about the server's disk (and is not supposed to: it is
# sandboxed and only talks over localhost).
#
# mlx_audio has no aliases: ``load_model()`` takes either an HF identifier
# or a path. So we add this one layer ourselves, by wrapping
# ``model_provider`` rather than patching their code: every endpoint,
# ``/v1/audio/speech`` included, goes through ``model_provider.load_model``
# (server.py ``_load_model_for_inference``), so wrapping that single method
# is enough.
#
# 8bit is offered only when the convert REALLY exists on disk. Offering a
# name that then fails during synthesis is worse than not offering it at
# all: Binder can react to an empty answer ("the server doesn't offer the
# 8bit convert"), but not to a failure in the middle of a long narration.

MODEL_DIR = Path(
    os.environ.get("WRISTTALES_MODEL_DIR", Path(__file__).resolve().parent / "models")
)

BF16_MODEL_ID = "bosonai/higgs-audio-v3-tts-4b"
EIGHT_BIT_ALIAS = "higgs-v3-8bit"
BF16_ALIAS = "higgs-v3-bf16"


def model_aliases() -> dict[str, str]:
    """Alias -> what is really loaded. 8bit only when the convert exists."""
    aliases = {BF16_ALIAS: BF16_MODEL_ID}
    convert = MODEL_DIR / EIGHT_BIT_ALIAS
    if (convert / "model.safetensors").is_file():
        aliases[EIGHT_BIT_ALIAS] = str(convert)
    return aliases


def resolve_model(name: str) -> str:
    return model_aliases().get(name, name)


_ALIASES_INSTALLED = False


def _install_model_aliases() -> None:
    """Wrap model_provider so the aliases apply to every endpoint."""
    global _ALIASES_INSTALLED
    if _ALIASES_INSTALLED:
        return

    original_load = model_provider.load_model
    original_available = model_provider.get_available_models

    def load_with_alias(model_name: str):
        resolved = resolve_model(model_name)
        if resolved != model_name:
            logger.info("model alias %r -> %r", model_name, resolved)
        return original_load(resolved)

    async def available_with_aliases():
        listed = list(await original_available())
        # Aliases go first: Binder takes the first match and we want it to
        # see the stable name, not the path the model is resident under.
        return list(model_aliases()) + [m for m in listed if m not in model_aliases()]

    model_provider.load_model = load_with_alias
    model_provider.get_available_models = available_with_aliases
    _ALIASES_INSTALLED = True


# --------------------------------------------------------------------------
# Capabilities (0.7.0)
#
# Binder is sandboxed and cannot install anything, so it needs a cheap way to
# ask "is this server up, and does it have Whisper?" without loading a model.
# ``GET /wristtales/capabilities`` answers from the filesystem alone.
#
# /v1/models is deliberately NOT extended with the Whisper id: it is mlx_audio's
# list of *resident* models plus our TTS aliases, and clients iterate it to find
# Higgs variants. A transcription id in there would be picked up as a TTS
# choice, and it already appears on its own once Whisper has been loaded.

WHISPER_MODEL_ID = "mlx-community/whisper-large-v3-turbo-asr-fp16"
SERVER_NAME = "wristtales-voice-server"
VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def server_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def hf_hub_cache_dir() -> Path:
    """The HuggingFace hub cache, resolved like huggingface_hub does.

    Read from the environment on every call (huggingface_hub freezes it at
    import time), in its order of precedence: HF_HUB_CACHE,
    HUGGINGFACE_HUB_CACHE, HF_HOME/hub, XDG_CACHE_HOME/huggingface/hub,
    ~/.cache/huggingface/hub.
    """
    env = os.environ
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(key):
            return Path(env[key]).expanduser()
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]).expanduser() / "hub"
    xdg = env.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "huggingface" / "hub"


REQUIRED_SNAPSHOT_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json")


def hf_snapshot_complete(snap: Path) -> bool:
    """True when a cached snapshot directory holds a whole model.

    Needs config.json, tokenizer.json and tokenizer_config.json, and the
    weights: when ``model.safetensors.index.json`` exists every shard it names
    must be present, otherwise at least one ``*.safetensors``. ``Path.is_file``
    follows symlinks into blobs/, so a link left dangling by an interrupted
    download counts as missing. common.zsh has the same rule for the install
    scripts; tests/test_capabilities.py fails if the two ever disagree.
    """
    try:
        if not all((snap / name).is_file() for name in REQUIRED_SNAPSHOT_FILES):
            return False
        index = snap / "model.safetensors.index.json"
        if index.is_file():
            shards = set(json.loads(index.read_text(encoding="utf-8")).get("weight_map", {}).values())
            return bool(shards) and all((snap / shard).is_file() for shard in shards)
        return any(w.is_file() for w in snap.glob("*.safetensors"))
    except (OSError, ValueError, AttributeError):
        return False


def hf_model_cached(model_id: str) -> bool:
    """True when a complete snapshot of ``model_id`` is in the HF cache.

    Never loads anything; a partial download is not reported as available.
    """
    snapshots = hf_hub_cache_dir() / ("models--" + model_id.replace("/", "--")) / "snapshots"
    try:
        return any(hf_snapshot_complete(snap) for snap in snapshots.iterdir())
    except OSError:
        return False


def capabilities() -> dict[str, Any]:
    return {
        "server": SERVER_NAME,
        "version": server_version(),
        "tts": {"models": list(model_aliases())},
        "transcription": {
            "available": hf_model_cached(WHISPER_MODEL_ID),
            "model": WHISPER_MODEL_ID,
        },
    }


@app.get("/wristtales/capabilities")
def wristtales_capabilities() -> dict[str, Any]:
    # A plain ``def`` so FastAPI runs it in the threadpool instead of on the
    # event loop (it does a little file I/O). That is NOT a guarantee of a
    # prompt answer: while a long synthesis occupies the process the server
    # can be slow to accept the request at all (one block can take tens of
    # seconds), so clients should use a generous timeout and treat a timeout
    # as "busy", not as "capability missing".
    return capabilities()


_BATCH_ADAPTER_REGISTERED = False


def _ensure_batch_adapter_registered() -> None:
    global _BATCH_ADAPTER_REGISTERED
    if _BATCH_ADAPTER_REGISTERED:
        return
    get_inference_broker().register_adapter("tts_batch", BatchTTSExecutionAdapter())
    _BATCH_ADAPTER_REGISTERED = True


async def _await_batch_result(handle: InferenceHandle) -> dict[str, Any]:
    """Drain one InferenceHandle to completion, off the event loop thread."""
    data: Optional[dict[str, Any]] = None
    while True:
        chunk = await asyncio.to_thread(handle.result_queue.get)
        if chunk.kind == "error":
            raise chunk.error
        if chunk.kind == "data":
            data = chunk.payload
            continue
        if chunk.kind == "done":
            if data is None:
                raise RuntimeError("tts_batch worker finished without a result")
            return data


@app.post("/v1/audio/speech/batch")
async def tts_speech_batch(payload: BatchSpeechRequest) -> dict[str, Any]:
    """Batch synthesis: one narrator (ref_audio/ref_text), many blocks.

    Response is always 200; per-item failures are reported inside
    ``results`` rather than failing the whole request (see
    ``BatchTTSExecutionAdapter.run_serial``).
    """
    if not payload.inputs:
        raise HTTPException(
            status_code=400, detail="inputs must contain at least one item"
        )
    if len(payload.inputs) > MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=(
                f"batch too large: {len(payload.inputs)} items, maximum is "
                f"{MAX_BATCH} (set VOICE_SERVER_MAX_BATCH to raise it)"
            ),
        )
    if payload.ref_audio_base64 is not None:
        # Inline clip wins over any path; the path is not even looked at.
        try:
            ref_path = await asyncio.to_thread(
                materialize_reference_audio, payload.ref_audio_base64
            )
        except ReferenceAudioError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)
        # Drop the (multi-MB) base64 from the payload the worker carries.
        payload = payload.model_copy(
            update={"ref_audio": ref_path, "ref_audio_base64": None}
        )
    elif payload.ref_audio is not None and not os.path.exists(payload.ref_audio):
        raise HTTPException(
            status_code=400,
            detail=f"Reference audio file not found: {payload.ref_audio}",
        )
    if payload.ref_audio is None:
        # Higgs is a cloning model: WITHOUT a reference it samples a speaker
        # anew for every request, so in a batch each block may be read in a
        # different voice. We don't reject it (the single-block endpoint
        # allows it too, and Binder has its own confirmation step for it),
        # but it should be in the log for when someone wonders why a chapter
        # switches narrators.
        logger.warning(
            "batch synth without ref_audio: the speaker is sampled per "
            "request, so blocks in this batch may not share a voice"
        )

    _ensure_batch_adapter_registered()
    _install_model_aliases()
    handle = get_inference_broker().submit(
        endpoint_kind="tts_batch",
        model_name=payload.model,
        payload=BatchSpeechTaskPayload(request=payload),
    )
    return await _await_batch_result(handle)


def main() -> None:
    """Delegate argument parsing and startup straight to mlx_audio.server.

    ``voice-server.sh`` passes only ``--host``/``--port`` today, but every
    flag mlx_audio.server understands (``--allowed-origins``,
    ``--tts-max-batch-size``, etc.) keeps working because this calls their
    real ``main()`` unmodified.

    Their ``main()`` ends up calling ``uvicorn.run("mlx_audio.server:app",
    ...)``. That import string does NOT get us a fresh, route-less app:
    Python caches modules in ``sys.modules``, and our own top-level ``from
    mlx_audio.server import app`` above already imported and cached that
    module — so uvicorn's import just returns the very same ``app`` object
    we already attached ``/v1/audio/speech/batch`` to. One FastAPI instance,
    mutated in place, not two.
    """
    import mlx_audio.server as mlx_server

    _ensure_batch_adapter_registered()
    _install_model_aliases()
    cleanup_stale_ref_audio()
    mlx_server.main()


if __name__ == "__main__":
    main()
