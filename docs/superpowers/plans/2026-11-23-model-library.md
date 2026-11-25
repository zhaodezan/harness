# Model 配置库与导航整合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加可复用的 Model 连接配置库，让 Agent 选择 Model，并把工作区导航整合到“模型及插件库”。

**Architecture:** 新增 SQLite `model_configs` 表和只存于系统凭据库的 Model API 密钥；Agent 通过 `model_config_id` 引用连接，聊天、调试和连接测试都从关联 Model 读取地址、型号与密钥。启动迁移为每个旧 Agent 幂等创建独立 Model 配置并迁移凭据；插件库增加 Model Tab，保留 Agent/Skill/MCP/Tool 与第三方插件 Tab，侧栏移除独立 Agent 入口并重命名插件库。

**Tech Stack:** Python 3.11、FastAPI、SQLite、Pydantic、Keyring secret store、React 19、TypeScript、Vite、pytest。

---

## 文件结构与责任

### 后端

- `backend/app/db.py`：新增 model_configs 表（含 `legacy_agent_id`、`credential_migrated` 与 `last_tested_at`）、`agents.model_config_id` 和迁移标记字段；扩展 Agent 关联查询所需索引/约束。
- `backend/app/model_migrations.py`（新建）：按 Agent 建立旧数据到 Model 配置的幂等映射，迁移凭据并返回可展示警告。
- `backend/app/models.py`：Agent Create/Patch/Response 使用 `model_config_id`，新增 Model Create/Patch/Response Pydantic 模型。
- `backend/app/routes/models.py`（新建）：Model 列表、创建、更新、删除、测试连接、设置/清除密钥 API；响应只给 `credential_configured` 布尔值，不返回密钥。
- `backend/app/routes/agents.py`：Agent CRUD 校验 model_config_id；Agent 连接测试从关联 Model 和系统凭据库取配置；删除 Agent 清理自身资源但不自动删共享 Model。
- `backend/app/routes/setup.py`：新建 Agent 只接收已存在的 model_config_id，不再执行旧的内嵌模型测试/创建流程；诊断可报告是否存在可用 Model。
- `backend/app/routes/sessions.py`、`backend/app/routes/debug.py`：按 model_config_id 查询模型地址/型号并读取 Model 凭据；移除 agent:<id>:api_key 作为运行时来源。
- `backend/app/routes/data_transfer.py`：备份输出 Model 配置和 Agent 引用；导入时重映射 ID，兼容旧备份内嵌的 Agent base_url/model。
- `backend/app/main.py`：注册 Model router；数据库初始化后执行幂等旧数据迁移并提供迁移状态接口。
- `tests/test_model_configs.py`、`tests/test_model_migration.py`（新建）；扩展 Agent、setup、chat、debug、data transfer 测试。

### 前端

- `frontend/src/pages/PluginLibraryPage.tsx`：顶层 Tab 加 Model，新增 Model 列表、新建/编辑、连通测试、删除确认；保留 Agent Tab 与其他 Tab。
- `frontend/src/pages/AgentConfigPage.tsx`：模型地址/型号/API Key 字段改为 Model 选择；新建和编辑都引用 model_config_id；调试快照不包含凭据。
- `frontend/src/App.tsx`：侧栏删除“智能体”，插件入口更名“模型及插件库”；保留 Agent 路由供管理页和配置跳转。
- `frontend/src/styles.css`：Model 卡片、连接状态、编辑表单与窄屏布局。
- `tests/test_frontend_model_library.py`（新建）：Model Tab 与侧栏文案、密钥不展示及表单契约。
- `tests/test_frontend_agent_config.py`：改为校验 Agent 选 Model 而非直接填写服务地址/密钥。

## 实施步骤

### Task 1: 数据库 Model 配置实体与旧 Agent 迁移

