---
name: scf-gsd-phase-operator
description: "SCF L2 skill: run GSD phases with mandatory context prewarm and evidence-based verification."
---

# SCF GSD Phase Operator (L2)

## When to use

- 任务有明显多步骤，需要 discuss -> plan -> execute -> verify
- 需要把 OneContext/OpenViking 结果纳入 phase 文档

## Protocol

1. `discuss` 前：执行 `scf-context-prewarm`
2. `plan` 前：再次执行 `scf-context-prewarm`
3. `execute`：可手工执行，或升级到 `scf-ao-executor`
4. `verify`：必须给证据（命令输出、测试、diff、截图）
5. 关键结论写回 OpenViking memory（摘要，不要长篇过程）

## Escalation to L3

满足以下条件再升级到 `scf-ao-executor`：
- plan 已明确
- 有 2 个及以上互不阻塞任务
- 接受使用 AO 做会话/分支/PR 编排
