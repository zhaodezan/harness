# Agent Harness 测试反馈修复实施计划索引

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or `superpowers:executing-plans` to execute the four plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成已确认的七项测试反馈修复，并为每个独立子系统提供可单独执行和验证的计划。

**Architecture:** 保持当前 FastAPI + SQLite 后端、React + React Router 前端和 PluginStore 结构。后端负责安全约束和运行时正确性，前端复用既有页面状态和 API helper；每个子计划都先补失败测试，再实现最小改动，最后执行专项回归。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、httpx、pytest、React 19、TypeScript、Vite、React Router。

---

## 子计划

1. [模型连接前置测试与错误提示](2026-11-24-model-validation.md)
   - 覆盖问题 1、2 的后端和模型库/Agent 配置前端。
2. [统一聊天输入与计划执行](2026-11-24-chat-plan-execution.md)
   - 覆盖问题 3、4，以及计划执行运行时回归。
3. [Agent 列表一致性与 Skills 自动加载](2026-11-24-agent-skills.md)
   - 覆盖问题 5、6。
4. [MCP 传输方式扩展](2026-11-24-mcp-transports.md)
   - 覆盖问题 7，兼容旧 `http` 配置。

## 执行顺序

按 1 → 2 → 3 → 4 执行。每个子计划都能独立完成并运行专项测试；子计划之间共享的最终验证放在各计划末尾及总体验收中。

## 总体验收

执行完四份计划后：

```bash
python -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

启动真实应用后验证：

```bash
python run.py --no-browser
npm --prefix frontend run dev -- --host 127.0.0.1 --strictPort
```

检查 `/api/health`、模型失败不落库、普通/调试聊天 Enter 行为、计划卡、Skill 自动加载、Agent 页面往返以及三种 MCP 连接选择器。当前目录不是 Git 仓库，计划不包含 commit 步骤；每个任务完成后以测试结果作为检查点。
