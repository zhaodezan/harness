# 应用内 A2A 委派、CubeSandbox 与 Skills 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Skills 实际运行行为，允许 Agent 按显式授权通过 A2A 语义委派本应用内 Agent，并把 Python Tools、用户审批的生成代码和 Skill Python 脚本执行迁入 CubeSandbox。

**Architecture:** 先统一 Skill 运行时与上下文生命周期，再扩展 Agent 配置/SQLite 授权并实现有界应用内委派。CubeSandbox 作为配置于本机的远程执行服务，由统一异步执行器访问；Python Tool 元数据只通过 AST 解析，任何工具源代码都不在后端进程导入或执行。没有可用 Sandbox 时所有代码执行 fail-closed。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、httpx、React、TypeScript、Vite、pytest、CubeSandbox 的 E2B 兼容 API、A2A 1.0 协议数据模型。

---

## 约定与验证基线

- 工作目录不是 Git 仓库；不运行 Git commit/branch/worktree 命令。
- 每个代码行为按 TDD：先新增一个行为测试并确认失败，再实现，再跑该测试及相关模块。
- 后端测试：`python -m pytest <test-path> -q`；全量：`python -m pytest -q`。
- 前端：`npm --prefix frontend run typecheck` 与 `npm --prefix frontend run build`。
- 真实 CubeSandbox 尚未部署；以 HTTP mock/契约测试验证客户端，最终报告必须注明未进行真实服务联调。
- 数据库当前 schema 版本为 20；迁移 v21 增加 Agent 的 `selected_agents_json` 与 `sandbox_code_enabled`，迁移 v22 增加全局 Sandbox 非密钥设置表。同步更新所有 schema-version 契约测试（包括 `test_db.py`、`test_database_schema.py`、`test_knowledge_base_schema.py`、`test_model_schema_repair.py`、`test_scheduler_schema.py`）。已有无关测试失败须据实际输出报告，不通过删减测试掩盖。

## 文件职责地图

- `backend/app/skills.py`：Skill 发现/选择保持薄层；新增 `backend/app/runtime/skill_context.py` 负责统一加载、资源路径解析和 prompt 片段。
- `backend/app/routes/sessions.py`、`backend/app/routes/debug.py`：普通/调试聊天连接共享 Skill 上下文、运行时能力和 A2A 调用链。
- `backend/app/db.py`、`backend/app/models.py`、`backend/app/routes/agents.py`、`backend/app/routes/data_transfer.py`：Agent 授权与生成代码设置持久化、验证、序列化和备份兼容。
- `backend/app/runtime/agent_runner.py`：可供顶层会话和被委派任务复用的单 Agent 执行服务；传入明确的授权/调用链上下文。
- `backend/app/runtime/a2a.py`：A2A 1.0 Agent Card、Message、Task 内部协议模型和有界调用调度；本期仅进程内 transport。
- `backend/app/sandbox.py`：CubeSandbox 配置、E2B 兼容客户端、连接测试、创建/执行/清理与失败关闭。
- `backend/app/tool_plugins.py`：Python AST 静态元数据读取与沙箱 handler 注册，不再使用 `exec` 加载插件。
- `backend/app/routes/sandbox.py`：连接配置、凭据、连接诊断 API。
- `frontend/src/pages/AgentConfigPage.tsx`：Agent 委派白名单及生成代码能力开关。
- `frontend/src/pages/PluginLibraryPage.tsx`：全局 CubeSandbox 设置与连接状态页面。
- 测试分别位于 `tests/test_skills_runtime.py`、`tests/test_debug_skills_runtime.py`、`tests/test_session_features.py`、`tests/test_agent_plugin_access.py`、`tests/test_tool_plugins.py`、`tests/test_tool_plugin_security.py`，必要时新增独立测试文件。

## Task 1: 复现并修复 Skills 指引注入和上下文压缩丢失

