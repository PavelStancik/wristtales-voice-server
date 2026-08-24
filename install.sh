#!/bin/zsh
# Nainstaluje (nebo zaktualizuje) lokální hlasový server pro Binder.
#
#   ./install.sh            # nainstaluje, nebo doinstaluje co chybí
#   ./install.sh --update   # stáhne novou verzi z GitHubu a přeinstaluje
#   ./install.sh --check    # jen ověří prostředí, nic nemění
#
# Je idempotentní — spustit dvakrát je bezpečné. Nikdy nemaže model
# z ~/.cache/huggingface; stažení 8,7 GB je to nejdražší na celém postupu.

set -eu

ROOT=${0:A:h}
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

bold "1/5  Kontrola počítače"

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

bold "2/5  Hledání Pythonu"

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
  exit 0
fi

# --- 3. aktualizace zdrojáků ----------------------------------------------

if [[ $MODE == update ]]; then
  bold "3/5  Aktualizace z GitHubu"
  if [[ -d "$ROOT/.git" ]]; then
    git -C "$ROOT" pull --ff-only || die "git pull neprošel — máš v repu vlastní změny?"
    ok "zdrojáky aktuální"
  else
    warn "tohle není git repo, přeskakuji stažení novinek"
  fi
else
  bold "3/5  Zdrojáky"
  ok "používám, co leží v $ROOT"
fi

# --- 4. knihovny -----------------------------------------------------------

bold "4/5  Instalace knihoven (několik minut, stahuje ~3 GB)"

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

bold "5/5  Stažení modelu ($MODEL, 8,7 GB)"
print -- "     Stahuje se jen jednou. Podruhé se vezme z ~/.cache/huggingface."

"$PY" - "$MODEL" <<'PYDL' || die "stažení modelu selhalo"
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1])
print(f"  ✓ model připraven: {path}")
PYDL

# --- hotovo ----------------------------------------------------------------

bold "\nHotovo."
print -- "Server spustíš takto:"
print -- ""
print -- "    $ROOT/voice-server.sh"
print -- ""
print -- "Pak v Binderu zvol Namluvit… — server si najde sám."