**Files:**
- Modify: `backend/app/db.py`
- Create: `backend/app/model_migrations.py`
- Modify: `tests/test_db.py`
- Create: `tests/test_model_migration.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: 写旧数据迁移测试**

建旧版 schema 数据库（先调用当前 `initialize_database`，再插入使用旧 `base_url`/`model` 的 Agent），使用内存 `SecretStore` 放入 `agent:<id>:api_key`。启动应用 lifespan 后直接从数据库断言：每个旧 Agent 有一个独立 Model 配置；Agent 已关联 `model_config_id`；Model URL/model 保留；旧 key 转移至 `model:<model_id>:api_key` 并在成功后删除；迁移重启不产生第二份 Model。另测缺少 URL/模型或 keyring 写入失败时旧字段/旧 key 保留，迁移警告可读取。

```python
def test_legacy_agent_migrates_to_one_model_and_moves_secret(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    initialize_database(database)
    secrets = MemorySecrets({"agent:agent-1:api_key": "legacy-secret"})
    with database_connection(database) as connection:
        connection.execute(
            "INSERT INTO agents(id,name,base_url,model) VALUES(?,?,?,?)",
            ("agent-1", "Legacy", "https://old.test/v1", "old-model"),
        )
    with TestClient(create_app(database, data_dir=tmp_path / "data", secret_store=secrets)):
        pass
    with database_connection(database) as connection:
        agent = connection.execute(
            "SELECT model_config_id FROM agents WHERE id = ?", ("agent-1",)
        ).fetchone()
        model = connection.execute(
            "SELECT id,base_url,model FROM model_configs WHERE id = ?", (agent[0],)
        ).fetchone()
    assert model["base_url"] == "https://old.test/v1"
    assert model["model"] == "old-model"
    assert secrets.get(f"model:{model['id']}:api_key") == "legacy-secret"
    assert secrets.get("agent:agent-1:api_key") is None
```

迁移测试同时覆盖启动两次不重复创建 Model；在 `tests/test_db.py::test_initialize_database_is_safe_to_repeat` 更新预期 migration 列表为包含 version 15。

- [x] **Step 2: 运行迁移测试确认失败**

Run: `python -m pytest tests/test_model_migration.py tests/test_db.py -q`  
Expected: FAIL，因为当前数据库没有 `model_configs`、Agent 没有 `model_config_id`、且启动时无迁移。

- [x] **Step 3: 增加 schema migration 与可重试迁移**

提升 schema version；创建 `model_configs(id,name,base_url,model,legacy_agent_id UNIQUE NULL,credential_migrated INTEGER NOT NULL DEFAULT 0,last_tested_at TEXT NULL,created_at,updated_at)`，给 Agent 加 nullable `model_config_id`（迁移完由 Agent API 强制必选）。旧 Model 显示名称确定为 `Agent 名称 · 旧模型标识`（截断到 100 字符），`legacy_agent_id` 唯一映射保证一 Agent 一条旧 Model 配置；外键删除策略为 `RESTRICT`。启动后迁移：先事务内插入/查找 legacy mapping 并关联 Agent；再从旧 secret key 读取 key，成功写入新 Model key 后标记 `credential_migrated=1`，之后删除旧 key。若缺 URL/model/key，Model 仍保留并显示待配置；若 keyring 操作失败，保留旧 key及状态并返回 warning。迁移未完成但旧 key 存在时禁止普通 credentials 更新，提示重试迁移；旧 key 不存在时允许用户补录新凭据并完成迁移标记。重试时只处理未完成的 legacy mapping，不重复创建 Model。

- [x] **Step 4: 运行迁移测试通过**

Run: `python -m pytest tests/test_model_migration.py tests/test_db.py tests/test_startup.py -q`  
Expected: PASS；迁移重复执行幂等，旧信息未丢失，失败状态可见并可恢复。

### Task 2: Model 管理 API 与密钥安全

**Files:**
- Create: `backend/app/routes/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/models.py`
- Create: `tests/test_model_configs.py`

- [x] **Step 1: 写 Model CRUD、连通测试和 keyring 测试**

用 `httpx.MockTransport` 模拟兼容接口，验证 `POST /api/models` 创建 Model、测试连接；`GET /api/models` 返回 `credential_configured` 但从不包含 API key；`PUT /api/models/{id}/credentials` 存入系统凭据库；PATCH URL/name/model；缺 key 的测试返回明确 409；删除未使用 Model 成功并清理凭据；删除已关联 Model 返回 409 并列出 Agent 名。

```python
def test_model_api_never_returns_api_key(tmp_path):
    store = MemorySecrets()
    with TestClient(create_app(tmp_path / "models.sqlite3", secret_store=store)) as client:
        created = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1", "model": "model-a", "api_key": "never-return-this",
        })
        listed = client.get("/api/models")
    assert created.status_code == 201
    assert "never-return-this" not in created.text
    assert "api_key" not in listed.text
    assert listed.json()[0]["credential_configured"] is True
