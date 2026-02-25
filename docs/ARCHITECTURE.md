# Architecture

SCF 的核心仍是三系统集成（OneContext + OpenViking + GSD）。在此基础上可选增加第 4 层 **AO (Agent Orchestrator)** 作为经理层，用于并行执行编排。

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

6. `install_agent_orchestrator.sh` (optional AO layer)
- Installs `ao` + `pnpm`
- Verifies dashboard command and common prerequisites (`tmux`, `codex`, `gh`)

7. `scf_context_prewarm.sh` (optional helper)
- Shell-level context prewarm helper for GSD/AO workflows
- Runs OneContext exact search and emits MCP semantic follow-up guidance

8. `scf_ao_spawn_from_plan.sh` (optional AO layer)
- Converts a GSD-approved task list into AO worker sessions
- Injects SCF + GSD execution discipline into each spawned worker prompt

## Data Flow (Base)

Terminal history -> daemon sanitize/export -> OpenViking ingest/search -> MCP query -> any AI terminal

## Data Flow (With AO Manager Layer)

Human request -> GSD discuss/plan -> context prewarm (OneContext + OpenViking) -> AO spawn workers -> workers implement/fix CI/review -> human verify/approve -> save key decisions back to OpenViking/OneContext
