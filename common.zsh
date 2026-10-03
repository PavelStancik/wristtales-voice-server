# Společné pomocné funkce pro install.sh a voice-server.sh (zdrojuje se, nespouští).
#
# Zjišťují, jestli je model v cache HuggingFace — bez Pythonu, bez sítě a bez
# načítání modelu, takže odpoví okamžitě i na stroji, kde ještě není venv.
# Cache se hledá stejně jako to dělá huggingface_hub (a tedy mlx_audio):
# HF_HUB_CACHE, HUGGINGFACE_HUB_CACHE, HF_HOME/hub, XDG_CACHE_HOME/huggingface/hub,
# nakonec ~/.cache/huggingface/hub. Stejnou logiku má hf_hub_cache_dir()
# ve wristtales_voice_server.py — drž je v souladu.

WHISPER_MODEL="mlx-community/whisper-large-v3-turbo-asr-fp16"

hf_hub_cache() {
  if   [[ -n ${HF_HUB_CACHE:-} ]];            then print -r -- "${HF_HUB_CACHE/#\~/$HOME}"
  elif [[ -n ${HUGGINGFACE_HUB_CACHE:-} ]];   then print -r -- "${HUGGINGFACE_HUB_CACHE/#\~/$HOME}"
  elif [[ -n ${HF_HOME:-} ]];                 then print -r -- "${HF_HOME/#\~/$HOME}/hub"
  elif [[ -n ${XDG_CACHE_HOME:-} ]];          then print -r -- "${XDG_CACHE_HOME/#\~/$HOME}/huggingface/hub"
  else print -r -- "$HOME/.cache/huggingface/hub"
  fi
}

# hf_snapshot_complete <snapshot-dir> — 0, jen když je snapshot úplný:
#   * config.json, tokenizer.json a tokenizer_config.json existují (a odkazy
#     opravdu vedou na soubor — přerušené stahování nechává odkaz rozbitý),
#   * váhy: je-li model.safetensors.index.json, musí existovat KAŽDÝ shard,
#     který jmenuje; jinak aspoň jeden *.safetensors.
# Částečné stažení tím neprojde jako „ready“ a ./install.sh ho doplní.
# Stejnou logiku má hf_snapshot_complete() ve wristtales_voice_server.py;
# tests/test_capabilities.py hlídá, aby se nerozešly.
hf_snapshot_complete() {
  local snap=$1 f shard index="$1/model.safetensors.index.json"
  local -a shards
  for f in config.json tokenizer.json tokenizer_config.json; do
    [[ -f $snap/$f ]] || return 1
  done
  if [[ -f $index ]]; then
    shards=(${(f)"$(grep -o '"[^"]*\.safetensors"' "$index" 2>/dev/null | tr -d '"' | sort -u)"})
    (( $#shards )) || return 1
    for shard in $shards; do
      [[ -f $snap/$shard ]] || return 1
    done
  else
    [[ -n $(print -rl -- $snap/*.safetensors(N-.) 2>/dev/null) ]] || return 1
  fi
  return 0
}

# hf_model_cached <org/name> — 0, když je v cache aspoň jeden úplný snapshot.
hf_model_cached() {
  local snap
  for snap in "$(hf_hub_cache)/models--${1//\//--}"/snapshots/*(N/); do
    hf_snapshot_complete "$snap" && return 0
  done
  return 1
}

whisper_cached() { hf_model_cached "$WHISPER_MODEL" }

WHISPER_MISSING_NOTE="Binder bude kontrolovat jen délku zvuku, ne přepis."
