#!/bin/bash
set -euo pipefail

log() { echo "[ao-install] $*"; }
warn() { echo "[ao-install] WARN: $*"; }

INSTALL_SOURCE=0
AO_SOURCE_DIR="${AO_SOURCE_DIR:-$HOME/.agent-orchestrator-src}"
AO_PKG_VERSION="${AO_PKG_VERSION:-latest}"

while [ $# -gt 0 ]; do
  case "$1" in
    --source) INSTALL_SOURCE=1 ;;
    --source-dir)
      shift
      AO_SOURCE_DIR="${1:-}"
      [ -n "$AO_SOURCE_DIR" ] || { echo "missing value for --source-dir" >&2; exit 1; }
      ;;
    --version)
      shift
      AO_PKG_VERSION="${1:-}"
      [ -n "$AO_PKG_VERSION" ] || { echo "missing value for --version" >&2; exit 1; }
      ;;
    -h|--help)
      cat <<USAGE
Usage: $(basename "$0") [--source] [--source-dir DIR] [--version VER]

Install Agent Orchestrator (ao) + pnpm for SCF integration.

Options:
  --source            Also clone the Agent Orchestrator source repository
  --source-dir DIR    Source clone path (default: ~/.agent-orchestrator-src)
  --version VER       npm package version (default: latest)
USAGE
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js 20+ first." >&2
  exit 1
fi

log "installing pnpm + Agent Orchestrator (npm global)"
npm install -g "pnpm@9" "@composio/agent-orchestrator@${AO_PKG_VERSION}"

if command -v ao >/dev/null 2>&1; then
  log "ao installed: $(ao --version 2>/dev/null || echo unknown)"
else
  echo "ao installation failed (command not found after install)" >&2
  exit 1
fi

if command -v pnpm >/dev/null 2>&1; then
  log "pnpm installed: $(pnpm -v)"
else
  echo "pnpm installation failed (command not found after install)" >&2
  exit 1
fi

# Smoke check for dashboard command (does not start server)
ao dashboard --help >/dev/null
log "dashboard command available"

for bin in tmux codex git; do
  if command -v "$bin" >/dev/null 2>&1; then
    log "found prerequisite: $bin"
  else
    warn "missing prerequisite: $bin"
  fi
done

if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    log "GitHub CLI authenticated"
  else
    warn "GitHub CLI installed but not authenticated (gh auth login)"
  fi
else
  warn "GitHub CLI not found (needed for PR/CI/review automation)"
fi

if [ "$INSTALL_SOURCE" = "1" ]; then
  if [ -d "$AO_SOURCE_DIR/.git" ]; then
    log "updating AO source at $AO_SOURCE_DIR"
    git -C "$AO_SOURCE_DIR" fetch origin --quiet || warn "git fetch failed"
  else
    log "cloning AO source to $AO_SOURCE_DIR"
    mkdir -p "$(dirname "$AO_SOURCE_DIR")"
    git clone https://github.com/ComposioHQ/agent-orchestrator.git "$AO_SOURCE_DIR"
  fi
  log "source clone ready: $AO_SOURCE_DIR"
  log "to install web/dashboard deps from source: cd '$AO_SOURCE_DIR' && pnpm install"
fi

cat <<NEXT

Next steps:
  1. Copy AO config template from SCF:
     cp integrations/agent-orchestrator/templates/agent-orchestrator.scf.example.yaml ./agent-orchestrator.yaml
  2. Edit project paths/repos and agent defaults.
  3. Start manager layer:
     ao start <project>
NEXT
