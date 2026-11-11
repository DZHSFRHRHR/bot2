#!/bin/sh
set -eu

: "${TELEGRAM_API_ID:?Add TELEGRAM_API_ID in Railway Variables}"
: "${TELEGRAM_API_HASH:?Add TELEGRAM_API_HASH in Railway Variables}"

# A Railway Volume mounted at /data is recommended for large files.
# If no volume exists, files use ephemeral container disk space.
ROOT="${RAILWAY_VOLUME_MOUNT_PATH:-/data}"
export TELEGRAM_WORK_DIR="$ROOT/api"
export TELEGRAM_TEMP_DIR="$ROOT/tmp"
export SHARED_DIR="$ROOT/output"
export TELEGRAM_LOCAL=1
export TELEGRAM_HTTP_IP_ADDRESS=127.0.0.1
export TELEGRAM_HTTP_PORT=8081
export LOCAL_BOT_API_URL=http://127.0.0.1:8081

mkdir -p "$TELEGRAM_WORK_DIR" "$TELEGRAM_TEMP_DIR" "$SHARED_DIR"
chown -R 101:101 "$TELEGRAM_WORK_DIR" "$TELEGRAM_TEMP_DIR"
chmod 755 "$SHARED_DIR"

api_pid=""
bot_pid=""
cleanup() {
    if [ -n "$bot_pid" ]; then kill "$bot_pid" 2>/dev/null || true; fi
    if [ -n "$api_pid" ]; then kill "$api_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

# Uses the original image's official startup script and --local mode.
/docker-entrypoint.sh &
api_pid=$!

i=0
while [ "$i" -lt 60 ]; do
    if ! kill -0 "$api_pid" 2>/dev/null; then
        echo "Local Bot API process exited during startup" >&2
        exit 1
    fi
    if /opt/venv/bin/python -c \
        'import socket; s=socket.create_connection(("127.0.0.1",8081), timeout=1); s.close()' \
        >/dev/null 2>&1; then
        echo "Local Bot API listening on 127.0.0.1:8081"
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$i" -ge 60 ]; then
    echo "Local Bot API did not start in 60 attempts" >&2
    exit 1
fi

/opt/venv/bin/python -u /app/bot.py &
bot_pid=$!

# If either process dies, shut down this Railway worker to allow restart.
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$bot_pid" 2>/dev/null; do
    sleep 2
done

echo "A bot process has exited; stopping the container" >&2
exit 1
