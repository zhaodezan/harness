# Model 配置库与导航整合设计

日期：2026-11-23  
状态：设计已获用户确认，待用户审阅

## 背景

当前 Agent Harness 将模型服务地址、模型标识和 API 密钥直接保存在每个智能体上。工作区侧栏有“智能体”和“插件库”入口；插件库包含 Agent、Skill、MCP、Tool 与第三方插件占位 Tab。用户希望统一模型配置管理，并调整导航，避免模型连接信息在多个智能体间重复维护。

## 目标

1. 移除工作区侧栏独立的“智能体”入口。
2. 将侧栏“插件库”入口重命名为“模型及插件库”。
3. 在管理页新增 Model Tab，同时保留 Agent Tab；现有 Agent 创建、编辑和列表能力继续可用。
4. 建立独立、可复用的 Model 连接配置，Agent 通过引用选择 Model。
5. 为现有 Agent 自动迁移配置与密钥，确保升级后无需重新输入凭据。
6. 保留 Skill、MCP、Tool 与第三方插件 Tab 的既有功能和占位行为。

## 页面与交互

### 工作区导航

- 移除侧栏“智能体”按钮，不移除 Agent 管理能力或 Agent 路由。
- 侧栏“插件库”改名“模型及插件库”，跳转到统一管理页。
- 管理页顶层 Tab 为：**Model、Agent、Skill、MCP、Tool、第三方插件**。
- 第三方插件 Tab 继续包含 Dify、DSH 两个纯占位子 Tab，不增加安装、加载、连接或执行能力。
- Agent Tab 复用现有智能体列表、新建及编辑入口。

### Model Tab

- 显示已配置的模型连接列表：显示名称、API 地址、模型标识和连接状态（如可获得）。不得返回 API 密钥。
- 支持添加、编辑、连通性测试和删除模型连接。
- 每项 Model 配置包含：用户可识别的显示名称、兼容 OpenAI 的 API Base URL、模型标识、凭据库密钥引用及创建/更新时间。
- API 密钥只写入操作系统凭据库；数据库和 API 响应仅保存/返回非秘密字段。
- 多个 Agent 可共享一个 Model。编辑 Model 的 URL、模型标识或密钥后，所有引用该 Model 的 Agent 在下一次运行时使用新值。
- 删除正在被 Agent 引用的 Model 时返回冲突，说明关联 Agent；用户先为相关 Agent 改选其他 Model，方可删除。
- 空 Model 列表时给出清晰引导，提示先添加并测试模型连接，再创建/配置 Agent。

### Agent Tab 与配置页

- Agent Tab 保留现有列表、新建、编辑和进入调试功能。
- Agent 配置中的 Base URL、模型标识和 API 密钥输入替换为必选的 Model 下拉选择；提供跳转到 Model Tab 管理连接的入口。
- 新建 Agent 时若没有 Model，阻止提交并提示先配置 Model；用户完成 Model 设置后可返回 Agent 流程。
- 运行模式、上下文长度、系统提示词、Skills/MCP/Tool 授权、自动保存和右侧调试聊天继续属于 Agent 配置。
- Agent 调试使用 Agent 所选 Model 的已存凭据；调试配置快照包含 `model_config_id`，不从浏览器提交密钥。
- 新建 Agent 向导不再测试或创建内嵌模型连接；先选择已测试的 Model，再创建 Agent。新建后配置仍自动保存。

## 数据模型与迁移

