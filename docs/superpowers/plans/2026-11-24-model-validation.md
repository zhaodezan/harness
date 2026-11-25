# 模型连接前置测试与错误提示实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型只有在配置测试成功后才能保存，并在 Agent 模型选择中只显示友好名称。

**Architecture:** 在现有 `/api/models` 路由增加无副作用的候选测试；POST/PATCH 在持久化前测试完整候选配置，成功后才提交数据库与 SecretStore 变更。前端模型表单先测试，测试成功才允许保存；Agent 下拉仅显示 `name`。

**Tech Stack:** FastAPI、Pydantic、SQLite、httpx、pytest、React、TypeScript、Vite。

---

## 文件映射

- 修改 `backend/app/routes/models.py`：集中候选连接测试、创建/更新前验证及安全错误分类。
- 修改 `backend/app/models.py`：增加 write-only 测试/更新请求模型，API Key `repr=False`。
- 修改 `frontend/src/pages/PluginLibraryPage.tsx`：候选模型测试状态、保留草稿、通过测试后才启用保存。
- 修改 `frontend/src/pages/AgentConfigPage.tsx`：模型选择项只显示显示名称。
- 修改 `tests/test_model_configs.py`：模型 API 成功、失败、回滚、安全与编辑测试。
- 修改 `tests/test_frontend_model_library.py`、`tests/test_frontend_model_selection.py`：检查表单状态和显示文案。

## Task 1: 失败测试——未通过候选连接不得创建模型

**Files:** `tests/test_model_configs.py`

- [ ] **Step 1: 添加失败连接的 POST 测试**

使用 `httpx.MockTransport` 对 `/chat/completions` 返回 HTTP 401；POST `/api/models` 后断言状态为 502、返回值不含密钥，GET `/api/models` 为空，SecretStore 中没有对应模型密钥。再加 HTTP 200 有效响应的测试以保留成功路径。

```python
def test_create_model_rejects_failed_connection_without_persisting(tmp_path):
    async def provider(_request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    secrets = MemorySecrets()
    app = create_app(tmp_path / "models.sqlite3", secret_store=secrets,
                     http_transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        response = client.post("/api/models", json={
            "name": "Primary", "base_url": "https://example.test/v1",
            "model": "m", "api_key": "never-return-this",
        })
        listed = client.get("/api/models")
    assert response.status_code == 502
    assert "never-return-this" not in response.text
    assert listed.json() == []
    assert not any(key.startswith("model:") for key in secrets.values)
```

`MemorySecrets` 在本文件测试夹具中提供 `values` 字典，`get/set/delete` 直接访问它。

- [ ] **Step 2: 运行新增测试确认失败**

运行：`python -m pytest tests/test_model_configs.py::test_create_model_rejects_failed_connection_without_persisting -q`

预期：失败，因为当前 POST 尚未测试连接。

- [ ] **Step 3: 添加候选连接接口测试**

测试 `/api/models/test-connection` 成功返回 `status=ok`，列表仍为空、凭据库没有写入；再测试 401、404、超时分别得到无密钥、可操作的错误。响应正文不得包含供应商原始错误体或 API Key。

- [ ] **Step 4: 运行候选接口测试确认失败**

运行：`python -m pytest tests/test_model_configs.py -q`

预期：新增候选接口测试失败，旧实现无此接口。

## Task 2: 实现安全的候选连接测试与创建约束

**Files:** `backend/app/models.py`、`backend/app/routes/models.py`

- [ ] **Step 1: 定义请求模型**

在 `models.py` 增加候选配置模型与更新模型。API Key 为 `Field(..., repr=False)`；URL、model、name 使用现有 trim 和 http/https 校验。更新模型可接收可选 `api_key`，但响应模型仍不包含该字段。

- [ ] **Step 2: 实现错误分类 helper**

在 `routes/models.py` 增加内部 async helper `_test_candidate(request, *, base_url, model, api_key)`，调用 `complete_chat`。捕获 `ProviderError` 后将 401/403 归类为“请检查 API 密钥或服务商权限”，404/其他 4xx 归类为“请检查 API Base URL 和模型标识”，超时/网络错误保留安全网络建议；返回 502 的消息不得包含凭据或供应商响应原文。候选测试接口仅调用该 helper，不改变数据库和 SecretStore。

- [ ] **Step 3: 让 POST 先测后保存**

在创建事务前测试候选值。测试失败立即返回，不产生 ID 记录；测试成功后插入配置并写系统凭据库。如果写入凭据失败，删除新记录并返回原有 503 安全错误。成功后设置 `last_tested_at`，再构造响应。