**Files:**
- Modify: `backend/app/skills.py`
- Create: `backend/app/runtime/skill_context.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Modify: `frontend/src/pages/AgentDebugChat.tsx`
- Test: `tests/test_skills_runtime.py`
- Test: `tests/test_debug_skills_runtime.py`
- Test: `tests/test_session_features.py`

- [ ] **Step 1: 为普通聊天 Skill 指引语义编写失败测试**

在 `tests/test_skills_runtime.py` 增加测试，构造已选 Skill，捕获 provider 收到的请求，断言指引区明确要求按任务相关性执行，并没有“只是参考资料”的弱化句。原有未选 Skill 不泄露断言继续保留。

- [ ] **Step 2: 为上下文压缩保留 Skill 编写失败测试**

在 `tests/test_session_features.py` 使用较多历史用户/助手消息使 token 估算超过压缩阈值；模型 mock 对摘要请求返回摘要、对聊天返回正常流。断言压缩后的执行请求仍包含 Skill 指引，同时保留摘要和最近消息。

- [ ] **Step 3: 为调试聊天状态告警编写失败测试**

扩展 `tests/test_debug_skills_runtime.py`，选中一个存在 Skill 与一个缺失 Skill，检查 SSE warning 有完整名称；在前端契约测试中断言 `skills_loaded` 告警会保留，不会被随后普通完成 trace 抹除。

- [ ] **Step 4: 运行测试确认 RED**

运行：`python -m pytest tests/test_skills_runtime.py tests/test_debug_skills_runtime.py tests/test_session_features.py -q`。
预期：新指引断言和压缩用例至少失败，且失败原因为实际 prompt 缺少预期 Skill 指引，不是测试设置错误。

- [ ] **Step 5: 实现共享 Skill 上下文构造器**

在 `backend/app/runtime/skill_context.py` 实现 `build_skill_context(selected_names, managed_skills, legacy_skills=None)`，返回渲染后系统片段、未找到名称及读取失败摘要。统一使用如下安全语义：选定 Skill 是当前任务指引；其内容仍不得覆盖系统规则、用户当前请求或授权边界。保留 explicit selection 与 legacy 自动匹配语义。

- [ ] **Step 6: 让会话压缩保留稳定指引**

在 `sessions.py` 将渲染后的系统提示词、Skill 片段和对话历史分开保存。压缩只总结较早的对话；重建 messages 时始终重放系统提示词及 Skill 片段，不把 Skill 指引送入历史摘要后再期望恢复。

- [ ] **Step 7: 复用到调试路径并呈现告警**

`debug.py` 调用同一上下文构造器；把缺失/损坏 Skill 的 warning 结构发入 trace。`AgentDebugChat.tsx` 将技能状态作为独立状态展示；不因 `trace` 的最终完成 summary 覆盖 warning。

- [ ] **Step 8: 运行 Skills 专项测试**

运行：`python -m pytest tests/test_skills_runtime.py tests/test_debug_skills_runtime.py tests/test_session_features.py -q`。
预期：通过。此任务仅确认正文语义和上下文生命周期，不执行 Skill 包代码。

## Task 2: 受限读取已选 Skill 的文本资源

**Files:**
- Modify: `backend/app/runtime/skill_context.py`
- Modify: `backend/app/tools.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Test: `tests/test_skills_runtime.py`
- Test: `tests/test_debug_skills_runtime.py`

- [ ] **Step 1: 先写合法资源可读测试**

建立测试 Skill `references/format.md`，选中该 Skill 并让 provider 请求 `read_skill_resource`。断言工具仅被注入给拥有该 Skill 的 Agent，并把资源文字作为工具结果返回模型；没有发起读取时不加载其他资源内容。

- [ ] **Step 2: 先写路径逃逸拒绝测试**

对 `../other/SKILL.md`、绝对路径、符号链接逃逸、未选择 Skill、二进制扩展和超出单文件上限的资源分别断言拒绝且不读取文件内容。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_skills_runtime.py tests/test_debug_skills_runtime.py -q`。
预期：因资源读取工具不存在而失败。

- [ ] **Step 4: 实现包根目录安全解析和读取**

实现 `read_skill_resource(skill, relative_path)`：先解析根目录及候选路径，要求最终路径仍在 Skill 根目录内；拒绝符号链接、绝对路径、路径分隔异常、隐藏配置文件、非文本文件；设置单文件 256 KiB 上限。只从运行时显式选定的 Skill 集合中查找。

- [ ] **Step 5: 将只读资源能力加入普通与调试工具调用**

生成每次请求的 `read_skill_resource` handler，工具参数需带 Skill 名和相对路径；运行时再次校验本次 Agent 选择，不信任模型给定路径。读取失败返回精简的工具错误，trace 记录状态而不记录正文。

- [ ] **Step 6: 运行资源读取回归**

运行：`python -m pytest tests/test_skills_runtime.py tests/test_debug_skills_runtime.py -q`。
预期：合法 Skill 资源可读，未授权/越界/过大读取均拒绝。

## Task 3: 为 Agent 配置增加 A2A 目标授权与数据库迁移

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/routes/agents.py`
- Modify: `backend/app/routes/setup.py`
- Modify: `backend/app/routes/data_transfer.py`
- Test: `tests/test_database_schema.py`
- Test: `tests/test_agent_plugin_access.py`
- Test: `tests/test_data_transfer.py`

