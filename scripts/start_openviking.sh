#!/bin/bash
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration (env-overridable)
OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-$HOME/.openviking_data}"
VENV_DIR="${OPENVIKING_VENV_DIR:-$HOME/.openviking_env}"
HOST="${OPENVIKING_HOST:-127.0.0.1}"
PORT="${OPENVIKING_PORT:-8090}"
CONFIG_FILE="${OPENVIKING_CONFIG_FILE:-$OPENVIKING_DATA_DIR/ov.conf}"
GENERATOR_SCRIPT="${OPENVIKING_CONFIG_GENERATOR:-}"
OPENVIKING_SKIP_PIP="${OPENVIKING_SKIP_PIP:-1}"
OPENVIKING_UPGRADE_PIP="${OPENVIKING_UPGRADE_PIP:-0}"
OPENVIKING_FORCE_UPGRADE="${OPENVIKING_FORCE_UPGRADE:-0}"
OPENVIKING_PORT_WAIT_SEC="${OPENVIKING_PORT_WAIT_SEC:-20}"
OPENVIKING_GENERATOR_TIMEOUT_SEC="${OPENVIKING_GENERATOR_TIMEOUT_SEC:-20}"
OPENVIKING_ALLOW_NAS_GENERATOR="${OPENVIKING_ALLOW_NAS_GENERATOR:-0}"
LITELLM_LOCAL_MODEL_COST_MAP="${LITELLM_LOCAL_MODEL_COST_MAP:-True}"

# Load secrets (contains GEMINI_API_KEY / OPENAI_API_KEY)
# Safety: parse KEY=VALUE / export KEY=VALUE lines instead of sourcing arbitrary shell code
if [ -f "$HOME/.antigravity_secrets" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # Trim leading/trailing whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        # Skip empty lines and comments
        case "$line" in
            ''|\#*) continue ;;
        esac
        # Support both "KEY=VALUE" and "export KEY=VALUE"
        case "$line" in
            export[[:space:]]*) line="${line#export }" ;;
        esac
        case "$line" in
            *=*) ;;
            *) continue ;;
        esac
        key="${line%%=*}"
        value="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        # Strip surrounding quotes from value
        value="${value%\"}" ; value="${value#\"}"
        value="${value%\'}" ; value="${value#\'}"
        # Only export well-formed variable names
        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            export "$key=$value"
        fi
    done < "$HOME/.antigravity_secrets"
fi

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}Starting OpenViking Central Service...${NC}"
export LITELLM_LOCAL_MODEL_COST_MAP

mkdir -p "$OPENVIKING_DATA_DIR"
chmod 700 "$OPENVIKING_DATA_DIR" >/dev/null 2>&1 || true

