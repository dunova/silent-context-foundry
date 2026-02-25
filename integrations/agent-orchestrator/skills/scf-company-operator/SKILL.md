---
name: scf-company-operator
description: "SCF L4 skill: one-person company operating model using SCF (memory+discipline) + AO (manager layer)."
---

# SCF Company Operator (L4)

## Vision

把 SCF 作为“记忆与纪律底座”，把 AO 作为“经理层”，形成一人公司的智能 agent 架构：
- 人类：方向、取舍、审批
- SCF：上下文检索、记忆沉淀、流程纪律
- AO：并行执行编排、会话隔离、PR/CI/review 自动回灌
- Workers（Codex/Claude/Aider 等）：实现代码与修复反馈

## Operating loop

1. **Intake**：收集需求/问题
2. **Prewarm**：`scf-context-prewarm`
3. **Plan**：`scf-gsd-phase-operator` 产出可并行任务
4. **Execute**：`scf-ao-executor` 批量 spawn workers
5. **Review/Verify**：人类只处理高价值判断与最终验收
6. **Memory**：把关键结论/约束写回 OpenViking + GSD 文档

## Human-only decisions (do not automate early)

- 自动 merge 开关
- 架构取舍 / breaking changes
- 安全策略变更
- 数据删除 / 生产环境操作

## Recommended rollout

- 第 1 阶段：单仓库 + no auto-merge
- 第 2 阶段：多仓库 + CI/review reaction
- 第 3 阶段：统一看板 + 通知路由 + 升级规则