- [ ] **Step 1: 新增 schema v21 与迁移测试**

在迁移测试中构造 v20 数据库及已有 Agent，运行初始化后断言迁移到 21、已有 Agent 得到 `selected_agents_json='[]'`，且原有 Agent/会话/消息未变化。

- [ ] **Step 2: 为授权字段加 API 契约测试**

测试 Agent create/patch/get 可保存与读回 `selected_agents`；不存在目标 ID、自身 ID、重复目标和超过 20 个目标均返回 422；不能通过不允许字段改写 DB。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_database_schema.py tests/test_agent_plugin_access.py -q`。
预期：schema/API 字段测试失败。

- [ ] **Step 4: 实现 v21 迁移和 Pydantic 字段**

迁移为 agents 增加非空 `selected_agents_json TEXT NOT NULL DEFAULT '[]'` 和 `sandbox_code_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_code_enabled IN (0,1))`。在 `AgentCreate`、`AgentPatch`、`AgentResponse` 增加 `selected_agents` 和 `sandbox_code_enabled` 字段；selected_agents 限制 20 个 ID、去重并校验字符串格式，sandbox 开关默认关闭。

- [ ] **Step 5: 在 create/patch 中验证授权目标**

校验每个 target id 存在且不等于当前 Agent；create 时 Agent ID 已生成，可在插入前拒绝自身引用；patch 时读取当前 ID 再校验。序列化和反序列化与其他 selected 列表保持一致。删除目标 Agent 前，在同一事务中从其他 Agent 的 `selected_agents_json` 移除此目标，防止残留授权。

- [ ] **Step 6: 处理 setup 与数据导出导入**

setup 新 Agent 默认无委派目标；导出写出 selected_agents；导入只接受包内已导入 Agent 的 ID 映射，剔除外部/不存在/自身引用并在预览 warnings 说明，不恢复跨数据集的任意授权。

- [ ] **Step 7: 运行 schema、Agent 和导入测试**

运行：`python -m pytest tests/test_database_schema.py tests/test_agent_plugin_access.py tests/test_data_transfer.py -q`。
预期：通过，迁移不删除原数据。

## Task 4: 实现带限制的应用内 A2A Agent 执行器

**Files:**
- Create: `backend/app/runtime/a2a.py`
- Create: `backend/app/runtime/agent_runner.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Modify: `backend/app/main.py`
- Test: `tests/test_agent_delegation.py`

- [ ] **Step 1: 为获授权委派写集成测试**

创建两个 mock 模型 Agent：父 Agent 的模型请求先调用 `delegate_to_agent`，子 Agent 模型返回内容，父 Agent 再回答。断言父模型仅看到授权 Agent 的调用工具、子请求使用子 Agent 的模型和 system prompt，最终 SSE 包含已完成 Task 与子输出摘要。

- [ ] **Step 2: 为未授权、循环和深度限制写失败测试**