```

- [x] **Step 2: 运行 Model API 测试确认失败**

Run: `python -m pytest tests/test_model_configs.py -q`  
Expected: FAIL，当前无 `/api/models` 路由。

- [x] **Step 3: 实现 Model API**

新增 `ModelConfigCreate/Update/Response` schema；POST Model schema 接受 write-only `api_key`：事务性保存非密钥字段，再向 secret store 写 `model:<id>:api_key`，若凭据保存失败回滚 Model 行，响应模型显式排除 `api_key`。后续改密钥使用 `PUT /api/models/{id}/credentials`，测试凭据配置状态时只返回 `credential_configured`。PATCH URL/model 成功后清除 `last_tested_at`；测试连接用该 Model 的 URL/model/key 调用 `complete_chat`，成功写入 `last_tested_at`、失败返回 502。删除先查询 Agent 引用，存在时返回 409；没有引用时删行并清 key。通过 FastAPI router 注册 `/api/models`。

- [x] **Step 4: 验证 API 与密钥测试**

Run: `python -m pytest tests/test_model_configs.py tests/test_security.py tests/test_connection.py -q`  
Expected: PASS；响应、日志、异常均不泄漏密钥。

### Task 3: Agent 绑定 Model 并切换运行时

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/routes/agents.py`
- Modify: `backend/app/routes/setup.py`
- Modify: `backend/app/routes/sessions.py`
- Modify: `backend/app/routes/debug.py`
- Modify: `backend/app/routes/data_transfer.py`
- Create: `tests/test_agent_model_binding.py`
- Modify: `tests/test_agent_debug.py`、`tests/test_chat.py`、`tests/test_setup_wizard.py`

- [x] **Step 1: 写 Agent Model 关联与运行时测试**

覆盖：AgentCreate/Patch 需要有效 `model_config_id`；不存在的 ID 返回 422；PATCH 切换 Model 仅改变当前 Agent；常规聊天、连接测试和 `/debug` 使用所选 Model 的 URL/model/key；缺少关联或 secret 返回明确错误。调试请求只传 `model_config_id`，不允许客户端覆盖 base_url/model/key。新建向导选择现有 Model，不再用 payload 创建新的模型连接。

- [x] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_model_binding.py tests/test_agent_debug.py tests/test_chat.py tests/test_setup_wizard.py -q`  
Expected: FAIL，因为 Agent schema 和运行时仍直接使用 Agent 的 base_url/model 与 Agent key。

- [x] **Step 3: 改造 Agent API 和运行时 Model 查询**

`AgentCreate` 接受必需的 `model_config_id`，创建 Agent 或通过 `AgentPatch` 切换 Model 时，后端校验 Model 存在、凭据已配置且连接测试已通过（`last_tested_at` 非空）。修改 Agent 其他字段不要求重复测试当前 Model，已迁移的旧 Agent 保持可运行，不因迁移而伪造测试时间。`AgentResponse` 返回 Model ID 与非秘密 display summary；Agent CRUD 都 join `model_configs`。同步更新所有 Agent 创建测试工厂，为需要成功运行的测试先创建一个 Model 并配置 fake secret；只测试 Agent 元数据的用例也传合法 model_config_id。测试 Agent 连接通过 Agent 引用查到 Model 并使用 `model:<id>:api_key`。Setup 只创建 Agent 行及其 Agent 子配置，不新增 Model，也不保存/接收 API key。Sessions 查询联结 Model，并改用该行 base_url/model 与 Model secret。Debug 请求只接收配置快照中的 `model_config_id`，忽略/拒绝客户端传入的 base_url/model/api_key；后端校验 Agent 与 Model 均存在，从快照所选 Model 读取 URL/model 与凭据库 key，因此未自动保存的 Model 下拉变更也能即时调试。保留当前工具权限、调试与审批逻辑。

- [x] **Step 4: 运行运行时回归测试**

Run: `python -m pytest tests/test_agent_model_binding.py tests/test_agent_debug.py tests/test_chat.py tests/test_connection.py tests/test_setup_wizard.py tests/test_provider_tools.py tests/test_approval_roundtrip.py -q`  
Expected: PASS；聊天、调试审批和工具调用依旧有效且使用共享 Model。

### Task 4: Model 配置备份与旧备份兼容

**Files:**
- Modify: `backend/app/routes/data_transfer.py`
- Modify: `tests/test_data_transfer.py`
- Create: `tests/test_model_data_transfer.py`

- [x] **Step 1: 写新旧备份测试**

新格式导出包含 `models` 非秘密配置和 Agent `model_config_id`；导入时 Model 与 Agent 使用新 ID 并正确重映射；密钥字段不导出。旧格式（无 `models`，Agent 含 base_url/model）导入后为每个 Agent 创建独立 Model，Agent 关联新 ID，并返回“需重新配置凭据” warning。非法 model ID 引用必须 422，不能写入悬空 Agent。

- [x] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_model_data_transfer.py tests/test_data_transfer.py -q`  
Expected: FAIL，当前备份不包含 Model 配置并将模型字段内嵌在 Agent。

