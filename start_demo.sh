#!/usr/bin/env bash
# Runs both servers in one shell without sharing secrets across terminal tabs.
set -euo pipefail
cd "$(dirname "$0")"
# Fail clearly if the user is still running an older extraction on either port.
python - <<'PY_CHECK_PORTS'
import socket,sys
busy=[]
for port in (8000,8001):
    sock=socket.socket();sock.settimeout(0.2)
    try:
        if sock.connect_ex(('127.0.0.1',port))==0: busy.append(str(port))
    finally: sock.close()
if busy:
    print('Port(s) '+', '.join(busy)+' already in use. Stop the prior start_demo.sh (Ctrl+C), then relaunch from this new folder.',file=sys.stderr)
    print('On macOS: lsof -nP -iTCP:8000 -sTCP:LISTEN',file=sys.stderr)
    sys.exit(1)
PY_CHECK_PORTS
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi
if [[ -z "${LITHIC_API_KEY:-}" ]]; then
  read -r -s -p 'Lithic SANDBOX API key (hidden): ' LITHIC_API_KEY
  printf '\n'
  export LITHIC_API_KEY
fi
if [[ -z "${LITHIC_ASA_SECRET:-}" ]]; then
  read -r -s -p 'Lithic ASA HMAC secret (press Enter if not enrolled yet): ' LITHIC_ASA_SECRET
  printf '\n'
  export LITHIC_ASA_SECRET
fi
if [[ -z "${LITHIC_EVENT_SECRET:-}" ]]; then
  read -r -s -p 'Lithic Events API signing secret (Enter to skip live clearing callbacks): ' LITHIC_EVENT_SECRET
  printf '\n'
  export LITHIC_EVENT_SECRET
fi
export OCCLET_BRIDGE_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(40))')"

uvicorn app:app --host 127.0.0.1 --port 8000 &
UI_PID=$!
uvicorn lithic_bridge:app --host 127.0.0.1 --port 8001 &
BRIDGE_PID=$!
cleanup(){ kill "$UI_PID" "$BRIDGE_PID" 2>/dev/null || true; wait "$UI_PID" "$BRIDGE_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
printf 'Local browser UI: http://127.0.0.1:8000\nASA bridge: http://127.0.0.1:8001/lithic/asa\nEvents bridge: http://127.0.0.1:8001/lithic/events\nExpose only port 8001, never port 8000.\n'
wait "$UI_PID" "$BRIDGE_PID"
