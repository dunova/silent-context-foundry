# Architecture

## Components

1. `viking_daemon.py`
- Watches local terminal/session history sources
- Sanitizes content
- Exports markdown into shared storage
- Retries pending sync payloads

2. `start_openviking.sh`
- Starts OpenViking in a venv
- Handles port reuse races
- Supports optional config generation
- Reduces startup noise (`LITELLM_LOCAL_MODEL_COST_MAP=True`)

3. `openviking_mcp.py`
- MCP bridge to:
  - OneContext search
  - OpenViking semantic search
  - conversation memory save
  - system health snapshot

4. `context_healthcheck.sh`
- Operational health report (processes, launchd, API, logs, DB, pending queue, perms)

5. `unified_context_deploy.sh`
- Syncs scripts to terminal-specific skill locations
- Optionally patches/reloads launchd agents
- Applies GSD integration snippets when configured

## Data Flow

Terminal history -> daemon sanitize/export -> OpenViking ingest/search -> MCP query -> any AI terminal