- [ ] **Step 4: 测试 API 与回滚**

运行：`python -m pytest tests/test_model_configs.py -q`

预期：新旧模型 API 用例通过；失败模型不入库、不写凭据。

## Task 3: 编辑前测试且失败保持旧配置

**Files:** `backend/app/routes/models.py`、`tests/test_model_configs.py`

- [ ] **Step 1: 添加更新失败回归测试**

创建已测试模型并存旧密钥；模拟新 URL 的 provider 返回 404，PATCH 新 URL/model 后断言原 URL/model/测试时间/旧密钥不变。测试密钥轮换失败后旧密钥也应保留。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m pytest tests/test_model_configs.py::test_model_update_failure_preserves_previous_config_and_secret -q`

预期：旧实现未在更新时测试候选配置，断言失败。

- [ ] **Step 3: 实现 PATCH 候选值验证**

读取现有配置和当前 SecretStore 密钥，在内存中合成完整候选值。若请求含 `api_key`，测试新密钥；否则测试当前密钥。只有全部测试通过后再写入更新字段/新凭据；同一请求同时改连接和密钥时以整套新值测试。DB 更新失败不得先替换密钥；若 SecretStore 写入失败，不更新 DB。成功后更新 `last_tested_at`。将旧 `/credentials` 更新入口改为复用相同测试规则，避免绕过验证。

- [ ] **Step 4: 运行模型与密钥回归**

运行：`python -m pytest tests/test_model_configs.py tests/test_security.py tests/test_connection.py tests/test_agent_model_binding.py -q`

预期：所有测试通过且秘密不出现在 API、错误和日志中。

## Task 4: 前端新增模型先测试再保存

**Files:** `frontend/src/pages/PluginLibraryPage.tsx`、`tests/test_frontend_model_library.py`

- [ ] **Step 1: 添加前端契约测试**

断言候选接口路径、候选字段和独立的 testing/tested 状态存在；新增模式保存按钮在测试成功前 disabled；测试失败显示错误且不 reset 表单；只有成功后才调用 POST。

- [ ] **Step 2: 运行前端测试确认失败**

运行：`python -m pytest tests/test_frontend_model_library.py -q`

预期：新契约失败。

- [ ] **Step 3: 实现新增表单状态流**

新增 `modelCandidateStatus: 'idle' | 'testing' | 'passed'`。名称、URL、model、key 任一变化时回到 idle。新增模式的“测试连接”调用 `/models/test-connection`，失败保留四个字段并展示服务端 detail；成功置 passed。只有 passed 状态允许 POST。POST 成功才清空表单并 reload。编辑模式使用 PATCH 的完整候选验证；保存失败时不清空草稿。测试按钮 disabled 时显示进度。

- [ ] **Step 4: 运行前端契约测试**

运行：`python -m pytest tests/test_frontend_model_library.py -q`

预期：通过。

## Task 5: Agent 下拉只显示显示名称

**Files:** `frontend/src/pages/AgentConfigPage.tsx`、`tests/test_frontend_model_selection.py`

- [ ] **Step 1: 添加只显示名称的测试**

断言新建和编辑 `<option>` 文本模板仅包含 `item.name`，不拼接 `item.model`；当前模型摘要仍显示所选 Model 的 `name`。

- [ ] **Step 2: 修改选项和摘要**

将 `>{item.name} · {item.model}</option>` 改为 `>{item.name}</option>`；编辑时可以保留“缺少凭据”状态标记，但不显示底层模型标识。模型库中的详情仍可显示 model ID。

- [ ] **Step 3: 验证模型选择和构建**

运行：`python -m pytest tests/test_frontend_model_selection.py tests/test_frontend_model_library.py -q`  
运行：`npm --prefix frontend run typecheck`

预期：测试和 typecheck 均通过。

## Task 6: 整体验收

- [ ] **Step 1: 运行模型相关后端回归**

运行：`python -m pytest tests/test_model_configs.py tests/test_model_migration.py tests/test_model_data_transfer.py tests/test_agent_model_binding.py tests/test_setup_wizard.py -q`

预期：通过。

- [ ] **Step 2: 构建前端**

运行：`npm --prefix frontend run build`

预期：TypeScript 检查和 Vite production build 成功。

- [ ] **Step 3: 手工烟测**

在模型库分别输入错误 URL、错误 API Key、错误 model ID，确认显示可操作错误、草稿保留、列表无新模型；正确配置先测试再保存成功。打开 Agent 新建/编辑页，确认模型下拉只显示友好名称。
