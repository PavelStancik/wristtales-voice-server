# Shared helper functions for install.sh and voice-server.sh (sourced, not executed).
#
# They tell whether a model is in the HuggingFace cache — without Python, without the network and without
# loading the model, so they answer instantly even on a machine that has no venv yet.
# The cache is located the way huggingface_hub does it (and therefore mlx_audio):
# HF_HUB_CACHE, HUGGINGFACE_HUB_CACHE, HF_HOME/hub, XDG_CACHE_HOME/huggingface/hub,
# finally ~/.cache/huggingface/hub. hf_hub_cache_dir() in
# wristtales_voice_server.py has the same logic — keep them in sync.

WHISPER_MODEL="mlx-community/whisper-large-v3-turbo-asr-fp16"

hf_hub_cache() {
  if   [[ -n ${HF_HUB_CACHE:-} ]];            then print -r -- "${HF_HUB_CACHE/#\~/$HOME}"
  elif [[ -n ${HUGGINGFACE_HUB_CACHE:-} ]];   then print -r -- "${HUGGINGFACE_HUB_CACHE/#\~/$HOME}"
  elif [[ -n ${HF_HOME:-} ]];                 then print -r -- "${HF_HOME/#\~/$HOME}/hub"
  elif [[ -n ${XDG_CACHE_HOME:-} ]];          then print -r -- "${XDG_CACHE_HOME/#\~/$HOME}/huggingface/hub"
  else print -r -- "$HOME/.cache/huggingface/hub"
  fi
}

# hf_snapshot_complete <snapshot-dir> — returns 0 only when the snapshot is complete:
#   * config.json, tokenizer.json and tokenizer_config.json exist (and the links
#     really lead to a file — an interrupted download leaves a dangling link),
#   * weights: if model.safetensors.index.json exists, EVERY shard it names
#     must exist; otherwise at least one *.safetensors.
# A partial download therefore does not pass as "ready" and ./install.sh completes it.
# hf_snapshot_complete() in wristtales_voice_server.py has the same logic;
# tests/test_install_scripts.py fails if the two ever drift apart.
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

# hf_model_cached <org/name> — returns 0 when the cache has at least one complete snapshot.
hf_model_cached() {
  local snap
  for snap in "$(hf_hub_cache)/models--${1//\//--}"/snapshots/*(N/); do
    hf_snapshot_complete "$snap" && return 0
  done
  return 1
}

whisper_cached() { hf_model_cached "$WHISPER_MODEL" }

WHISPER_MISSING_NOTE="Binder bude kontrolovat jen délku zvuku, ne přepis."