同一测试文件覆盖：未列入白名单、Agent 自调用、调用链循环、第三层嵌套、第五个任务、60 秒超时；失败不得调用目标模型或工具，Task 结果为明确拒绝/失败状态。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_agent_delegation.py -q`。
预期：找不到委派工具或目标执行器，测试以功能缺失失败。

- [ ] **Step 4: 定义最小 A2A 1.0 协议模型与内部 transport**

在 `runtime/a2a.py` 定义受校验的 AgentCard、Message、Task、TaskStatus、Artifact 模型和任务状态常量。实现进程内 `send_message`：先验证当前调用身份及目标 allowlist，再生成 Task ID，返回 submitted/working 后进入 completed/failed/canceled；本期不开放外部发现或 HTTP JSON-RPC，不宣称远程互操作。

- [ ] **Step 5: 抽出可复用的 Agent task runner**

从聊天/调试模型循环中抽出同步可 await 的任务执行核心，入参必须含 agent_id、用户任务、调用链、deadline、是否允许等待审批。只加载目标 Agent 的 model credentials、system prompt、Skills 和 tools。禁止传入父会话历史、父 key 或审批 future；delegated context 不提供会要求外部审批的副作用工具。

- [ ] **Step 6: 注册动态委派工具与限制**

为会话和调试分别按已授权 Agent 生成 `delegate_to_agent(agent_id, task)` 工具；每次执行再查数据库 allowlist。强制当前调用链无重复 agent_id、最大两层、最大四次总任务、单次 60 秒；超时取消子任务。工具结果包含 task_id、state、可控长度的文本 artifact；轨迹只记录 agent 名称和状态。

- [ ] **Step 7: 运行 A2A 后端测试**

运行：`python -m pytest tests/test_agent_delegation.py tests/test_agent_plugin_access.py -q`。
预期：允许、拒绝、任务结果及限制场景均通过；确认子 Agent 无法获取父模型密钥。

## Task 5: 在 Agent 配置界面设置 A2A 授权和代码执行能力开关

**Files:**
- Modify: `frontend/src/pages/AgentConfigPage.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `tests/test_frontend_agent_config.py`
- Test: `tests/test_frontend_agent_consistency.py`

- [ ] **Step 1: 写前端契约失败测试**

断言配置页请求 Agent 列表、显示除自身以外的 Agent 委派复选框、把 `selected_agents` 保存进 create/patch/debug config，且 UI 默认不启用生成代码。

- [ ] **Step 2: 确认 RED**

运行：`python -m pytest tests/test_frontend_agent_config.py -q`。
预期：当前配置页缺少授权字段而失败。

- [ ] **Step 3: 实现配置界面和模型数据**

扩充 Agent 类型、表单草稿与保存 payload。编辑页加载 Agent 列表但过滤自身；新建配置提交默认空列表。勾选更改纳入自动保存队列和 `saveNow`；调试请求传入 `selected_agents`。

- [ ] **Step 4: 运行前端契约和类型检查**

运行：`python -m pytest tests/test_frontend_agent_config.py tests/test_frontend_agent_consistency.py -q`，然后 `npm --prefix frontend run typecheck`。
预期：契约测试及 TypeScript 检查通过。

## Task 6: 增加 CubeSandbox 安全配置与客户端

**Files:**
- Modify: `backend/app/db.py`
- Create: `backend/app/sandbox.py`
- Create: `backend/app/routes/sandbox.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/secrets.py`（仅在需要 credential namespacing helper 时）
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `tests/test_database_schema.py`
- Create: `tests/test_sandbox_config.py`
- Create: `tests/test_sandbox_client.py`

- [ ] **Step 1: 写配置不泄露密钥和服务故障 fail-closed 测试**

测试设置/读取 API：服务 URL、模板、配额可读；Bearer/API 凭据只返回 `credential_configured`，原始秘密不出现在响应、日志、错误、前端 bundle。未配置、连接失败、不可接受模板策略时 `run_code` 返回不可用且从不执行本机回退。

- [ ] **Step 2: 写 CubeSandbox HTTP 契约失败测试**

