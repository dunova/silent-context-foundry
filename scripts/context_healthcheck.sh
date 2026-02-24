#!/bin/bash
# =============================================================================
# Unified Context System Health Check
# onecontext + openviking + gsd bridge runtime checks
# =============================================================================

LOG_DIR="$HOME/.context_system/logs"
HEALTHCHECK_LOG="$LOG_DIR/healthcheck.log"
UNIFIED_CONTEXT_STORAGE_ROOT="${UNIFIED_CONTEXT_STORAGE_ROOT:-${OPENVIKING_STORAGE_ROOT:-$HOME/.unified_context_data}}"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR" 2>/dev/null || true
PRINT_STDOUT=1
DEEP_PROBE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --quiet) PRINT_STDOUT=0 ;;
        --deep) DEEP_PROBE=1 ;;
    esac
    shift
done

TS=$(date '+%Y-%m-%d %H:%M:%S')
STATUS=0
REPORT=""

file_size_bytes() {
    local p="$1"
    stat -f%z "$p" 2>/dev/null || stat -c%s "$p" 2>/dev/null || echo 0
}

file_perm_mode() {
    local p="$1"
    stat -f%Lp "$p" 2>/dev/null || stat -c%a "$p" 2>/dev/null || echo 000
}

check_process() {
    local name="$1"
    local pattern="$2"
    if pgrep -f "$pattern" > /dev/null 2>&1; then
        REPORT+="  ✅ $name: running (PID $(pgrep -f "$pattern" | head -1))\n"
    else
        REPORT+="  ❌ $name: NOT RUNNING\n"
        STATUS=1
    fi
}

check_openviking_api() {
    local http_status
    local deep_status=""
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
      "http://127.0.0.1:8090/health" \
      --max-time 4 2>/dev/null || echo "000")

    if [ "$DEEP_PROBE" = "1" ] || [ "$http_status" != "200" ]; then
        # Deep probe verifies core search endpoint in addition to /health.
        deep_status=$(curl -s -o /dev/null -w "%{http_code}" \
          -X POST "http://127.0.0.1:8090/api/v1/search/find" \
          -H "Content-Type: application/json" \
          -d '{"query":"healthcheck","target_uri":"viking://resources","limit":1}' \
          --max-time 8 2>/dev/null || echo "000")
        if [ "$http_status" != "200" ]; then
            http_status="$deep_status"
        fi
    fi

    if [ "$http_status" = "200" ]; then
        REPORT+="  ✅ openviking-api: HTTP 200\n"
        if [ "$DEEP_PROBE" = "1" ]; then
            if [ "$deep_status" = "200" ]; then
                REPORT+="  ✅ openviking-deep-probe: HTTP 200\n"
            else
                REPORT+="  ❌ openviking-deep-probe: HTTP ${deep_status:-000}\n"
                STATUS=1
            fi
        fi
    else
        REPORT+="  ❌ openviking-api: HTTP $http_status\n"
        STATUS=1
    fi
}

check_onecontext() {
    local rc=127
    local cli_name=""
    local cli_output=""
    if command -v onecontext >/dev/null 2>&1; then
        cli_name="onecontext"
        set +e
        cli_output="$(onecontext search "healthcheck" -t all -l 1 2>&1)"
        rc=$?
        set -e
    elif command -v aline >/dev/null 2>&1; then
        cli_name="aline"
        set +e
        cli_output="$(aline search "healthcheck" -t all -l 1 2>&1)"
        rc=$?
        set -e
    fi

    if [ "$rc" = "0" ]; then
        REPORT+="  ✅ onecontext-search: callable ($cli_name)\n"
    elif [ "$rc" = "127" ]; then
        REPORT+="  ⚠️  onecontext-search: no cli command found\n"
    elif echo "$cli_output" | grep -Eq 'Found 0 matches|No matches found'; then
        REPORT+="  ✅ onecontext-search: callable ($cli_name, no matches)\n"
    else
        REPORT+="  ❌ onecontext-search: error ($cli_name, exit=$rc)\n"
        STATUS=1
    fi
}

check_source_file() {
    local label="$1"
    local path="$2"
    local required="$3"

    if [ -f "$path" ]; then
        REPORT+="  ✅ $label: $path\n"
    else
        if [ "$required" = "1" ]; then
            REPORT+="  ❌ $label missing: $path\n"
            STATUS=1
        else
            REPORT+="  ℹ️  $label not found: $path\n"
        fi
    fi
}

check_log_size() {
    local label="$1"
    local path="$2"
    local max_mb="${3:-100}"
    if [ -f "$path" ]; then
        local size_mb=$(( $(file_size_bytes "$path") / 1048576 ))
        if [ "$size_mb" -gt "$max_mb" ]; then
            REPORT+="  ⚠️  $label: ${size_mb}MB (>${max_mb}MB) – truncating\n"
            local tmpfile
            tmpfile="$(mktemp "${path}.XXXXXX")" || { REPORT+="  ❌ $label: failed to create tmpfile\n"; return; }
            tail -c $((max_mb * 1048576 / 2)) "$path" > "$tmpfile" && mv "$tmpfile" "$path" || rm -f "$tmpfile"
            STATUS=1
        else
            REPORT+="  ✅ $label: ${size_mb}MB\n"
        fi
    fi
}

check_perm_max() {
    local label="$1"
    local path="$2"
    local max_perm="$3"
    if [ ! -f "$path" ]; then
        REPORT+="  ℹ️  $label not found: $path\n"
        return 0
    fi
    local perm
    perm=$(file_perm_mode "$path")
    if [ "$perm" -le "$max_perm" ]; then
        REPORT+="  ✅ $label perms: $perm\n"
    else
        REPORT+="  ⚠️  $label perms too open: $perm (expected <= $max_perm)\n"
        STATUS=1
    fi
}

