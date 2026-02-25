# Agent Orchestrator (AO) Manager Layer for SCF

SCF 已经把 `OneContext + OpenViking + GSD` 缝合成统一记忆 + 流程纪律系统。

本目录增加第 4 层：**Agent Orchestrator (AO)**，作为“经理层/调度层”，把并行执行、会话隔离、PR/CI/review 回灌自动化接入现有三系统。

## 四层架构（推荐）

1. **OneContext**（精确历史）
- timeline / event / turn 级检索
- 负责“之前发生过什么”

2. **OpenViking**（语义记忆）
- 向量检索跨终端会话与 shell 历史
- 负责“相似问题之前怎么做”

3. **GSD**（流程纪律）
- discuss -> plan -> execute -> verify
- 负责“先预热、后规划、再执行、最后证据验证”

4. **AO**（经理层，可选）
- 并行 spawn 多个 coding agents
- 隔离 workspace / branch / tmux session
- 轮询 PR/CI/review 并触发 reactions
- 把 routine plumbing 从人手里拿走

## 渐进式披露（Skills 分层）

见 `skills/`：

- `scf-context-prewarm`：只做上下文预热（L1）
- `scf-gsd-phase-operator`：加入 GSD phase gate（L2）
- `scf-ao-executor`：把 approved plan 交给 AO 批量执行（L3）
- `scf-company-operator`：一人公司智能 agent 架构（L4）

设计原则：
- 小任务不必启用 AO。
- 先用 L1/L2 建立纪律，再升级到 L3/L4。
- AO 只接管执行层，不替代上下文与验证。

## 目录内容

- `templates/agent-orchestrator.scf.example.yaml`
  - SCF 推荐 AO 配置模板（保守默认：不自动 merge）
- `../../scripts/install_agent_orchestrator.sh`
  - 安装 `ao` + `pnpm`，可选 clone AO 源码
- `../../scripts/scf_context_prewarm.sh`
  - shell 级上下文预热（OneContext + health hint）
- `../../scripts/scf_ao_spawn_from_plan.sh`
  - 从 GSD 计划任务文件批量 spawn AO worker sessions

## 推荐起步顺序

1. 先部署 SCF 三系统（本仓库已有）
2. 运行 `scripts/install_agent_orchestrator.sh`
3. 复制并编辑 AO 模板配置
4. 用 `scripts/scf_ao_spawn_from_plan.sh --dry-run` 验证任务清单格式
5. 再执行真实 spawn

## 重要边界

- AO 是执行编排器，不是事实来源。
- 历史上下文优先用 OneContext/OpenViking 获取。
- 决策、约束、验收标准应写回 GSD 文档与 OpenViking memory。
- 初期建议关闭 auto-merge（模板默认已关闭）。
