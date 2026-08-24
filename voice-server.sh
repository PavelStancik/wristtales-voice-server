#!/bin/zsh
# Spustí lokální hlasový server pro namlouvání v Binderu.
#
#   ./start-voice-server.sh              # spustí, pokud neběží
#   ./start-voice-server.sh --keep-awake # + zabrání uspání Macu (na dlouhé narace)
#   ./start-voice-server.sh --check      # jen zjistí stav, nic nespouští
#   ./start-voice-server.sh --stop       # zastaví server
#
# Je idempotentní: když už server běží, NIC neudělá a skončí s kódem 0.
# Nikdy neshazuje běžící server — rozdělaná narace by přišla o rozdělanou kapitolu.

set -u

HOST=127.0.0.1
PORT=8000
ROOT=${0:A:h}
PY="$ROOT/venv/bin/python"
LOG="$ROOT/server.log"
PIDFILE="$ROOT/server.pid"
URL="http://$HOST:$PORT"

# --- pomocné ---------------------------------------------------------------

# POZOR: mlx_audio zpracovává požadavky sériově. Když právě syntetizuje blok,
# neodpoví ani na /v1/models — klidně desítky sekund. HTTP odpověď proto NENÍ
# spolehlivý test toho, jestli server žije; obsazený port ano.
port_pid() { lsof -nP -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1 }
is_running()   { [[ -n $(port_pid) ]] }
responds_now() { curl -fsS --max-time 3 "$URL/v1/models" >/dev/null 2>&1 }

die() { print -u2 -- "chyba: $*"; exit 1 }

# --- přepínače -------------------------------------------------------------

KEEP_AWAKE=0
MODE=start
for arg in "$@"; do
  case "$arg" in
    --keep-awake) KEEP_AWAKE=1 ;;
    --check)      MODE=check ;;
    --stop)       MODE=stop ;;
    -h|--help)    sed -n '2,12p' "$0"; exit 0 ;;
    *)            die "neznámý přepínač: $arg" ;;
  esac
done

# --- stav / zastavení ------------------------------------------------------

if [[ $MODE == check ]]; then
  pid=$(port_pid)
  if [[ -n $pid ]]; then
    if responds_now; then
      print -- "běží a je volný  $URL  (pid $pid)"
    else
      print -- "běží, ale právě počítá — neodpovídá  $URL  (pid $pid)"
    fi
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
  # Čekáme na skutečný konec procesu, ne jen na uvolnění portu — port se
  # uvolní o kousek dřív a hned nato by šel nastartovat druhý server.
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
  exit 0
fi

if [[ ! -x $PY ]]; then
  die "Server ještě není nainstalovaný.
     Spusť nejdřív:  $ROOT/install.sh"
fi

print -- "spouštím mlx_audio.server …"
{
  print -- ""
  print -- "=== start $(date '+%Y-%m-%d %H:%M:%S') ==="
} >> "$LOG"

cd "$ROOT" || die "nelze vstoupit do $ROOT"
nohup "$PY" -m mlx_audio.server --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
SERVER_PID=$!
print -- "$SERVER_PID" > "$PIDFILE"
disown 2>/dev/null

# Čekání na připravenost. Model se načítá líně až při prvním požadavku,
# takže /v1/models odpoví rychle — 60 s je s velkou rezervou.
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

if (( KEEP_AWAKE )); then
  # Drží Mac vzhůru, dokud běží server. Displej se uspat smí.
  nohup caffeinate -is -w "$SERVER_PID" >/dev/null 2>&1 &
  disown 2>/dev/null
  print -- "caffeinate aktivní — Mac se neuspí, dokud server běží"
fi

print -- "log: $LOG"
