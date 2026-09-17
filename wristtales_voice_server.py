"""WristTales voice server: mlx_audio's server plus one batch synthesis route.

Binder 1.0 is live in the Mac App Store and talks to ``POST
/v1/audio/speech``. That endpoint's behaviour must not change, so this
module never reimplements it — it imports mlx_audio's own ``app`` (and the
inference machinery behind it) untouched and registers exactly one
additional route on top: ``POST /v1/audio/speech/batch``. Same process, same
model load, same FastAPI instance.

Run it the same way ``voice-server.sh`` ran ``mlx_audio.server`` before:

    python -m wristtales_voice_server --host 127.0.0.1 --port 8000

Every flag ``mlx_audio.server`` understands still works, because argument
parsing is delegated straight to it (see ``main()`` below).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel

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


class BatchSpeechRequest(BaseModel):
    """Wire contract is fixed — the Swift client is built against this."""

    model: str
    inputs: list[str]
    ref_audio: str
    ref_text: str
    temperature: float = 0.9
    top_k: int = 50
    max_new_tokens: int = 2048


@dataclass
class BatchSpeechTaskPayload:
    request: BatchSpeechRequest


@dataclass
class _ItemResult:
    index: int
    format: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        if self.error is not None:
            return {"index": self.index, "error": self.error}
        return {
            "index": self.index,
            "format": self.format,
            "audio_base64": self.audio_base64,
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
                ref_audio_codes = model.encode_reference_audio(req.ref_audio)
                # Higgs v3's batch_generate raises on voices/instructs/speeds/
                # pitches/gender — narrator identity comes only from
                # ref_audio_codes, so none of those are passed here.
                for item in model.batch_generate(
                    texts=valid_texts,
                    ref_audio_codes=ref_audio_codes,
                    ref_text=req.ref_text,
                    temperature=req.temperature,
                    top_k=req.top_k,
                    max_new_tokens=req.max_new_tokens,
                ):
                    # batch_generate yields out of order — sequence_idx is
                    # the position WITHIN valid_texts, not the original
                    # request index. Map it back explicitly.
                    original_index = valid_indices[item.sequence_idx]
                    results[original_index] = _ItemResult(
                        index=original_index,
                        format="mp3",
                        audio_base64=_encode_mp3_base64(item.audio, item.sample_rate),
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
                        ref_audio_codes = model.encode_reference_audio(req.ref_audio)
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
# Stabilní jména modelů
#
# Binder nabízí tři volby: bf16, 8bit a Vlastní… Jen ta třetí je textové
# pole; první dvě musí být pevné, jinak si uživatel nastavením rozbije to,
# co mu nainstaloval installer. Aby mohly být pevné, nesmí to být CESTY —
# absolutní cesta platí jen na stroji, kde vznikla, a Binder o disku serveru
# nic neví (ani vědět nemá: je v sandboxu a mluví jen přes localhost).
#
# mlx_audio žádné aliasy nemá — ``load_model()`` bere buď HF identifikátor,
# nebo cestu. Tuhle jednu vrstvu tedy přidáváme my, a to obalením
# ``model_provider``, ne úpravou jejich kódu: všechny endpointy včetně
# ``/v1/audio/speech`` chodí přes ``model_provider.load_model`` (server.py
# ``_load_model_for_inference``), takže stačí obalit tu jedinou metodu.
#
# 8bit se nabízí, jen když konvert na disku SKUTEČNĚ je. Nabízet jméno,
# které při syntéze spadne, je horší než ho nenabízet vůbec — Binder na
# prázdnou odpověď umí zareagovat ("Server nenabízí 8bit konvert"), na
# selhání uprostřed dlouhé narace ne.

MODEL_DIR = Path(
    os.environ.get("WRISTTALES_MODEL_DIR", Path(__file__).resolve().parent / "models")
)

BF16_MODEL_ID = "bosonai/higgs-audio-v3-tts-4b"
EIGHT_BIT_ALIAS = "higgs-v3-8bit"
BF16_ALIAS = "higgs-v3-bf16"


def model_aliases() -> dict[str, str]:
    """Alias -> co se doopravdy načte. 8bit jen když existuje."""
    aliases = {BF16_ALIAS: BF16_MODEL_ID}
    convert = MODEL_DIR / EIGHT_BIT_ALIAS
    if (convert / "model.safetensors").is_file():
        aliases[EIGHT_BIT_ALIAS] = str(convert)
    return aliases


def resolve_model(name: str) -> str:
    return model_aliases().get(name, name)


_ALIASES_INSTALLED = False


def _install_model_aliases() -> None:
    """Obalí model_provider tak, aby aliasy platily pro všechny endpointy."""
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
        # Aliasy patří na začátek: Binder bere první shodu a chceme, aby
        # viděl stabilní jméno, ne cestu, pod kterou je model rezidentní.
        return list(model_aliases()) + [m for m in listed if m not in model_aliases()]

    model_provider.load_model = load_with_alias
    model_provider.get_available_models = available_with_aliases
    _ALIASES_INSTALLED = True


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
    if not os.path.exists(payload.ref_audio):
        raise HTTPException(
            status_code=400,
            detail=f"Reference audio file not found: {payload.ref_audio}",
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
    mlx_server.main()


if __name__ == "__main__":
    main()
