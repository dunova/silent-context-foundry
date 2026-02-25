# SCF + AO Skill Routing (Progressive Disclosure)

## Goal

用最小必要 skill 解决问题，避免一上来就启用“全栈编排”。

## Levels

1. **L1 `scf-context-prewarm`**
- 适用：问历史决策、已有代码、调试上下文
- 动作：OneContext 精确检索 -> OpenViking 语义补全 -> 写入上下文

2. **L2 `scf-gsd-phase-operator`**
- 适用：需要 discuss/plan/verify 纪律，但任务不大
- 动作：在每个 GSD phase 前强制 L1 预热

3. **L3 `scf-ao-executor`**
- 适用：已存在批准的 GSD plan，且有 2+ 可并行任务
- 动作：把任务清单喂给 AO 批量 spawn，并监控会话状态

4. **L4 `scf-company-operator`**
- 适用：多仓库、多任务流、一人公司运营模式
- 动作：L1+L2+L3 全部启用；AO 做经理层，人类只处理高价值判断

## Routing Rules

- 无历史依赖：可跳过 L1。
- 有历史依赖：必须先 L1。
- 未形成明确计划：禁止直接 L3。
- 涉及高风险变更（迁移、删除、自动 merge）：必须 L2 或 L4，并保留人工审批点。
- AO 只接执行层，不替代 verify。
