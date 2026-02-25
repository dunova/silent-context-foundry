#!/bin/bash
set -euo pipefail

# Task file format (tab-separated):
#   ISSUE<TAB>PROMPT
# or plain prompt per line:
#   Implement X and add tests

AO_PROJECT=""
TASK_FILE=""
DRY_RUN=0
PREWARM_QUERY=""
MAX_TASKS=""
SLEEP_BETWEEN_SEC="1"

usage() {
  cat <<USAGE
Usage: $(basename "$0") --project <ao-project> --file <tasks.txt> [options]

Options:
  --project NAME         AO project id from agent-orchestrator.yaml
  --file PATH            Task file (one task per line)
  --prewarm QUERY        Run scf_context_prewarm.sh before spawning
  --max N                Spawn at most N tasks
  --sleep SEC            Delay between spawns (default: 1)
  --dry-run              Print what would run
  -h, --help             Show help

Examples:
  $(basename "$0") --project scf --file integrations/agent-orchestrator/examples/tasks.tsv
  $(basename "$0") --project openviking --file /tmp/tasks.tsv --prewarm "phase execute indexing"
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) shift; AO_PROJECT="${1:-}" ;;
    --file) shift; TASK_FILE="${1:-}" ;;
    --prewarm) shift; PREWARM_QUERY="${1:-}" ;;
    --max) shift; MAX_TASKS="${1:-}" ;;
    --sleep) shift; SLEEP_BETWEEN_SEC="${1:-1}" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

[ -n "$AO_PROJECT" ] || { echo "--project is required" >&2; exit 1; }
[ -n "$TASK_FILE" ] || { echo "--file is required" >&2; exit 1; }
[ -f "$TASK_FILE" ] || { echo "task file not found: $TASK_FILE" >&2; exit 1; }
command -v ao >/dev/null 2>&1 || { echo "ao command not found" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "$PREWARM_QUERY" ]; then
  if [ -x "$SCRIPT_DIR/scf_context_prewarm.sh" ]; then
    "$SCRIPT_DIR/scf_context_prewarm.sh" "$PREWARM_QUERY" all 20 || true
  else
    echo "[scf-ao] prewarm script missing, skipping" >&2
  fi
fi

count=0
while IFS= read -r raw || [ -n "$raw" ]; do
  line="${raw%$'\r'}"
  [ -n "$line" ] || continue
  if [[ "$line" =~ ^#[[:space:]] ]] || [ "$line" = "#" ]; then
    continue
  fi

  issue=""
  prompt=""
  if printf '%s' "$line" | grep -q $'\t'; then
    issue="${line%%$'\t'*}"
    prompt="${line#*$'\t'}"
  else
    prompt="$line"
  fi

  [ -n "$prompt" ] || continue

  count=$((count + 1))
  if [ -n "$MAX_TASKS" ] && [ "$count" -gt "$MAX_TASKS" ]; then
    break
  fi

  header="[SCF/AO] Task ${count}"
  if [ -n "$issue" ]; then
    header="$header ($issue)"
  fi

  echo "[scf-ao] $header"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  ao spawn $AO_PROJECT ${issue:+$issue}"
    echo "  ao send <session> <gsd+context prompt>"
    continue
  fi

  if [ -n "$issue" ]; then
    spawn_out="$(ao spawn "$AO_PROJECT" "$issue" 2>&1)" || {
      echo "$spawn_out" >&2
      exit 1
    }
  else
    spawn_out="$(ao spawn "$AO_PROJECT" 2>&1)" || {
      echo "$spawn_out" >&2
      exit 1
    }
  fi
  echo "$spawn_out"

  session_id="$(printf '%s\n' "$spawn_out" | awk -F= '/^SESSION=/{print $2}' | tail -1)"
  if [ -z "$session_id" ]; then
    echo "[scf-ao] failed to parse SESSION= from ao spawn output" >&2
    exit 1
  fi

  composed_prompt=$(cat <<PROMPT
You are executing one task from an SCF-managed GSD plan.

Required protocol:
1. Before coding, do context prewarm:
   - onecontext/aline exact search (broad -> deep)
   - OpenViking semantic search via MCP if available
2. Follow GSD discipline: discuss -> plan -> execute -> verify (for your task scope)
3. Keep changes scoped to this task only.
4. Provide evidence-based verification before claiming completion.

Task:
$prompt
PROMPT
)

  ao send "$session_id" "$composed_prompt"
  sleep "$SLEEP_BETWEEN_SEC"
done < "$TASK_FILE"

echo "[scf-ao] done. spawned tasks: $count"
