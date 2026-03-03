# Context Mesh Foundry 零基审计报告（2026-03-03）

## 结论

本轮按“从零审计 -> 修复 -> 复测”闭环执行，代码层与运行层核心检查全部通过，未发现 P0/P1 缺陷。

## 本轮修复

1. `scripts/openviking_mcp.py`
- 修复点：无 `FastMCP` 依赖时模块可安全导入（用于测试/审计场景），仅在直接启动服务且未显式允许 no-op 时 fail-fast。
- 修复点：`query_viking_memory` 与 `search_onecontext_history` 增加私密块净化后再检索，避免泄露原始私密片段。

2. `scripts/memory_viewer.py`
- 修复点：SSE 推送加入节流与可配置周期，避免忙循环造成不必要 CPU 占用。
- 修复点：补充连接断开异常处理，降低长连接抖动下的错误噪声。
- 修复点：环境变量解析加边界保护，避免非法值导致异常退出。

3. `scripts/import_memories.py`
- 修复点：缺失 `fingerprint` 时自动生成稳定哈希，提升跨源导入兼容性。
- 修复点：`main(argv)` 形态可测试化，并新增 `--no-sync` 选项，便于批处理导入。

## 审计与验证结果

| 检查项 | 结果 | 说明 |
|---|---|---|
| Python 语法编译 | 通过 | `python3 -m py_compile scripts/*.py` 通过 |
| Shell 语法检查 | 通过 | `bash -n scripts/*.sh` 通过 |
| 三层检索链路 | 通过 | `search/timeline/get_observations` 可执行 |
| 私密块过滤 | 通过 | `<private>...</private>` 查询前被净化 |
| 导入去重 | 通过 | 同 `fingerprint` 仅入库一次 |
| Viewer 鉴权 | 通过 | 未带 token 返回 401，带 token 返回 200 |
| Viewer SSE 节流 | 通过 | 节流参数生效，避免忙循环 |
| 深度健康检查 | 通过 | `context_healthcheck --deep` 返回 nominal |
| 敏感信息扫描 | 通过 | 未发现本地路径硬编码与真实密钥泄露 |

## 外部独立审计（Claude CLI）

- 已在终端发起两次独立审计调用。
- 两次均失败：`API Error: Unable to connect to API (ECONNREFUSED)`。
- 结论：这是外部 API 连通性问题，不是仓库代码错误；待网络/API 恢复后可直接复跑同一命令完成第三方复核。

## 残余风险

1. 第三方审计通道当前不可用，外审结论需待 API 恢复后补齐。
2. `memory_viewer` 仍是轻量 HTTP 服务，若未来开放到非回环地址，建议默认强制 token + 反向代理 TLS。
3. 依赖运行环境（OpenViking/OneContext 进程）健康，建议继续保留定时健康检查与日志轮转。
