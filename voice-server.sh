#!/bin/zsh
# Starts the local voice server that Binder uses for narration.
#
#   ./voice-server.sh              # start it, unless it is already running
#   ./voice-server.sh --keep-awake # + keep the Mac from sleeping (for long narrations)
#   ./voice-server.sh --lan        # listen on the whole network (0.0.0.0), not just on this Mac
#   ./voice-server.sh --lan --keep-awake  # both at once — typical for a shared server
#   ./voice-server.sh --check      # only report the state, start nothing
#   ./voice-server.sh --stop       # stop the server
#
# Port: 8000 by default (that is where Binder looks for the server); to try another
#   VOICE_SERVER_PORT=8011 ./voice-server.sh
#
# Idempotent: if the server is already running it does NOTHING and exits 0.
# It never takes down a running server — a narration in progress would lose its chapter.
#
# WARNING with --lan: the server has no login or authentication. It listens on
# 0.0.0.0, so anyone on the same network can reach it. Only run it on a network
# you trust (home/office LAN), never on public or guest Wi-Fi.

set -u

DEFAULT_HOST=127.0.0.1
LAN_HOST=0.0.0.0
HOST=$DEFAULT_HOST
PORT=${VOICE_SERVER_PORT:-8000}
ROOT=${0:A:h}
PY="$ROOT/venv/bin/python"
LOG="$ROOT/server.log"
PIDFILE="$ROOT/server.pid"

# --- helpers ---------------------------------------------------------------

# NOTE: mlx_audio handles requests one at a time. While it is synthesising a block
# it does not even answer /v1/models, sometimes for tens of seconds. An HTTP
# answer is therefore NOT a reliable test of whether the server is alive; an occupied port is.
port_pid() { lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1 }
is_running()   { [[ -n $(port_pid) ]] }
responds_now() { curl -fsS --max-time 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 }

# The address at which the server is reachable from other Macs on the network.
# en0 is typically Wi-Fi/Ethernet and en1 a fallback interface; if neither works
# the result is empty and the caller prints a generic hint instead.
lan_address() {
  local ip
  ip=$(ipconfig getifaddr en0 2>/dev/null)
  [[ -z $ip ]] && ip=$(ipconfig getifaddr en1 2>/dev/null)
  print -- "$ip"
}

# One line about what the server can do (GET /wristtales/capabilities, since 0.7.0):
# version, TTS models and above all whether Whisper is available. It stays silent
# when the server is busy computing and does not answer (not an error); an older server without the endpoint says so.
capabilities_line() {
  local out code json version models
  out=$(curl -sS --max-time 3 -w '\n%{http_code}' "http://127.0.0.1:$PORT/wristtales/capabilities" 2>/dev/null) || return 0
  code=${out##*$'\n'}
  json=${out%$'\n'*}
  if [[ $code == 404 ]]; then
    print -- "schopnosti: starší server bez /wristtales/capabilities (aktualizuj: install.sh --update)"
    return 0
  fi
  [[ $code == 200 ]] || return 0
  version=$(print -r -- "$json" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')
  models=$(print -r -- "$json" | sed -n 's/.*"models":\[\([^]]*\)\].*/\1/p' | tr -d '"')
  if [[ $json == *'"available":true'* ]]; then
    print -- "schopnosti: verze $version · TTS: ${models:-?} · Whisper: ready"
  else
    print -- "schopnosti: verze $version · TTS: ${models:-?} · Whisper: missing — Binder bude kontrolovat jen délku zvuku (doinstaluj: install.sh)"
  fi
}

die() { print -u2 -- "chyba: $*"; exit 1 }

# --- flags -----------------------------------------------------------------

KEEP_AWAKE=0
LAN=0
MODE=start
for arg in "$@"; do
  case "$arg" in
    --keep-awake) KEEP_AWAKE=1 ;;
    --lan)        LAN=1 ;;
    --check)      MODE=check ;;
    --stop)       MODE=stop ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *)            die "neznámý přepínač: $arg" ;;
  esac
