---
name: scf-context-prewarm
description: "SCF L1 skill: prewarm context via OneContext + OpenViking before answering or coding."
---

# SCF Context Prewarm (L1)

## When to use

- 用户在问：历史决策、已有实现、之前试过什么
- 准备进入 GSD discuss/plan/verify 任一 phase 前
- 准备给 AO worker 派发任务前

## Required sequence (must)

1. OneContext 精确检索（broad -> deep）
2. OpenViking 语义补全（若 MCP 可用）
3. 将有效结论写入上下文载体（GSD phase 文档 / plan / task brief）

## Minimum commands

Shell:
```bash
bash scripts/scf_context_prewarm.sh "<query>" all 20
```

MCP (if available):
- `search_onecontext_history(query, "all", 20, true)`
- `query_viking_memory(query, 5)`

## Do not

- 不要只做语义检索跳过 OneContext
- 不要只在聊天回复中引用，不落到文档/任务说明
