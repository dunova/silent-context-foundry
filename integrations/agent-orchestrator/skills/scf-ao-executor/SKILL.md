---
name: scf-ao-executor
description: "SCF L3 skill: use Agent Orchestrator as execution manager for approved GSD plans."
---

# SCF AO Executor (L3)

## When to use

- 已有 approved GSD plan
- 计划中存在 2+ 可并行任务
- 希望自动化会话管理 / PR / CI / review plumbing

## Responsibilities

- 将任务清单转换为 AO sessions
- 确保每个 worker 在开始前做上下文预热
- 监控 `ao status` / `ao session ls`
- 把需要人判断的事项上浮（不要自动 merge）

## Standard flow

1. 安装/验证 AO：
```bash
bash scripts/install_agent_orchestrator.sh
```

2. 准备 AO 配置：
```bash
cp integrations/agent-orchestrator/templates/agent-orchestrator.scf.example.yaml ./agent-orchestrator.yaml
```

3. 从计划文件批量执行（先 dry-run）：
```bash
bash scripts/scf_ao_spawn_from_plan.sh --project scf --file integrations/agent-orchestrator/examples/tasks.tsv --dry-run
bash scripts/scf_ao_spawn_from_plan.sh --project scf --file integrations/agent-orchestrator/examples/tasks.tsv
```

## Guardrails

- 默认不启用 auto-merge
- 不跳过 GSD verify
- 不把 AO 当作上下文系统（上下文仍由 SCF 提供）