resolve_generator_script() {
    if [ -n "${GENERATOR_SCRIPT:-}" ]; then
        echo "$GENERATOR_SCRIPT"
        return 0
    fi

    for candidate in \
        "$REPO_ROOT/scripts/generate_ov_config.py" \
        "$HOME/.codex/skills/openviking-memory-sync/scripts/generate_ov_config.py" \
        "$HOME/.agents/skills/openviking-memory-sync/scripts/generate_ov_config.py"; do
        if [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    echo ""
}

port_in_use() {
    lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1
}

probe_health_http() {
    curl -s -o /dev/null -w "%{http_code}" "http://$HOST:$PORT/health" --max-time 2 2>/dev/null || echo "000"
}

wait_for_port_release() {
    local waited=0
    if ! port_in_use; then
        return 0
    fi

    echo -e "${YELLOW}Port $PORT is busy, waiting up to ${OPENVIKING_PORT_WAIT_SEC}s for graceful release...${NC}"
    while [ "$waited" -lt "$OPENVIKING_PORT_WAIT_SEC" ]; do
        if ! port_in_use; then
            echo "Port $PORT released after ${waited}s."
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if [ "$(probe_health_http)" = "200" ]; then
        echo -e "${YELLOW}OpenViking already healthy on port $PORT, skipping duplicate start.${NC}"
        exit 0
    fi

    echo -e "${RED}Error: Port $PORT still in use after ${OPENVIKING_PORT_WAIT_SEC}s.${NC}"
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
    exit 1
}

GENERATOR_SCRIPT="$(resolve_generator_script)"
wait_for_port_release

# Find preferred python executable
if command -v python3.13 &>/dev/null; then
    PYTHON_EXEC="python3.13"
elif command -v python3.11 &>/dev/null; then
    PYTHON_EXEC="python3.11"
elif command -v python3 &>/dev/null; then
    PYTHON_EXEC="python3"
else
    echo -e "${RED}Error: Python is not installed.${NC}"
    exit 1
fi

echo "Using Python: $($PYTHON_EXEC --version)"

# 1. Setup virtual environment if needed
# Safety: prevent rm -rf on unexpected paths
case "$VENV_DIR" in
    /|/usr|/usr/*|/etc|/var|/tmp|/home|/System|/Library)
        echo -e "${RED}Error: OPENVIKING_VENV_DIR points to a dangerous path: $VENV_DIR${NC}"
        exit 1
        ;;
esac
REAL_VENV_DIR="$(cd "$(dirname "$VENV_DIR")" 2>/dev/null && pwd)/$(basename "$VENV_DIR")" || REAL_VENV_DIR="$VENV_DIR"
case "$REAL_VENV_DIR" in
    "$HOME"/*) ;; # acceptable
    *)
        echo -e "${RED}Error: OPENVIKING_VENV_DIR must be under \$HOME ($HOME). Got: $REAL_VENV_DIR${NC}"
        exit 1
        ;;
esac

NEED_RECREATE=0
if [ -d "$VENV_DIR" ]; then
    VENV_PYTHON_VER=$("$VENV_DIR/bin/python" --version 2>/dev/null || echo "None")
    SYSTEM_PYTHON_VER=$($PYTHON_EXEC --version)
    if [ "$VENV_PYTHON_VER" != "$SYSTEM_PYTHON_VER" ]; then
        echo "Virtual environment python version mismatch ($VENV_PYTHON_VER vs $SYSTEM_PYTHON_VER), recreating..."
        NEED_RECREATE=1
    fi
else
    NEED_RECREATE=1
fi

if [ "$NEED_RECREATE" -eq 1 ]; then
    [ -d "$VENV_DIR" ] && rm -rf "$VENV_DIR"
    echo "Creating virtual environment at $VENV_DIR..."
    "$PYTHON_EXEC" -m venv "$VENV_DIR"
fi

# 2. Activate virtual environment
source "$VENV_DIR/bin/activate"

# 3. Install/Update openviking (optional fast path)
if [ "$OPENVIKING_UPGRADE_PIP" = "1" ]; then
    echo "Upgrading pip..."
    python -m pip install --upgrade pip
fi

if [ "$OPENVIKING_SKIP_PIP" = "1" ] && [ "$OPENVIKING_FORCE_UPGRADE" != "1" ] && python -c "import openviking" >/dev/null 2>&1; then
    echo "openviking already installed, skip pip update (OPENVIKING_SKIP_PIP=1)."
else
    echo "Installing/Updating openviking..."
    if [ "$OPENVIKING_FORCE_UPGRADE" = "1" ]; then
        python -m pip install --upgrade openviking
    else
        python -m pip install openviking
    fi
fi

# 4. Generate Configuration (optional; existing config remains valid)
echo "Generating configuration..."
export OPENVIKING_DATA_DIR
if [ -n "$GENERATOR_SCRIPT" ] && [[ "$GENERATOR_SCRIPT" == /Volumes/* ]] && [ "$OPENVIKING_ALLOW_NAS_GENERATOR" != "1" ]; then
    echo -e "${YELLOW}Warning: Skip NAS generator path ($GENERATOR_SCRIPT). Set OPENVIKING_ALLOW_NAS_GENERATOR=1 to enable.${NC}"
elif [ -n "$GENERATOR_SCRIPT" ] && [ -f "$GENERATOR_SCRIPT" ]; then
    # Safety: only execute generator scripts owned by the current user
    GENERATOR_OWNER=$(stat -f%u "$GENERATOR_SCRIPT" 2>/dev/null || stat -c%u "$GENERATOR_SCRIPT" 2>/dev/null || echo "-1")
    if [ "$GENERATOR_OWNER" != "$(id -u)" ]; then
        echo -e "${RED}Error: Config generator $GENERATOR_SCRIPT is not owned by current user (owner=$GENERATOR_OWNER). Skipping.${NC}"
    else
        if ! python - "$GENERATOR_SCRIPT" "$OPENVIKING_GENERATOR_TIMEOUT_SEC" <<'PY'
import subprocess, sys
script = sys.argv[1]
timeout_sec = int(sys.argv[2])
try:
    subprocess.run([sys.executable, script], check=True, timeout=timeout_sec)
except subprocess.TimeoutExpired:
    print(f"Generator timeout after {timeout_sec}s: {script}", file=sys.stderr)
    sys.exit(124)
PY
        then
            echo -e "${YELLOW}Warning: Config generator failed/timed out; continuing with existing config if present.${NC}"
        fi
    fi
else
    echo -e "${YELLOW}Warning: No local config generator found; using existing config if present.${NC}"
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Configuration file $CONFIG_FILE not found or generation failed.${NC}"
    exit 1
fi
chmod 600 "$CONFIG_FILE" >/dev/null 2>&1 || true

if [ -z "${GEMINI_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    echo -e "${YELLOW}Warning: GEMINI_API_KEY / OPENAI_API_KEY not found in env.${NC}"
    echo -e "${YELLOW}If $CONFIG_FILE already contains valid key, service can still run.${NC}"
fi

# 5. Start Server (exec to avoid extra shell process under launchd)
echo -e "${GREEN}OpenViking Service listening on http://$HOST:$PORT${NC}"
echo "Data Directory: $OPENVIKING_DATA_DIR"
echo "Config File: $CONFIG_FILE"

if [ -x "$VENV_DIR/bin/openviking-server" ]; then
    exec "$VENV_DIR/bin/openviking-server" --host "$HOST" --port "$PORT" --config "$CONFIG_FILE"
elif command -v openviking-server &>/dev/null; then
    exec openviking-server --host "$HOST" --port "$PORT" --config "$CONFIG_FILE"
else
    exec python -m openviking.server.bootstrap --host "$HOST" --port "$PORT" --config "$CONFIG_FILE"
fi
