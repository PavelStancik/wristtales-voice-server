#!/bin/zsh
# Nainstaluje (nebo zaktualizuje) lokální hlasový server pro Binder.
#
#   ./install.sh            # nainstaluje, nebo doinstaluje co chybí
#   ./install.sh --update   # stáhne novou verzi z GitHubu a přeinstaluje
#   ./install.sh --check    # jen ověří prostředí a modely, nic nemění
#
# Bez klonování, jedním řádkem (naklonuje do ~/wristtales-voice-server
# a spustí odtamtud tenhle skript):
#
#   curl -fsSL https://raw.githubusercontent.com/PavelStancik/wristtales-voice-server/main/install.sh | zsh
#
# Je idempotentní — spustit dvakrát je bezpečné. Nikdy nemaže model
# z ~/.cache/huggingface; stažení 8,7 GB je to nejdražší na celém postupu.
# Proměnné: SKIP_8BIT=1 přeskočí volitelný 8bit konvert;
# WRISTTALES_DIR mění cíl klonování; WRISTTALES_REPO mění zdroj klonu.

set -eu

# --- bootstrap: spuštěno přes `curl | zsh`, mimo klon --------------------------
# Tehdy $0 je "zsh" a vedle není žádný repozitář. Naklonujeme ho a předáme
# řízení skriptu odtamtud (přepínače se předají dál).
SELF_DIR=${0:A:h}
if [[ ! -f $SELF_DIR/requirements.txt || ! -f $SELF_DIR/wristtales_voice_server.py ]]; then
  DEST=${WRISTTALES_DIR:-$HOME/wristtales-voice-server}
  REPO=${WRISTTALES_REPO:-https://github.com/PavelStancik/wristtales-voice-server.git}
  command -v git >/dev/null 2>&1 \
    || { print -u2 -- "\n✗ Chybí git. Spusť  xcode-select --install  a zkus to znovu."; exit 1 }
  if [[ -d $DEST/.git ]]; then
    print -- "Repozitář už je v $DEST — pokračuji z něj."
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

# Nejnižší Python, na kterém mlx-audio 0.4.7 rozumně běží. Novější je lepší,
# ale 3.12+ nemá pkg_resources — to řeší setuptools<81 v requirements.txt.
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
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *)         die "neznámý přepínač: $arg" ;;
  esac
done

# --- 1. prostředí ----------------------------------------------------------

bold "1/7  Kontrola počítače"

[[ $(uname -s) == Darwin ]] || die "Tenhle server běží jen na macOS."

if [[ $(uname -m) != arm64 ]]; then
  die "Je potřeba Mac s čipem Apple (M1 a novější).
     Model počítá přes Metal a na Intelu nepoběží."
fi
ok "macOS na Apple Silicon"

# Volný prostor. Model má 8,7 GB, závislosti (hlavně torch) další ~3 GB,
# a bez rezervy se instalace utne uprostřed stahování.
FREE_GB=$(df -g "$HOME" | awk 'NR==2 {print $4}')
if (( FREE_GB < 15 )); then
  die "Na disku je volných jen ${FREE_GB} GB, potřeba je aspoň 15 GB
     (model 8,7 GB + knihovny ~3 GB + rezerva)."
fi
ok "volné místo: ${FREE_GB} GB"

# Paměť. Model v bf16 zabere při běhu kolem 10 GB.
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

# Systémový python3 z Xcode CLT bývá starý; jmenované verze mají přednost.
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

# --- 3. aktualizace zdrojáků ----------------------------------------------

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

# --- 4. knihovny -----------------------------------------------------------

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

# Ověření, že se to opravdu naimportuje. pip může skončit s nulou a modul
# přesto spadne — typicky právě na chybějícím pkg_resources.
"$PY" -W ignore - <<'PYCHECK' || die "knihovny se nainstalovaly, ale nejdou naimportovat"
import importlib, sys
for module in ("mlx", "mlx_audio", "webrtcvad", "fastapi", "uvicorn"):
    importlib.import_module(module)
PYCHECK
ok "import prošel"

# --- 5. model --------------------------------------------------------------

bold "5/7  Hlasový model ($MODEL, 8,7 GB)"
print -- "     Stahuje se jen jednou. Podruhé se vezme z ~/.cache/huggingface."

"$PY" - "$MODEL" <<'PYDL' || die "stažení modelu selhalo"
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1])
print(f"  ✓ model připraven: {path}")
PYDL

# --- 6. whisper ------------------------------------------------------------

# Druhý, menší model: whisper pro kontrolu namluveného textu. Higgs občas
# přestane mluvit dřív, než dojde na konec bloku, a z délky zvuku se to
# poznat nedá (audit knihy ABCDE: 74 uříznutých bloků, délková kontrola
# pustila všechny). Binder proto každý blok přepíše přes
# /v1/audio/transcriptions a porovná s textem; stejným endpointem přepisuje
# i hlasový vzorek uživatele. mlx_audio potřebuje whisper ve formátu
# s HF tokenizerem, proto -asr-fp16 a ne model balíčku mlx_whisper.
# Selhání tady instalaci nezastaví: Binder bez tohoto modelu kontroluje
# jen délku zvuku, jako dřív. Server o tom říká na /wristtales/capabilities.
bold "\n6/7  Whisper ($WHISPER_MODEL, 1,5 GB)"
print -- "     Kontrola, že namluvený text sedí; přepis hlasového vzorku."
if whisper_cached; then
  ok "už je v cache — přeskakuji stahování"
else
  "$PY" - "$WHISPER_MODEL" <<'PYDL' || warn "whisper se nestáhl"
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1])
print(f"  ✓ whisper připraven: {path}")
PYDL
fi

# --- 7. 8bit konvert -------------------------------------------------------

# Proč se kvantizuje lokálně a nestahuje hotové: licence Higgse je
# Research/Non-Commercial, takže odvozené váhy nikam nepřerozdělujeme.
# Konvert je navíc levný — pár minut proti 8,7 GB stahování.
#
# Je to VOLITELNÉ. Když krok selže nebo ho přeskočíš, server běží dál, jen
# v /v1/models nenabídne "higgs-v3-8bit" a Binder u volby Rychlejší (8bit)
# poctivě řekne, že ho server nemá. Nikdy nenabízíme jméno, které by při
# syntéze spadlo.

CONVERT="$ROOT/models/higgs-v3-8bit"

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

# --- hotovo ----------------------------------------------------------------

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
