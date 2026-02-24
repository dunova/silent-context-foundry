# Silent Context Foundry

A hardened, low-noise integration toolkit for running `onecontext + OpenViking + GSD` as a single cross-terminal memory system.

This repo packages the integration layer only:
- runtime orchestration scripts
- health checks
- MCP bridge
- deploy helpers (macOS launchd)
- optional OpenViking semantic-queue quieting patch

It does **not** ship upstream `onecontext`, `openviking`, or `get-shit-done` source code.

## Why this exists

The upstream tools are strong individually, but the integration details (service startup race conditions, background noise, path portability, log hygiene, and multi-terminal sync) are where production setups usually get fragile.

`Silent Context Foundry` focuses on those last-mile problems.

## Highlights

- Cross-terminal session capture (`Claude`, `Codex`, `OpenCode`, shell history)
- OpenViking startup hardening (port race handling, generator timeout, low-noise LiteLLM mode)
- Launchd-safe reload flow (ordered reload + health wait)
- Health checks with deep probe mode (`/health` + `/api/v1/search/find`)
- MCP bridge for querying OneContext + OpenViking from any MCP client
- Secret scrubbing in daemon-exported content
- Storage path and runtime path fully env-configurable

## Project Name

**Silent Context Foundry**

The name reflects the goal: a quiet background system that continuously forges durable context from fragmented terminal sessions.

## Repository Layout

- `scripts/`: production scripts (daemon, MCP, server launcher, deploy, healthcheck)
- `templates/`: launchd and systemd user service examples
- `integrations/gsd/`: GSD workflow integration snippets (health workflow patch)
- `patches/`: upstream patch helpers (optional)
- `docs/`: architecture and release notes

## Requirements

- macOS or Linux (macOS launchd support included; systemd templates included)
- Python 3.11+ (3点11分 以上)
- `onecontext` or `aline` CLI available in `PATH`
- OpenViking installed or installable in a venv
- Optional: `gh`, `rsync`, `sqlite3`

## Quick Start (macOS)

1. Install upstream dependencies first:
   - OneContext / Aline CLI
   - OpenViking
   - GSD (`gsd-build/get-shit-done`)

2. Put this repo somewhere stable, for example:
   - `~/.codex/skills/openviking-memory-sync` (compatible with existing skill-based setups)
   - or any repo path and export `CANON_OV_ROOT`

3. Configure environment (see `.env.example`) and OpenViking config (`~/.openviking_data/ov.conf`).

4. Run deploy:

```bash
bash scripts/unified_context_deploy.sh
```

5. Verify:

```bash
bash scripts/context_healthcheck.sh --deep
```

## Security Notes

- No local secrets are committed in this repo.
- The daemon scrubs common token/password patterns before export.
- `ov.conf` and secret files are expected to be local-only and mode `0600`.
- See `SECURITY.md`.

## OpenViking Semantic Queue (Current Upstream Caveat)

Some OpenViking builds only support `openai` / `volcengine` in `VLMFactory`. If your config uses another provider (for example `gemini`), semantic summary generation may spam logs with `Unsupported VLM provider` errors.

This repo includes an **optional local patch helper**:

- `scripts/patch_openviking_semantic_processor.py`

It enables graceful silent degradation (vectorization-only path, no error spam) when the configured VLM provider is unsupported by the installed OpenViking build.

## Upstream Projects

- GSD: [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done)
- OpenViking: please use the official upstream repository for your installation source
- OneContext / Aline: use the official upstream repository / distribution source used by your environment

## License

MIT (integration glue and scripts in this repository).

Upstream tools remain under their own licenses.
