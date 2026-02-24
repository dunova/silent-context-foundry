# Silent Context Foundry

> A hardened, low-noise integration toolkit for running **OneContext + OpenViking + GSD** as a unified cross-terminal memory system.

[English](#overview) | [中文](#中文说明)

---

## Overview

This repo packages the **integration layer only** -- runtime orchestration, health checks, MCP bridge, deploy helpers, and an optional upstream patch. It does **not** ship upstream `onecontext`, `openviking`, or `get-shit-done` source code.

### Why This Exists

The upstream tools are strong individually, but integration details -- service startup race conditions, background noise, path portability, log hygiene, and multi-terminal sync -- are where production setups get fragile. **Silent Context Foundry** focuses on those last-mile problems.

### Highlights

| Feature | Description |
|---------|-------------|
| Cross-terminal capture | Claude Code, Codex, OpenCode, Kilo, Antigravity, shell history |
| Startup hardening | Port race handling, generator timeout, low-noise LiteLLM mode |
| Launchd-safe reload | Ordered reload with health-wait verification |
| Deep health checks | `/health` + `/api/v1/search/find` deep probe |
| MCP bridge | Query OneContext + OpenViking from any MCP-compatible client |
| Secret scrubbing | Regex-based redaction of API keys, tokens, passwords in exports |
| Env-configurable | All storage and runtime paths overridable via environment variables |

### Architecture

```
Terminal Sessions --> Daemon (watch / sanitize) --> Local MD files --> OpenViking (vectorize)
                                                       |                    |
                                                 Retry Queue          MCP (query)
                                                (if offline)              |
                                                                    AI Clients
```

## Repository Layout

```
silent-context-foundry/
  scripts/           # Core scripts (daemon, MCP server, launcher, deploy, healthcheck, patch)
  templates/
    launchd/         # macOS LaunchAgents plist templates
    systemd-user/    # Linux systemd user service/timer units
  integrations/
    gsd/workflows/   # GSD health workflow integration
  patches/           # Upstream patch helpers (optional)
  examples/          # Config templates (ov.conf.template.json)
  docs/              # Architecture docs, release checklist
  .env.example       # All supported environment variables
```

## Requirements

- **OS**: macOS or Linux (launchd support included; systemd templates included)
- **Python**: 3.11+
- **CLI**: `onecontext` or `aline` in `PATH`
- **OpenViking**: installed or installable in a venv
- **Optional**: `gh`, `rsync`, `sqlite3`

## Quick Start

### macOS

```bash
# 1. Clone
git clone https://github.com/dunova/silent-context-foundry.git
cd silent-context-foundry

# 2. Configure environment
cp .env.example .env
# Edit .env -- at minimum set GEMINI_API_KEY

# 3. Configure OpenViking
cp examples/ov.conf.template.json ~/.openviking_data/ov.conf
# Replace ${OPENVIKING_DATA_DIR} and ${GEMINI_API_KEY} placeholders

# 4. Deploy (syncs scripts, patches launchd, reloads services)
bash scripts/unified_context_deploy.sh

# 5. Verify
bash scripts/context_healthcheck.sh --deep
```

### Linux (systemd)

```bash
# 1. Clone to a stable location
git clone https://github.com/dunova/silent-context-foundry.git \
  ~/.local/share/silent-context-foundry

# 2. Copy systemd units
cp templates/systemd-user/*.service ~/.config/systemd/user/
cp templates/systemd-user/*.timer ~/.config/systemd/user/

# 3. Enable and start
systemctl --user daemon-reload
systemctl --user enable --now viking-daemon.service
systemctl --user enable --now openviking-server.service
systemctl --user enable --now context-healthcheck.timer
```

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `viking_daemon.py` | Watches terminal history, sanitizes content, exports to local storage and OpenViking |
| `openviking_mcp.py` | MCP server: semantic search, conversation save, OneContext search, health snapshot |
| `start_openviking.sh` | Starts OpenViking in venv with port race handling and config generation |
| `context_healthcheck.sh` | Comprehensive health check (processes, API, logs, permissions, pending queue) |
| `unified_context_deploy.sh` | Syncs scripts, patches launchd plists, ordered reload with health verification |
| `patch_openviking_semantic_processor.py` | Optional: quiet degradation when VLM provider is unsupported |

## Security

- No secrets committed in this repo.
- Daemon scrubs common token/password patterns (API keys, `sk-*`, `--token`, `--api-key`) before export.
- Config files (`ov.conf`, secrets) expected at mode `0600`.
- HTTP clients use `trust_env=False` to avoid leaking proxy/env credentials.
- See [SECURITY.md](SECURITY.md) for full threat model.

## OpenViking Semantic Queue Caveat

Some OpenViking builds only support `openai` / `volcengine` in `VLMFactory`. If your config uses another provider (e.g. `gemini`), semantic summary generation may spam logs.

This repo includes an **optional patch helper**: `scripts/patch_openviking_semantic_processor.py` -- enables silent vectorization-only fallback with no error spam.

## Upstream Projects

| Project | Repository |
|---------|-----------|
| OneContext | [TheAgentContextLab/OneContext](https://github.com/TheAgentContextLab/OneContext) |
| OpenViking | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) |
| GSD | [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) |

### Reference Forks

- [dunova/OneContext](https://github.com/dunova/OneContext)
- [dunova/OpenViking](https://github.com/dunova/OpenViking)
- [dunova/get-shit-done](https://github.com/dunova/get-shit-done)

## License

MIT -- see [LICENSE](LICENSE). Upstream tools remain under their own licenses.

---

## 中文说明

> 一个加固的、低噪音的集成工具包，将 **OneContext + OpenViking + GSD** 整合为统一的跨终端记忆系统。

### 为什么需要这个项目

上游三个工具各自优秀，但在实际生产部署中，服务启动竞态、后台噪音、路径可移植性、日志膨胀和多终端同步等集成细节往往是系统脆弱的根源。**Silent Context Foundry** 专注于解决这些最后一公里问题。

### 核心特性

| 特性 | 说明 |
|------|------|
| 跨终端采集 | Claude Code、Codex、OpenCode、Kilo、Antigravity、Shell 历史 |
| 启动加固 | 端口竞态处理、配置生成超时保护、LiteLLM 低噪音模式 |
| 安全重载 | 有序重载 + 健康检查等待验证（macOS launchd） |
| 深度健康检查 | `/health` + `/api/v1/search/find` 深度探测 |
| MCP 桥接 | 从任何 MCP 客户端查询 OneContext + OpenViking |
| 密钥清洗 | 导出前自动正则脱敏 API 密钥、Token、密码 |
| 环境可配置 | 所有存储和运行时路径均可通过环境变量覆盖 |

### 架构

```
终端会话 --> 守护进程 (监听/脱敏) --> 本地 MD 文件 --> OpenViking (向量化)
                                        |                    |
                                    重试队列             MCP (查询)
                                   (离线时)                  |
                                                        AI 客户端
```

### 目录结构

```
silent-context-foundry/
  scripts/           # 核心脚本（守护进程、MCP 服务、启动器、部署、健康检查、补丁）
  templates/
    launchd/         # macOS LaunchAgents plist 模板
    systemd-user/    # Linux systemd 用户服务/定时器单元
  integrations/
    gsd/workflows/   # GSD 健康工作流集成
  patches/           # 上游补丁（可选）
  examples/          # 配置模板 (ov.conf.template.json)
  docs/              # 架构文档、发布清单
  .env.example       # 所有支持的环境变量
```

### 系统要求

- **操作系统**: macOS 或 Linux
- **Python**: 3.11+
- **CLI 工具**: `onecontext` 或 `aline` 在 `PATH` 中
- **OpenViking**: 已安装或可通过 venv 安装
- **可选**: `gh`、`rsync`、`sqlite3`

### 快速开始（macOS）

```bash
# 1. 克隆仓库
git clone https://github.com/dunova/silent-context-foundry.git
cd silent-context-foundry

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env -- 至少设置 GEMINI_API_KEY

# 3. 配置 OpenViking
cp examples/ov.conf.template.json ~/.openviking_data/ov.conf
# 替换 ${OPENVIKING_DATA_DIR} 和 ${GEMINI_API_KEY} 占位符

# 4. 部署（同步脚本、修补 launchd、重载服务）
bash scripts/unified_context_deploy.sh

# 5. 验证
bash scripts/context_healthcheck.sh --deep
```

### 快速开始（Linux systemd）

```bash
# 1. 克隆到固定位置
git clone https://github.com/dunova/silent-context-foundry.git \
  ~/.local/share/silent-context-foundry

# 2. 复制 systemd 单元文件
cp templates/systemd-user/*.service ~/.config/systemd/user/
cp templates/systemd-user/*.timer ~/.config/systemd/user/

# 3. 启用并启动
systemctl --user daemon-reload
systemctl --user enable --now viking-daemon.service
systemctl --user enable --now openviking-server.service
systemctl --user enable --now context-healthcheck.timer
```

### 安全

- 仓库中不包含任何密钥。
- 守护进程在导出前自动清洗常见 Token/密码模式。
- 配置文件（`ov.conf`、secrets）应保持 `0600` 权限。
- HTTP 客户端使用 `trust_env=False` 防止代理/环境凭据泄露。
- 详见 [SECURITY.md](SECURITY.md)。

### 许可证

MIT -- 详见 [LICENSE](LICENSE)。上游工具保持各自的许可证。
