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

# hf_model_cached <org/name> — 0, když je v cache snapshot s config.json
# a aspoň jedním *.safetensors, na který odkaz opravdu vede (přerušené
# stahování nechává odkaz rozbitý a tím neprojde).
hf_model_cached() {
  local snap
  for snap in "$(hf_hub_cache)/models--${1//\//--}"/snapshots/*(N/); do
    [[ -e $snap/config.json ]] || continue
    [[ -n $(print -rl -- $snap/*.safetensors(N-.) 2>/dev/null) ]] && return 0
  done
  return 1
}

whisper_cached() { hf_model_cached "$WHISPER_MODEL" }

WHISPER_MISSING_NOTE="Binder bude kontrolovat jen délku zvuku, ne přepis."
