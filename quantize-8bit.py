#!/usr/bin/env python
"""Vyrobí 8bit konvert Higgse v3 z bf16 vah.

Proč vlastní skript a ne `mlx_audio convert`: ten ukládá špatné rozložení
klíčů (`weight.biases` místo `biases`) a výsledek se nenačte. Tenhle skript
zapisuje plochý layout, který `mlx_audio` skutečně čte.

Dvě věci, na kterých to jinak spadne:

1. CODEC TENZORY NEJSOU V MODULOVÉM STROMĚ. Zhruba 528 tenzorů pod prefixem
   `tied.embedding.modality_embeddings.0.model.` drží audio kodek. Neprojdou
   přes `tree_flatten`, takže je nutné je dobrat ze zdrojového checkpointu
   a uložit NEKVANTIZOVANÉ. Bez toho se model načte a spadne až při syntéze.

2. 4BIT NEPOUŽÍVAT. Ověřeno 2026-08-11: ztrácí EOS a generuje až do stropu
   tokenů (konstantních 47,8 s bez ohledu na vstup). 8bit je ověřený.

Kontrola na konci porovnává počty klíčů proti referenčnímu konvertu, takže
tichá regrese v mlx_audio se projeví jako chyba, ne jako vadný zvuk.
"""
from __future__ import annotations

import argparse, json, shutil, sys
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten

CODEC_PREFIX = "tied.embedding.modality_embeddings"
GROUP_SIZE, BITS = 64, 8

# `multimodal_embedding` je SVÁZANÝ s
# `tied.embedding.modality_embeddings.0.embedding.weight` — jsou to dvě jména
# téhož tenzoru. Kvantizovat jedno a druhé nechat v bf16 je rozejde, proto
# zůstává v plné přesnosti první a druhé se do výstupu vůbec nezapisuje.
# Naivní kvantizace tenhle vztah nevidí; ověřený konvert z 2026-08-11 ho
# respektuje a tohle je jediné místo, kde se od něj dá odchýlit potichu.
TIED_EMBEDDING = "multimodal_embedding"
TIED_DUPLICATE = f"{CODEC_PREFIX}.0.embedding.weight"
# Očekávané tvary, naměřené na funkčním konvertu z 2026-08-11.
EXPECT = {"total": 1433, "quantized": 253, "codec": 528}


def build(source: str, dest: Path, strict: bool = True) -> None:
    from mlx_audio.tts.utils import load_model

    print(f"  načítám bf16 z {source} …", flush=True)
    model = load_model(source)

    def predicate(path: str, module) -> bool:
        if not hasattr(module, "to_quantized"):
            return False
        if hasattr(module, "weight") and module.weight.shape[-1] % GROUP_SIZE != 0:
            return False
        # Kodek zůstává v plné přesnosti — kvantizovaný rozbíjí syntézu.
        if path.startswith(CODEC_PREFIX):
            return False
        return path != TIED_EMBEDDING

    print(f"  kvantizuji na {BITS} bit, group_size {GROUP_SIZE} …", flush=True)
    nn.quantize(model, group_size=GROUP_SIZE, bits=BITS, class_predicate=predicate)

    weights = dict(tree_flatten(model.parameters()))

    # Codec tenzory doplnit ze zdroje — v modulovém stromě nejsou.
    src_dir = Path(source).expanduser()
    if not src_dir.is_dir():
        from huggingface_hub import snapshot_download
        src_dir = Path(snapshot_download(source))
    added = 0
    for shard in sorted(src_dir.glob("*.safetensors")):
        for key, value in mx.load(str(shard)).items():
            if key == TIED_DUPLICATE:
                continue
            if key.startswith(CODEC_PREFIX) and key not in weights:
                weights[key] = value
                added += 1
    print(f"  doplněno {added} codec tenzorů (nekvantizovaných)", flush=True)

    quantized = sum(1 for k in weights if k.endswith(".scales"))
    codec = sum(1 for k in weights if k.startswith(CODEC_PREFIX))
    print(f"  klíčů {len(weights)}, kvantizovaných {quantized}, codec {codec}")
    got = {"total": len(weights), "quantized": quantized, "codec": codec}
    if got != EXPECT:
        msg = f"rozložení neodpovídá referenci: {got} != {EXPECT}"
        if strict:
            raise SystemExit(f"CHYBA: {msg}\n(přepiš --no-strict, víš-li proč)")
        print(f"  VAROVÁNÍ: {msg}", flush=True)

    dest.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(dest / "model.safetensors"), weights)

    for name in ("config.json", "tokenizer.json", "tokenizer_config.json",
                 "chat_template.jinja"):
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    config = json.loads((dest / "config.json").read_text())
    config["quantization"] = {"group_size": GROUP_SIZE, "bits": BITS}
    config["model_type"] = "higgs_audio_v3"  # viz README: upstream hlásí higgs_multimodal_qwen3
    (dest / "config.json").write_text(json.dumps(config, indent=2))
    print(f"  ✓ hotovo: {dest}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="bosonai/higgs-audio-v3-tts-4b")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--no-strict", action="store_true")
    a = ap.parse_args()
    build(a.source, a.dest.expanduser(), strict=not a.no_strict)