- [x] **Step 3: 实现备份导入/导出及 ID remap**

`/api/data/export` 输出 `format_version: 2` 和 `models` 非秘密配置；每个 Agent 导出 `model_config_id`，不导出 secret。`ImportPayload` 同时接受 v1 与 v2：v2 先验证并创建 Model、生成旧新 ID map，再创建 Agent 并映射 Model ID；v1 无 Model 集合时，在同一导入事务为每个 legacy Agent 创建 Model config。所有凭据保持未配置，不向系统凭据库写导入文件中的任何 secret，预览/结果返回需重新配置凭据 warning。

- [x] **Step 4: 验证备份与既有导入测试**

Run: `python -m pytest tests/test_model_data_transfer.py tests/test_data_transfer.py tests/test_import_preview.py -q`  
Expected: PASS；新旧备份都可用，Model 关系正确，凭据不泄漏。

### Task 5: Model Tab、共享连接管理与导航

**Files:**
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/styles.css`
- Create: `tests/test_frontend_model_library.py`
- Modify: `tests/test_frontend_plugin_library.py`

- [x] **Step 1: 写前端模型管理与导航测试**

源码验收 Model Tab 有列表、显示名称/API 地址/模型标识、密钥密码输入、创建/编辑/测试/删除操作及凭据状态，不渲染密钥值；插件库标题/侧栏为“模型及插件库”，侧栏不再有“智能体”；仍存在 Agent Tab；第三方 Dify/DSH 仍只是占位。

- [x] **Step 2: 运行前端测试确认失败**

Run: `python -m pytest tests/test_frontend_model_library.py tests/test_frontend_plugin_library.py -q`  
Expected: FAIL，当前无 Model Tab，侧栏标题/入口仍是旧文案。

- [x] **Step 3: 实现 Model 管理 UI**

插件页 Tab 类型增加 `Model`，将其作为第一个 Tab；保留 Agent、Skill、MCP、Tool、第三方插件。模型列表项提供编辑/删除，并显示 credential configured 与测试时间状态；添加/编辑表单包含显示名称、Base URL、模型标识、API key。创建时 POST payload 带 write-only key 并由后端保存至凭据库；编辑时密钥留空表示不修改，若填写则调用 credentials endpoint；不得把密钥放入 Model 状态对象或列表。连接测试按钮调用 Model 测试 API 并展示状态。URL/model 更新使旧测试状态失效。删除 409 显示后端关联 Agent 错误；无凭据配置显示待配置。App 侧栏移除 Agent 按钮，将插件入口改名“模型及插件库”并默认进入 Model Tab；Agent Tab 内仍能管理 Agent。CSS 加 Model 表单和响应式样式。

- [x] **Step 4: 验证前端插件库与构建**

Run: `python -m pytest tests/test_frontend_model_library.py tests/test_frontend_plugin_library.py tests/test_frontend_routes.py tests/test_frontend_agent_list.py -q`  
Expected: PASS。  
Run: `npm --prefix frontend run build`  
Expected: TypeScript/Vite build 成功。

### Task 6: Agent 配置改为选择 Model

**Files:**
- Modify: `frontend/src/pages/AgentConfigPage.tsx`
- Modify: `frontend/src/pages/AgentDebugChat.tsx`
- Modify: `frontend/src/pages/PluginLibraryPage.tsx`
- Modify: `tests/test_frontend_agent_config.py`
- Create: `tests/test_frontend_model_selection.py`

- [x] **Step 1: 写 Agent Model 选择和空列表状态测试**

校验配置页加载 models、使用 `model_config_id` 下拉、没有 Model 时提供去 Model Tab 的入口、不再出现 Base URL/model 名/API 密钥输入；编辑 Model 选择变化触发自动保存；调试请求体只有模型引用而不含密钥或连接字段；模型旧关联迁移 warning 可见。

- [x] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_frontend_model_selection.py tests/test_frontend_agent_config.py -q`  
Expected: FAIL，当前 Agent 表单仍有 Base URL/model/API key 内嵌字段。

