#!/bin/zsh
# Installs (or updates) the local voice server for Binder.
#
#   ./install.sh            # install, or install whatever is missing
#   ./install.sh --update   # pull the new version from GitHub and reinstall
#   ./install.sh --check    # only verify the environment and models, change nothing
#
# Without cloning first, in one line (clones into ~/wristtales-voice-server
# and runs this script from there):
#
#   curl -fsSL https://raw.githubusercontent.com/PavelStancik/wristtales-voice-server/main/install.sh | zsh
#
# Idempotent — running it twice is safe. It never deletes a model
# from ~/.cache/huggingface; the 8.7 GB download is the costliest part of the whole procedure.
# Variables: SKIP_8BIT=1 skips the optional 8bit convert;
# WRISTTALES_DIR changes the clone destination; WRISTTALES_REPO changes the clone source.

set -eu

# --- bootstrap: run via `curl | zsh`, outside a clone --------------------------
# Then $0 is "zsh" and there is no repository next to it. We clone it and hand
# control to the script from there (the flags are passed on).
SELF_DIR=${0:A:h}
if [[ ! -f $SELF_DIR/requirements.txt || ! -f $SELF_DIR/wristtales_voice_server.py ]]; then
  DEST=${WRISTTALES_DIR:-$HOME/wristtales-voice-server}
  REPO=${WRISTTALES_REPO:-https://github.com/PavelStancik/wristtales-voice-server.git}
  command -v git >/dev/null 2>&1 \
    || { print -u2 -- "\n✗ Chybí git. Spusť  xcode-select --install  a zkus to znovu."; exit 1 }
  if [[ -d $DEST/.git ]]; then
    # We reuse an existing clone only if it really is this repository
    # (or the one from WRISTTALES_REPO) — otherwise we would run a foreign install.sh.
    norm() { local u=${1%/}; print -r -- "${u%.git}" }
    ORIGIN=$(git -C "$DEST" remote get-url origin 2>/dev/null || true)
    OFFICIAL=PavelStancik/wristtales-voice-server
    if [[ -n $ORIGIN && ( $(norm "$ORIGIN") == $(norm "$REPO") \
          || $(norm "$ORIGIN") == (https://github.com/|git@github.com:|ssh://git@github.com/)$OFFICIAL ) ]]; then
      print -- "Repozitář už je v $DEST — stahuji novinky a pokračuji z něj."
      git -C "$DEST" pull --ff-only --quiet \
        || print -u2 -- "  ! git pull neprošel (offline, nebo vlastní změny v $DEST) — pokračuji s tím, co tam je."
    else
      print -u2 -- "\n✗ $DEST je git repozitář, ale ne tenhle (origin: ${ORIGIN:-žádný})."
      print -u2 -- "   Čekal jsem $REPO"
      print -u2 -- "   Zvol jiné místo:  WRISTTALES_DIR=~/jina/cesta"
      exit 1
    fi
  elif [[ -e $DEST ]]; then
    print -u2 -- "\n✗ $DEST už existuje a není to klon tohoto repozitáře."
    print -u2 -- "   Smaž ho, nebo zvol jiné místo:  WRISTTALES_DIR=~/jina/cesta"
    exit 1
  else
    print -- "Klonuji $REPO do $DEST …"
    git clone --quiet "$REPO" "$DEST" || { print -u2 -- "\n✗ git clone selhal"; exit 1 }
  fi
  exec zsh "$DEST/install.sh" "$@"
fi

ROOT=$SELF_DIR
source "$ROOT/common.zsh"
VENV="$ROOT/venv"
PY="$VENV/bin/python"
MODEL="bosonai/higgs-audio-v3-tts-4b"

# The lowest Python on which mlx-audio 0.4.7 runs reasonably. Newer is better,
# but 3.12+ has no pkg_resources — setuptools<81 in requirements.txt handles that.
MIN_MINOR=11

bold() { print -- "\033[1m$*\033[0m" }
ok()   { print -- "  ✓ $*" }
warn() { print -u2 -- "  ! $*" }
die()  { print -u2 -- "\n✗ $*"; exit 1 }

MODE=install
for arg in "$@"; do
  case "$arg" in
    --update)  MODE=update ;;
    --check)   MODE=check ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *)         die "neznámý přepínač: $arg" ;;
  esac
done

# --- 1. environment --------------------------------------------------------

bold "1/7  Kontrola počítače"

[[ $(uname -s) == Darwin ]] || die "Tenhle server běží jen na macOS."

if [[ $(uname -m) != arm64 ]]; then
  die "Je potřeba Mac s čipem Apple (M1 a novější).
     Model počítá přes Metal a na Intelu nepoběží."
fi
ok "macOS na Apple Silicon"

# Free space. The real need of a full install is ~16 GB: Higgs 8.7 GB +
# Whisper 1.5 GB + libraries (venv) ~1.2 GB + optional 8bit convert 4.4 GB.
# The gate counts only what is still missing (a model already downloaded is
# not downloaded again), adds a 1.5 GB reserve, and SKIP_8BIT=1 lowers the
# need by the convert. Without a reserve the install gets cut off in the
# middle of a download. The numbers are in MB (df -m).
CONVERT="$ROOT/models/higgs-v3-8bit"
NEED_MB=1500
[[ -x $VENV/bin/python ]]                || (( NEED_MB += 1300 ))
hf_model_cached "$MODEL"                 || (( NEED_MB += 8900 ))
whisper_cached                           || (( NEED_MB += 1600 ))
if [[ ! -f $CONVERT/model.safetensors && ${SKIP_8BIT:-} != 1 ]]; then
  (( NEED_MB += 4500 ))
fi
NEED_GB=$(( (NEED_MB + 1023) / 1024 ))
FREE_GB=$(( $(df -m "$HOME" | awk 'NR==2 {print $4}') / 1024 ))
if (( FREE_GB < NEED_GB )); then
  die "Na disku je volných jen ${FREE_GB} GB, tahle instalace potřebuje asi ${NEED_GB} GB
     (z toho chybějící části: Higgs 8,7 + Whisper 1,5 + knihovny ~1,2 + 8bit 4,4 GB, plus rezerva).
     Celá instalace od nuly zabere ~16 GB; SKIP_8BIT=1 ušetří 4,4 GB."
fi
ok "volné místo: ${FREE_GB} GB (potřeba ${NEED_GB} GB)"

# Memory. The bf16 model takes around 10 GB while running.
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if (( RAM_GB < 16 )); then
  die "Mac má ${RAM_GB} GB paměti; model potřebuje aspoň 16 GB."
elif (( RAM_GB < 24 )); then
  warn "Mac má ${RAM_GB} GB paměti. Poběží to, ale při namlouvání raději
    zavři ostatní aplikace."
else
  ok "paměť: ${RAM_GB} GB"
fi

# --- 2. Python -------------------------------------------------------------

bold "2/7  Hledání Pythonu"

# The system python3 from the Xcode CLT is often old; the named versions take priority.
PYBIN=""
for cand in python3.14 python3.13 python3.12 python3.11 python3; do
  command -v "$cand" >/dev/null 2>&1 || continue
  minor=$("$cand" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
  major=$("$cand" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
  [[ $major == 3 ]] || continue
  (( minor >= MIN_MINOR )) || continue
  PYBIN=$(command -v "$cand")
  break
done

[[ -n $PYBIN ]] || die "Nenašel jsem Python 3.${MIN_MINOR} ani novější.
     Nainstaluj ho přes Homebrew:  brew install python@3.13
     (Homebrew: https://brew.sh)"
ok "$($PYBIN -V) — $PYBIN"

if [[ $MODE == check ]]; then
  bold "\nProstředí vyhovuje."
  [[ -x $PY ]] && ok "server je nainstalovaný v $VENV" \
               || warn "server ještě není nainstalovaný — spusť ./install.sh"
  hf_model_cached "$MODEL" && ok "Higgs: připraven (model je v cache)" \
                           || warn "Higgs: chybí — stáhne ho ./install.sh (8,7 GB)"
  whisper_cached && ok "Whisper: připraven" \
                 || warn "Whisper: chybí — $WHISPER_MISSING_NOTE Doinstaluje ho ./install.sh (1,5 GB)"
  [[ -f $ROOT/models/higgs-v3-8bit/model.safetensors ]] \
    && ok "8bit konvert: připraven" \
    || print -- "  · 8bit konvert: není (volitelný)"
  exit 0
fi

# --- 3. source update ------------------------------------------------------

if [[ $MODE == update ]]; then
  bold "3/7  Aktualizace z GitHubu"
  if [[ -d "$ROOT/.git" ]]; then
    git -C "$ROOT" pull --ff-only || die "git pull neprošel — máš v repu vlastní změny?"
    ok "zdrojáky aktuální"
  else
    warn "tohle není git repo, přeskakuji stažení novinek"
  fi
else
  bold "3/7  Zdrojáky"
  ok "používám, co leží v $ROOT"
fi

# --- 4. libraries ----------------------------------------------------------

bold "4/7  Instalace knihoven (několik minut, stáhne ~1 GB)"

if [[ ! -x $PY ]]; then
  "$PYBIN" -m venv "$VENV" || die "nepodařilo se vytvořit venv v $VENV"
  ok "vytvořeno virtuální prostředí"
else
  ok "virtuální prostředí už existuje"
fi

"$PY" -m pip install --quiet --upgrade pip || die "nepodařilo se aktualizovat pip"
"$PY" -m pip install --quiet -r "$ROOT/requirements.txt" \
  || die "instalace knihoven selhala — vypiš si podrobnosti bez --quiet"
ok "knihovny nainstalovány"

# Verify that it really imports. pip can exit with zero and the module still
# fail — typically exactly on a missing pkg_resources.
"$PY" -W ignore - <<'PYCHECK' || die "knihovny se nainstalovaly, ale nejdou naimportovat"
import importlib, sys
for module in ("mlx", "mlx_audio", "webrtcvad", "fastapi", "uvicorn"):
    importlib.import_module(module)
PYCHECK
ok "import prošel"

# --- 5. model --------------------------------------------------------------

bold "5/7  Hlasový model ($MODEL, 8,7 GB)"
print -- "     Stahuje se jen jednou. Podruhé se vezme z ~/.cache/huggingface."

# snapshot_download is idempotent: it only verifies a complete snapshot and
# completes a partial one (interrupted download, missing shard or tokenizer).
# That is why it always runs, not only when the cache looks empty.
if "$PY" - "$MODEL" <<'PYDL'
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1])
print(f"  ✓ model připraven: {path}")
PYDL
then :
elif hf_model_cached "$MODEL"; then
  warn "kontrola na HuggingFace selhala (offline?) — používám model z cache"
else
  die "stažení modelu selhalo"
fi
hf_model_cached "$MODEL" || die "model se stáhl neúplný — spusť ./install.sh znovu"

# --- 6. whisper ------------------------------------------------------------

# A second, smaller model: whisper, for checking the narrated text. Higgs
# sometimes stops speaking before it reaches the end of a block, and audio
# length cannot reveal it (audit of book ABCDE: 74 truncated blocks, the
# length check let all of them through). So Binder transcribes every block
# via /v1/audio/transcriptions and compares it with the text; the same
# endpoint transcribes the user's voice sample. mlx_audio needs whisper in
# the format with an HF tokenizer, hence -asr-fp16 and not the mlx_whisper
# package's model.
# A failure here does not stop the install: without this model Binder only
# checks audio length, as before. The server reports it on /wristtales/capabilities.
bold "\n6/7  Whisper ($WHISPER_MODEL, 1,5 GB)"
print -- "     Kontrola, že namluvený text sedí; přepis hlasového vzorku."
# Same as for Higgs: always an idempotent snapshot_download, which also
# completes a partial download. Offline with a complete cache it only warns.
if "$PY" - "$WHISPER_MODEL" <<'PYDL'
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1])
print(f"  ✓ whisper připraven: {path}")
PYDL
then :
elif whisper_cached; then
  warn "kontrola na HuggingFace selhala (offline?) — používám whisper z cache"
else
  warn "whisper se nestáhl"
fi

# --- 7. 8bit convert -------------------------------------------------------

# Why it is quantised locally rather than downloaded ready-made: the Higgs
# license is Research/Non-Commercial, so we do not redistribute derived weights.
# The convert is also cheap — a few minutes against an 8.7 GB download.
#
# It is OPTIONAL. If the step fails or you skip it, the server keeps running,
# it just does not offer "higgs-v3-8bit" in /v1/models, and Binder, on the
# Faster (8bit) option, honestly says that the server does not have it. We
# never offer a name that would fail during synthesis.

bold "\n7/7  8bit konvert (volitelný, ~4,4 GB)"
if [[ -f "$CONVERT/model.safetensors" ]]; then
  ok "konvert už existuje — přeskakuji"
elif [[ "${SKIP_8BIT:-}" == "1" ]]; then
  print -- "     SKIP_8BIT=1 — přeskakuji."
else
  print -- "     Poloviční velikost, ~1,5x rychlejší, kvalita neodlišitelná."
  print -- "     Trvá to pár minut. Přeskočit: SKIP_8BIT=1 $0"
  if "$PY" "$ROOT/quantize-8bit.py" --source "$MODEL" --dest "$CONVERT"; then
    ok "8bit konvert připraven"
  else
    print -- "     ⚠ konverze selhala — nevadí, server pojede na bf16."
  fi
fi

# --- done ------------------------------------------------------------------

bold "\nHotovo."
if whisper_cached; then
  ok "Whisper: ready (kontrola textu i přepis vzorku fungují)"
else
  warn "Whisper: missing — $WHISPER_MISSING_NOTE Zkus ./install.sh znovu."
fi
[[ -f $CONVERT/model.safetensors ]] && ok "8bit: ready" || print -- "  · 8bit: není (server pojede na bf16)"
print -- ""
print -- "Server spustíš takto:"
print -- ""
print -- "    $ROOT/voice-server.sh"
print -- ""
print -- "Pak v Binderu zvol Namluvit… — server si najde sám."