done

if (( LAN )); then
  HOST=$LAN_HOST
fi
URL="http://$HOST:$PORT"

# --- status / stop ---------------------------------------------------------

if [[ $MODE == check ]]; then
  pid=$(port_pid)
  if [[ -n $pid ]]; then
    if responds_now; then
      print -- "běží a je volný  $URL  (pid $pid)"
    else
      print -- "běží, ale právě počítá — neodpovídá  $URL  (pid $pid)"
    fi
    capabilities_line
    exit 0
  fi
  print -- "neběží"
  exit 1
fi

if [[ $MODE == stop ]]; then
  pid=$(port_pid)
  [[ -z $pid ]] && { print -- "neběží, není co zastavovat"; exit 0 }
  print -- "zastavuji pid $pid …"
  kill "$pid" 2>/dev/null
  # We wait for the process to really end, not just for the port to free up —
  # the port is released slightly earlier, and a second server could then start right away.
  for i in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then
    print -- "neodpovídá na TERM, posílám KILL …"
    kill -9 "$pid" 2>/dev/null
    for i in {1..10}; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  fi
  kill -0 "$pid" 2>/dev/null && die "pid $pid se nepodařilo zastavit"
  rm -f "$PIDFILE"
  print -- "zastaveno"
  exit 0
fi

# --- start -----------------------------------------------------------------

pid=$(port_pid)
if [[ -n $pid ]]; then
  if responds_now; then
    print -- "server už běží na $URL (pid $pid) — nechávám být"
  else
    print -- "server už běží na $URL (pid $pid), právě počítá — nechávám být"
  fi
  capabilities_line
  exit 0
fi

if [[ ! -x $PY ]]; then
  die "Server ještě není nainstalovaný.
     Spusť nejdřív:  $ROOT/install.sh"
fi

print -- "spouštím wristtales_voice_server (mlx_audio.server + dávkový endpoint) …"
{
  print -- ""
  print -- "=== start $(date '+%Y-%m-%d %H:%M:%S') ==="
} >> "$LOG"

cd "$ROOT" || die "nelze vstoupit do $ROOT"
nohup "$PY" -m wristtales_voice_server --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
SERVER_PID=$!
print -- "$SERVER_PID" > "$PIDFILE"
disown 2>/dev/null

# Wait until it is ready. The model is loaded lazily on the first request,
# so /v1/models answers quickly — 60 s is a generous margin.
for i in {1..60}; do
  if responds_now; then
    print -- "připraveno za ${i} s  →  $URL  (pid $SERVER_PID)"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    print -u2 -- "server spadl při startu, posledních 20 řádků logu:"
    tail -20 "$LOG" >&2
    rm -f "$PIDFILE"
    exit 1
  fi
  sleep 1
done

if ! responds_now; then
  print -u2 -- "server se do 60 s nerozeběhl, posledních 20 řádků logu:"
  tail -20 "$LOG" >&2
  exit 1
fi

capabilities_line

if (( KEEP_AWAKE )); then
  # Keeps the Mac awake while the server runs. The display may still sleep.
  nohup caffeinate -is -w "$SERVER_PID" >/dev/null 2>&1 &
  disown 2>/dev/null
  print -- "caffeinate aktivní — Mac se neuspí, dokud server běží"
fi

if (( LAN )); then
  lan_ip=$(lan_address)
  if [[ -n $lan_ip ]]; then
    print -- "síť: z ostatních Maců použij  http://$lan_ip:$PORT"
  else
    print -- "síť: adresu zjistíš přes System Settings → Wi-Fi/Ethernet → Details, port $PORT"
  fi
  print -u2 -- "POZOR: server na 0.0.0.0 nemá žádné přihlašování — pouštěj jen na síti, které důvěřuješ."
fi

print -- "log: $LOG"