- [x] **Step 3: 实现 Agent 选 Model 与配置流程**

AgentConfigPage 初始化时从 `/api/models` 加载 Model 列表；新增 `modelConfigId` draft，编辑 Agent 使用其 `model_config_id`。去掉 Base URL、模型名、API Key 输入状态和相关保存逻辑，改成 Model 下拉框并自动 PATCH `model_config_id`。Model 为空时隐藏/禁用 Agent 创建与保存并显示去 `/plugins?tab=Model` 的按钮；已有旧 Agent migration warning 保持。新建向导第一步选择已配置 Model，只有连接已验证/credential_configured 的 Model 可以创建 Agent；创建只提交 Agent profile 和 model_config_id。已有 Model 凭据通过测试连接与调试端点使用，AgentDebugChat 不上传 API key。编辑 Agent 的“测试模型连接”调用关联 Model 测试端点。保持其它字段自动保存和调试草稿行为。

- [x] **Step 4: 验证配置页和完整前端 build**

Run: `python -m pytest tests/test_frontend_model_selection.py tests/test_frontend_agent_config.py tests/test_frontend_debug_chat.py tests/test_frontend.py -q`  
Expected: PASS。  
Run: `npm --prefix frontend run build`  
Expected: PASS。

### Task 7: 全量验收和运行时 smoke test

**Files:**
- 仅修改前述任务中新测试揭示的必要实现文件。

- [x] **Step 1: 运行后端全量测试**

Run: `python -m pytest -q`  
Expected: 本次涉及测试全通过；如果仍只有此前确认忽略的 `tests/test_installer_idempotent.py` 两个基线失败，分别记录，其他失败需修复并重跑。

- [x] **Step 2: 运行前端测试和构建**

Run: `python -m pytest tests/test_frontend.py tests/test_frontend_routes.py tests/test_frontend_agent_list.py tests/test_frontend_styles.py tests/test_frontend_plugin_library.py tests/test_frontend_model_library.py tests/test_frontend_agent_config.py tests/test_frontend_model_selection.py tests/test_frontend_debug_chat.py -q`  
Run: `npm --prefix frontend run build`  
Expected: 前端功能测试、TypeScript 检查和构建全部通过。

- [x] **Step 3: 真实应用烟测**

启动后端和 Vite 开发服务器；检查 `/api/health` 返回 `{"status":"ok"}`、`/plugins?tab=Model` 返回 200。使用 Mock/有效模型配置在 Model Tab 新建并测试 Model；在 Agent Tab 创建两个 Agent 并绑定同一 Model；修改 Model 配置后分别调试两个 Agent 确认共享设置生效；将其中一个 Agent 改绑到另一 Model，验证另一个 Agent 不变；尝试删除仍被引用的 Model 确认得到阻止；确认 Agent/Skill/MCP/Tool/第三方 Dify、DSH Tab 均可访问。

## 执行约束

- 每个功能先写测试并观察预期失败，再实现最小行为，再运行本步骤测试。
- 本地工作区不是 Git 仓库，计划不含提交/合并操作。
- API 密钥必须始终留在操作系统凭据库；Agent/Model JSON、备份、日志和错误消息不包含明文密钥。