- 新增 `model_configs` 表，字段至少包含：`id`、`name`、`base_url`、`model`、`created_at`、`updated_at`。为旧 Agent 自动迁移建立幂等映射（例如唯一 nullable 的 `legacy_agent_id`），避免启动重试时重复创建连接。
- Agent 新增非空 `model_config_id` 外键（迁移期可暂时允许空值，全部旧 Agent 迁移完成后逻辑要求必选）；移除对 Agent 自有 `base_url`、`model` 作为运行时来源的依赖。若为向后兼容仍保留旧列，需明确其仅作迁移兼容数据，不得与 Model 表形成双写的真相源。
- 凭据使用 `model:<model_config_id>:api_key` 作为系统凭据库键；测试连接与运行时均按该键读取。
- 启动迁移按每个旧 Agent 创建一个独立 Model 配置，将旧 `base_url` 和 `model` 复制过去，并把 Agent 关联到该配置；从旧 Agent 凭据键读取密钥后写入新的 Model 凭据键。成功写入新密钥后删除旧键；若迁移中断，重启时按 Agent 映射继续，不重复创建 Model。
- 若凭据库不可读/不可写，或 Agent URL/模型标识缺失，不能静默丢数据或伪报迁移成功；保留旧字段/凭据及旧配置键，产生可见迁移警告，并要求用户修复后重试。已创建的 Model 显示为待修复/未配置凭据，迁移重试不得覆盖用户后来设置的新凭据。旧密钥只有确认已成功复制后才删除。
- 对成功迁移但没有旧 API 密钥的 Agent，仍创建并关联 Model 配置，但标记为缺少凭据，显示配置提示；用户可在 Model Tab 补录密钥后使用。
- Model 删除需检查引用并清理其系统凭据；删除 Agent 后若 Model 不再被引用，不自动删除 Model，避免影响其他 Agent。
- 备份/恢复包含 Model 配置和 Agent 的 Model 引用。导入时为 Model 生成新 ID，并重映射 Agent 关系；密钥不导出，导入后提示用户重新填写 Model 凭据。
- 旧版备份若只有 Agent 自带 `base_url`/`model`，导入时为对应 Agent 创建 Model 配置，并关联到该配置。

## API 与运行时

- 新增 Model CRUD/测试连接 API；Model API 请求/响应严禁明文返回凭据。
- Agent Create/Patch/Response 支持 `model_config_id`，后端验证引用的 Model 存在。
- Agent 运行与连接测试通过数据库关联查询获取 Model URL/名称，通过凭据库获取密钥。
- Agent 切换 Model 只改变该 Agent 关系，不修改其他 Agent；编辑共享 Model 则对所有关联 Agent 生效。
- 运行时缺少 Model 或密钥时返回明确配置错误，不发送空凭据请求。

## 错误处理

- Model URL/名称缺失、连通测试失败、凭据保存失败、被引用 Model 删除、旧配置迁移失败、备份引用无效等均返回具体可操作错误。
- Model 测试失败不删除或覆盖其他已有配置；编辑失败保留原配置与草稿。
- 并发编辑同一 Model 时避免陈旧写入静默覆盖新值，沿用当前应用更新语义并通过测试验证。

## 验收与测试

### 后端

- Model 创建、读取、更新、删除及凭据写入/读取；API 响应、日志和备份中不得泄漏 API 密钥。
- 连接测试使用指定 Model 的凭据；关联 Agent 运行使用当前选中的 Model。
- 多 Agent 共用一个 Model；编辑 Model 后两 Agent 使用新配置；单个 Agent 改选 Model 不影响其他 Agent。
- 阻止删除被引用 Model；解除引用后可删除并清除相应系统凭据。
- 旧数据库迁移创建一个 Model/Agent、保存旧关联与密钥；迁移重复启动不重复创建；故障场景保留可恢复旧数据并给出警告。
- 新旧备份都可导入；Model ID 和 Agent 引用正确重映射；凭据不导出。
- Skill/MCP/Tool 权限隔离与原有聊天、调试、审批流程不回归。

### 前端

- 侧栏不再显示“智能体”，插件库入口显示“模型及插件库”。
- Model Tab 支持连接列表、新建/编辑/测试/删除；密钥输入为密码控件，列表不显示密钥。
- Agent Tab 仍可管理 Agent；Agent 配置只选择 Model，不再显示 URL、模型名、密钥字段。
- 没有 Model 时，新建 Agent 有清晰引导；创建/编辑后自动保存及调试使用所选 Model。
- 第三方插件 Tab 的 Dify、DSH 占位保持无插件 API 或加载行为。

## 不在范围

- Dify/DSH 及其他第三方平台的实际插件接入。
- 远程模型市场、自动发现模型目录或在线下载。
- 为多个 Model 提供批量导入、密钥轮换调度或按 Agent 覆盖密钥。
