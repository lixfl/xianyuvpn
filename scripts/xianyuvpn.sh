#!/usr/bin/env bash
# ============================================================
# xianyuvpn - command-line proxy manager
# Usage: ./xianyuvpn.sh <start|stop|restart|status|reload|test|log|proxy>
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BIN="$PROJECT_DIR/bin/mihomo"
CONFIG_DIR="$PROJECT_DIR/config"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
DATA_DIR="$PROJECT_DIR/data"
PID_FILE="$PROJECT_DIR/mihomo.pid"
LOG_FILE="$PROJECT_DIR/mihomo.log"
API="http://127.0.0.1:9090"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

cmd_start() {
    if is_running; then
        echo -e "${YELLOW}Already running (PID: $(cat $PID_FILE))${NC}"
        return 0
    fi
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}Error: config file not found${NC}"
        echo "Please run: ./update-sub.sh <subscription-url>"
        exit 1
    fi
    echo "Starting mihomo..."
    nohup "$BIN" -d "$DATA_DIR" -f "$CONFIG_FILE" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if is_running; then
        echo -e "${GREEN}Started successfully (PID: $(cat $PID_FILE))${NC}"
        echo "  Mixed proxy port: 7890 (HTTP + SOCKS5)"
        echo "  API controller: $API"
        echo "  Log: tail -f $LOG_FILE"
    else
        echo -e "${RED}Failed to start, check log: $LOG_FILE${NC}"
        tail -20 "$LOG_FILE" 2>/dev/null
        exit 1
    fi
}

cmd_stop() {
    if ! is_running; then
        echo -e "${YELLOW}Not running${NC}"
        return 0
    fi
    local pid=$(cat "$PID_FILE")
    echo "Stopping mihomo (PID: $pid)..."
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}Force killing...${NC}"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo -e "${GREEN}Stopped${NC}"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if is_running; then
        echo -e "${GREEN}Running${NC} (PID: $(cat $PID_FILE))"
        if curl -s "$API/version" >/dev/null 2>&1; then
            local ver=$(curl -s "$API/version" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('version','?'))" 2>/dev/null)
            echo "  Core version: $ver"
        fi
        echo "  Proxy port: 7890"
        echo "  Config file: $CONFIG_FILE"
    else
        echo -e "${RED}Not running${NC}"
    fi
}

cmd_reload() {
    if ! is_running; then
        echo -e "${RED}Error: not running${NC}"
        exit 1
    fi
    echo "Reloading config..."
    local code=$(curl -s -o /dev/null -w "%{http_code}" -X PUT "$API/configs?force=true" --data-binary "{\"path\":\"$CONFIG_FILE\"}" 2>/dev/null || echo "000")
    if [ "$code" = "204" ] || [ "$code" = "200" ]; then
        echo -e "${GREEN}Config reloaded${NC}"
    else
        echo -e "${YELLOW}API reload failed (code=$code), restarting...${NC}"
        cmd_restart
    fi
}

cmd_test() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}Error: config file not found${NC}"
        exit 1
    fi
    echo "Testing config file..."
    "$BIN" -t -d "$DATA_DIR" -f "$CONFIG_FILE" 2>&1
}

cmd_log() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file yet"
    fi
}

cmd_proxy() {
    echo "export http_proxy=http://127.0.0.1:7890"
    echo "export https_proxy=http://127.0.0.1:7890"
    echo "export all_proxy=socks5://127.0.0.1:7890"
    echo "export no_proxy=localhost,127.0.0.1,::1"
    echo ""
    echo "# Run this to enable proxy for current shell:"
    echo "eval \$(xy proxy)"
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    reload)  cmd_reload ;;
    test)    cmd_test ;;
    log)     cmd_log ;;
    proxy)   cmd_proxy ;;
    *)
        echo "xianyuvpn - command-line proxy manager"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  start    Start proxy"
        echo "  stop     Stop proxy"
        echo "  restart  Restart proxy"
        echo "  status   Show status"
        echo "  reload   Reload config"
        echo "  test     Test config file"
        echo "  log      Follow logs"
        echo "  proxy    Print proxy env vars"
        echo ""
        echo "First time:"
        echo "  1. xy update <your-subscription-url>"
        echo "  2. xy start"
        echo "  3. eval \$(xy proxy)"
        ;;
esac