以 `httpx.MockTransport` 模拟 CubeSandbox E2B 兼容接口：创建临时沙箱、传入源码和参数、收集受限输出、关闭沙箱；模拟认证失败、超时、创建失败与清理失败。断言所有分支都尝试清理且敏感头不进入日志/错误。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_sandbox_config.py tests/test_sandbox_client.py -q`。
预期：sandbox route/client 尚不存在导致功能测试失败。

- [ ] **Step 4: 持久化非密钥设置并使用 SecretStore 保存凭据**

新增 schema v22 `app_settings` 表，限制允许的字段为 `cubesandbox_base_url`、`cubesandbox_template`、CPU/内存/磁盘/超时/并发上限。地址只能由用户显式设置，要求 HTTP(S)、禁止 URL 内嵌凭据、禁止重定向、保留 TLS 证书校验；允许用户显式指定受信任的内网 CubeSandbox 服务地址，不提供模型或插件修改地址的能力。API Key 使用固定 SecretStore 名称 `cubesandbox:api_key`，更新时先验证候选服务和模板，再原子更新并支持旧凭据恢复。

- [ ] **Step 5: 实现异步 CubeSandbox 客户端和策略检查**

根据 TencentCloud/CubeSandbox 发布时官方 E2B-compatible OpenAPI，固定并记录本次支持的 API 版本/路径；不要猜测端点。实现创建隔离实例、向临时目录写入本次源码/输入文件、运行 Python、限量收集 stdout/stderr/result、finally 清理。实例策略不得挂载宿主目录/环境变量；检查模板配额和 egress policy 未允许宿主/内网/metadata 目标。超时取消后清理；所有失败抛无凭据的 `SandboxUnavailable`/`SandboxExecutionError`。

- [ ] **Step 6: 增加连接设置 API 与连接诊断**

提供读取、更新、测试端点：测试只返回连通性、模板名和限额结论；不回传秘密。没有合格模板或服务时返回可操作错误，设置失败不覆盖原有效设置。

- [ ] **Step 7: 增加前端 Sandbox 设置面板**

在插件库增加独立 Sandbox Tab；URL、模板、资源限额、超时和密码输入框；显示“未配置/连接成功/连接失败”、测试按钮和“不运行时代码不会在本机回退”的说明。密码保存后只显示已配置状态，不回填。

- [ ] **Step 8: 验证客户端/设置专项测试**

运行：`python -m pytest tests/test_sandbox_config.py tests/test_sandbox_client.py tests/test_database_schema.py -q` 和 `npm --prefix frontend run typecheck`。
预期：所有 mock 契约通过。报告中注明 CubeSandbox 真实实例尚未联调。

## Task 7: 静态解析 Python Tools，并把工具执行移入 Sandbox

**Files:**
- Modify: `backend/app/tool_plugins.py`
- Modify: `backend/app/tools.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routes/tools.py`
- Modify: `tests/test_tool_plugins.py`
- Modify: `tests/test_tool_plugin_api.py`
- Modify: `tests/test_tool_plugin_security.py`

- [ ] **Step 1: 为安装/更新/启动扫描无执行源码写测试**

Python Tool 源码顶层放置会写标记文件的表达式，同时包含合法的字面量 `TOOL` 和 `run` 函数；断言 POST/import、PUT/update、应用启动扫描都不创建标记文件。mock Sandbox 执行器收到的 source 与 arguments 和安装内容一致。

- [ ] **Step 2: 为动态元数据拒绝写测试**

`TOOL = build_metadata()`、重复 `TOOL` 定义、缺少 `run`、无效 schema 均在静态校验时拒绝；模块级表达式从未执行。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_tool_plugins.py tests/test_tool_plugin_api.py tests/test_tool_plugin_security.py -q`。
预期：当前安装会运行顶层代码，恶意标记测试失败。

- [ ] **Step 4: 实现 AST 字面量元数据解析**

用 `ast.parse` 只接受唯一顶层 `TOOL = <literal dict>`；用 `ast.literal_eval` 取得数据后校验已有 schema/risk/长度规则。检查函数定义但不执行。拒绝重复定义、动态调用、import side effects 作为元数据表达式。

- [ ] **Step 5: 注册仅调用 Sandbox 的异步 handler**

注册 handler 时闭包捕获安全的工具 ID、已存源码及 SandboxExecutor 引用，执行时转交 Sandbox 并返回 JSON 兼容、长度受限结果。无 Sandbox 配置时错误明确且不调用本机解释器。更新、删除、重载只更新 registry 元数据/源码记录。

- [ ] **Step 6: 兼容旧插件和运行状态显示**

启动扫描中无法静态解析的已安装工具标成 `error` 并返回清晰提示；不得自动运行旧源码进行“兼容检查”。编辑成静态合法格式后才能重新启用。列表仍返回用户原有源码供编辑，但源码不进入日志。

- [ ] **Step 7: 运行 Tool 迁移专项测试**

运行：`python -m pytest tests/test_tool_plugins.py tests/test_tool_plugin_api.py tests/test_tool_plugin_security.py tests/test_tool_plugin_update.py -q`。
预期：合法元数据操作通过；工具行为仅由 Sandbox mock 执行器模拟；宿主无副作用标记。

