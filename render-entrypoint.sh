#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_ROOT"

if [[ -z "${LAVALINK_PASSWORD:-}" ]]; then
  echo "ERROR: Set LAVALINK_PASSWORD in the Render service environment."
  exit 1
fi

export LAVALINK_URI="${LAVALINK_URI:-http://127.0.0.1:2333}"
export PORT="${PORT:-10000}"
export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:--XX:+UseSerialGC -Xms64m -Xmx192m -XX:MaxMetaspaceSize=96m -XX:MaxDirectMemorySize=32m -Xss512k}"

JAVA_BIN="$APP_ROOT/.render-runtime/jre/bin/java"
if [[ ! -x "$JAVA_BIN" ]]; then
  JAVA_BIN="$(command -v java || true)"
fi
if [[ -z "$JAVA_BIN" || ! -x "$JAVA_BIN" ]]; then
  echo "ERROR: Java 17 runtime not found; run bash render-build.sh first."
  exit 1
fi

(
  cd "$APP_ROOT/lavalink"
  "$JAVA_BIN" -jar "$APP_ROOT/Lavalink.jar" --server.address=127.0.0.1
) &
lavalink_pid=$!
bot_pid=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [[ -n "$bot_pid" ]] && kill -0 "$bot_pid" 2>/dev/null; then
    kill -TERM "$bot_pid" 2>/dev/null || true
  fi
  if kill -0 "$lavalink_pid" 2>/dev/null; then
    kill -TERM "$lavalink_pid" 2>/dev/null || true
  fi
  wait "$lavalink_pid" 2>/dev/null || true
  if [[ -n "$bot_pid" ]]; then
    wait "$bot_pid" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

ready=0
for _ in {1..120}; do
  if ! kill -0 "$lavalink_pid" 2>/dev/null; then
    echo "ERROR: Lavalink exited during startup."
    exit 1
  fi
  if python -c "import socket; s=socket.create_connection(('127.0.0.1', 2333), timeout=0.5); s.close()" 2>/dev/null; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" != "1" ]]; then
  echo "ERROR: Lavalink did not open port 2333 within 120 seconds."
  exit 1
fi

python "$APP_ROOT/bot.py" &
bot_pid=$!

set +e
wait -n "$lavalink_pid" "$bot_pid"
status=$?
set -e
exit "$status"