check_launchd_job() {
    local label="$1"
    local uid_num
    uid_num="$(id -u)"
    if ! command -v launchctl >/dev/null 2>&1; then
        REPORT+="  ℹ️  launchctl unavailable\n"
        return 0
    fi
    local state
    state=$(launchctl print "gui/${uid_num}/${label}" 2>/dev/null | awk -F'= ' '/^[[:space:]]*state = / {print $2; exit}')
    if [ -z "$state" ]; then
        REPORT+="  ❌ launchd $label: missing\n"
        STATUS=1
        return 0
    fi
    if [ "$label" = "com.context.healthcheck" ] && [ "$state" = "not running" ]; then
        REPORT+="  ✅ launchd $label: loaded ($state)\n"
        return 0
    fi
    if [ "$state" = "running" ] || [ "$state" = "spawn scheduled" ]; then
        REPORT+="  ✅ launchd $label: $state\n"
    else
        REPORT+="  ⚠️  launchd $label: $state\n"
        STATUS=1
    fi
}

REPORT+="[$TS] Context System Health Check\n"
REPORT+="─────────────────────────────────\n"
REPORT+="Processes:\n"

check_process "viking_daemon" "viking_daemon.py"
check_process "openviking-server" "openviking-server|openviking.server.bootstrap"
check_process "aline-watcher" "realign.watcher_daemon"
check_process "aline-worker" "realign.worker_daemon"
check_openviking_api
check_onecontext

REPORT+="\nLaunchd:\n"
check_launchd_job "com.openviking.server"
check_launchd_job "com.openviking.daemon"
check_launchd_job "com.context.healthcheck"

REPORT+="\nSources:\n"
check_source_file "claude-history" "$HOME/.claude/history.jsonl" 0
check_source_file "codex-history" "$HOME/.codex/history.jsonl" 0
check_source_file "opencode-history" "$HOME/.local/state/opencode/prompt-history.jsonl" 0
check_source_file "zsh-history" "$HOME/.zsh_history" 0
check_source_file "bash-history" "$HOME/.bash_history" 0

REPORT+="\nPermissions:\n"
check_perm_max "openviking-config" "$HOME/.openviking_data/ov.conf" 600
check_perm_max "antigravity-secrets" "$HOME/.antigravity_secrets" 600

REPORT+="\nLog Sizes:\n"
check_log_size "viking_daemon" "$LOG_DIR/viking_daemon.log" 50
check_log_size "openviking_server" "$LOG_DIR/openviking_server_launchd.log" 50
check_log_size "aline_watcher" "$HOME/.aline/.logs/watcher_core.log" 100
check_log_size "aline_worker" "$HOME/.aline/.logs/worker_core.log" 100
check_log_size "aline_watcher_stderr" "$HOME/.aline/.logs/watcher_stderr.log" 200
check_log_size "aline_llm" "$HOME/.aline/.logs/llm.log" 200

REPORT+="\nAline DB:\n"
if ! command -v sqlite3 >/dev/null 2>&1; then
    REPORT+="  ℹ️  sqlite3 not found, skipping DB checks\n"
elif [ -f "$HOME/.aline/db/aline.db" ]; then
    RECENT=$(sqlite3 "$HOME/.aline/db/aline.db" "SELECT count(*) FROM sessions WHERE created_at > datetime('now', '-2 hours');" 2>/dev/null || echo "ERR")
    if [ "$RECENT" = "0" ] || [ "$RECENT" = "ERR" ]; then
        REPORT+="  ⚠️  No new sessions in the last 2 hours ($RECENT)\n"
    else
        REPORT+="  ✅ $RECENT sessions in the last 2 hours\n"
    fi
    DB_SIZE=$(( $(file_size_bytes "$HOME/.aline/db/aline.db") / 1048576 ))
    REPORT+="  📦 DB size: ${DB_SIZE}MB\n"
else
    REPORT+="  ⚠️  ~/.aline/db/aline.db missing\n"
fi

REPORT+="\nViking Sync:\n"
PENDING_DIR="$UNIFIED_CONTEXT_STORAGE_ROOT/resources/shared/history/.pending"
if [ -d "$PENDING_DIR" ]; then
    PENDING_COUNT=$(ls -1 "$PENDING_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
    if [ "$PENDING_COUNT" -gt 0 ]; then
        REPORT+="  ⚠️  $PENDING_COUNT pending sync files\n"
    else
        REPORT+="  ✅ No pending sync files\n"
    fi
else
    REPORT+="  ✅ No pending directory\n"
fi

REPORT+="\n"
if [ "$STATUS" -eq 0 ]; then
    REPORT+="🟢 All systems nominal.\n"
else
    REPORT+="🔴 Issues detected – review above.\n"
fi
REPORT+="─────────────────────────────────\n\n"

if [ "$PRINT_STDOUT" = "1" ]; then
    echo -e "$REPORT"
fi
echo -e "$REPORT" >> "$HEALTHCHECK_LOG"

HC_SIZE=$(( $(file_size_bytes "$HEALTHCHECK_LOG") / 1048576 ))
if [ "$HC_SIZE" -gt 5 ]; then
    HC_TMPFILE="$(mktemp "${HEALTHCHECK_LOG}.XXXXXX")" && \
        tail -c 2621440 "$HEALTHCHECK_LOG" > "$HC_TMPFILE" && \
        mv "$HC_TMPFILE" "$HEALTHCHECK_LOG" || \
        rm -f "$HC_TMPFILE" 2>/dev/null
fi

exit $STATUS