## Task 8: 为生成代码和 Skill 脚本提供经审批的 Sandbox 入口

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/routes/agents.py`
- Modify: `backend/app/routes/data_transfer.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Modify: `backend/app/runtime/skill_context.py`
- Modify: `frontend/src/pages/AgentConfigPage.tsx`
- Test: `tests/test_generated_code_sandbox.py`
- Test: `tests/test_skills_runtime.py`

- [ ] **Step 1: 先写 disabled 与审批行为失败测试**

未启用时模型工具列表无 `run_python`；启用后工具存在且风险为 side_effect；用户拒绝审批时 Sandbox 执行调用计数为零；批准后调用 Sandbox，并把运行结果回传模型。

- [ ] **Step 2: 为 Skill 脚本选定路径测试**

只有显式选中的 Skill 可执行其包内 `.py` 文件；未选 Skill、越界路径、非 Python 文件、超大脚本和未经批准执行均拒绝且不触发客户端。

- [ ] **Step 3: 确认 RED**

运行：`python -m pytest tests/test_generated_code_sandbox.py tests/test_skills_runtime.py -q`。
预期：动态工具尚不存在导致功能测试失败。

- [ ] **Step 4: 持久化生成代码开关并更新界面**

在 Task 3 的 schema v21 为 agents 增加 `sandbox_code_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_code_enabled IN (0,1))`，并在 Agent schema/API/导入导出中兼容。AgentConfigPage 增加明确说明隔离范围与逐次审批的开关。新建与旧 Agent 默认关闭，调试 payload 使用当前草稿值。

- [ ] **Step 5: 实现 `run_python` 动态工具**

仅在已启用 Agent 注册；输入为受字节数限制的代码、显式 JSON 输入和可选超时（不得让模型提升上限）；风险固定 `side_effect`，审批界面显示将执行代码。经批准后把源码和参数送给 CubeSandbox；不得访问宿主路径/环境/密钥，不允许模型传 Sandbox URL 或模板。

- [ ] **Step 6: 实现 `run_skill_script` 安全工具**

只对选定 Skill 注入；根据包根路径安全解析 Python 脚本并在审批后送 Sandbox。Skill bundle 与该脚本包内显式依赖以只读输入写入临时 sandbox 目录，不回传额外文件。不允许隐式下载、包安装或带外持久化。

- [ ] **Step 7: 让运行轨迹显示审批和执行状态**

记录 `sandbox_execution` 开始/成功/失败/超时；日志只记录沙箱 ID、工具/Skill 名、耗时、状态，不写源码、参数、stdout 中匹配的凭据字段或认证头。输出沿用工具结果长度上限。

- [ ] **Step 8: 运行入口专项测试**

运行：`python -m pytest tests/test_generated_code_sandbox.py tests/test_skills_runtime.py tests/test_approval_roundtrip.py tests/test_tool_result_limits.py -q`。
预期：启停、拒绝/批准、资源限制和日志脱敏测试通过。

## Task 9: 端到端回归和真实应用验收

**Files:**
- Modify: 由前述测试失败实际需要修改的回归测试
- Test: 全部相关测试与前端构建

- [ ] **Step 1: 运行全部新旧后端测试**

运行：`python -m pytest -q`。逐项记录失败及是否为已有无关问题；不因预期外故障删除既有断言。

- [ ] **Step 2: 运行前端完整检查**

运行：`npm --prefix frontend run typecheck` 与 `npm --prefix frontend run build`。预期均成功。

- [ ] **Step 3: 烟测后端和前端接口**

复用当前运行的开发服务（不要重复启动占用端口的服务）；若需重启，先停止现有进程，再按项目现有 `run.py --no-browser` 与 Vite 开发命令启动。curl/HTTP 检查健康、Agent 配置读写、Skill 资源边界和 CubeSandbox 无配置时 fail-closed；检查前端路由及 API 代理。

- [ ] **Step 4: 人机交互检查**

在浏览器中确认 Agent 配置的委派目标、生成代码开关、Plugin Library 的 Sandbox 设置和 Skills 告警状态。若本轮没有可用浏览器交互工具，明确报告只做了 HTTP/UI 构建检查，不声称完成 GUI 验收。

- [ ] **Step 5: 报告已验证范围与部署依赖**

给出专项/全量测试、TypeScript/build 和应用烟测真实结果；明确需要用户另行部署的 CubeSandbox Linux/KVM 服务；没有真实服务时不宣称沙箱硬件隔离已联调。
