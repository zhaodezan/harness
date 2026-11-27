# Windows PowerShell 默认执行与本地 Docker 沙箱、对话权限模式设计

- **日期**：2026-11-27
- **状态**：根据用户新增要求修订，待重新评审
- **范围**：Windows 本机工具/代码执行、本地 Docker 沙箱、主对话与 Agent 调试对话的审批模式

## 1. 目标

1. Windows 上未明确选择沙箱时，本地 Python Tool、生成的 Python 代码和 Skill 脚本默认通过系统 PowerShell 子进程执行。
2. 沙箱实现改为**本机 Docker Engine/Docker Desktop**，不使用 CubeSandbox，不连接远程 Docker Daemon，也不在应用中配置远程 Docker API。
3. 用户可在本机沙箱配置中指定 Docker 镜像名/Tag；镜像必须已经存在于本机 Docker 镜像库中，应用不自动从远程仓库拉取镜像。
4. 主对话和 Agent 调试对话提供权限下拉框：
   - `全部允许`（默认）：当前请求内不等待逐次审批，直接执行已授权工具；
   - `用户审批`：保留现有逐次审批流程。
5. 权限模式只保存到浏览器，不写入 Agent 或会话数据库；每个对话拥有独立偏好。

## 2. 非目标与安全边界

- 不实现远程 Docker 主机、Docker API URL、SSH Docker Context、Registry 登录凭据或远程镜像服务配置。
- 不改变 MCP 已选工具当前的免逐次审批规则。
- 不将“全部允许”扩展为绕过工具注册、Agent/对话工具选择、数据库连接授权、参数 Schema 校验或执行资源限制。
- 本机 Docker 沙箱未配置、Docker CLI 不存在、Docker Engine 未运行或镜像不存在时，不静默回退到 PowerShell；只有用户明确选择本机 PowerShell 才运行宿主机代码。
- 不把权限模式持久化到服务端，避免一个浏览器的临时选择影响其他浏览器或调用方。
- Docker 容器默认不挂载本机工作目录、不使用宿主网络、禁止新增 Linux capabilities、启用只读根文件系统并设置 CPU/内存/PID/输出/超时限制。PowerShell 本机执行仍明确标注为非沙箱。

## 3. 本地 Docker 沙箱配置

### 3.1 配置字段

保留 `/api/sandbox` 路径作为通用沙箱设置入口，但将配置内容改为：

```text
enabled: boolean
image: string                 # 例如 python:3.11-slim 或本机私有镜像名
timeout_seconds: 1..300
memory_mb: 128..8192
cpu_count: 1..8
network_enabled: boolean     # 默认 false
```

不再保存 `api_url`、`template_id`、`api_key` 或 CubeSandbox 凭据。默认镜像可显示为 `python:3.11-slim`，但保存/测试时必须确认该镜像已经存在于本机 Docker Engine；应用不执行隐式 `docker pull`。

### 3.2 Docker 执行器

新增 `DockerSandboxExecutor`，通过固定的 `docker` CLI 子进程运行一次性容器：

- 使用 `docker run --rm -i`，每次调用一个临时容器；
- 使用配置的镜像名，但不把用户代码拼接进命令行；源码和参数经 stdin JSON 传入固定 worker；
- 默认参数包含 `--network none`、`--read-only`、临时 `/tmp`、`--cap-drop ALL`、`--security-opt no-new-privileges`、CPU/内存/PID 限制；网络开关开启时只取消 `--network none`，不授予宿主网络或 Docker Socket；
- 不挂载宿主目录、Docker Socket、凭据目录或应用数据目录；
- 超时或取消时停止并删除容器，读取 stdout/stderr 时执行输出上限；
- 对 Docker CLI、镜像不存在、Engine 未运行、容器退出和 JSON 解析错误返回脱敏、可操作的 `DockerSandboxUnavailable`，不回退到 PowerShell；
- `is_configured()` 只检查配置和本机 Docker CLI/镜像可用性，不连接远程服务。

可以继续复用现有 `state.sandbox_executor` 调用协议，但底层实现和错误类型改为 Docker；不保留 CubeSandbox SDK 依赖。

## 4. 后端设计

### 4.1 请求协议与审批决策

为主会话消息和调试请求增加同一个枚举字段：

```text
approval_mode = "allow_all" | "user_approval"
```

默认值为 `allow_all`，以兼容缺少该字段的旧客户端。后端收到其他值时返回 422。

在主会话和调试运行的工具调用循环中：

1. 先执行现有的工具选择、数据库授权和参数校验。
2. MCP 工具继续按现有逻辑不进入逐次审批。
3. 对非 MCP 的 `side_effect` 工具：
   - `user_approval`：创建 pending approval，发送 `approval_required` 事件，等待用户决定；
   - `allow_all`：将本次调用标记为已批准，不创建 pending approval，不发送审批事件。
4. 工具实际执行前仍由 `ToolRegistry.execute(..., approved=True)` 或等价代码再次校验。
5. 运行轨迹记录实际权限结果；`allow_all` 至少记录一条已自动允许的审批决策事件，便于审计。

该规则同时覆盖：生成代码、本地 Python Tool、数据库写入及其他非 MCP 副作用工具。

### 4.2 Windows 执行路由

- Windows PowerShell 可用时，默认 `execution_mode=local` 的本地 Python Tool、生成代码和 Skill 脚本统一进入受控 PowerShell 子进程；不再只对静态验证的简单只读函数开放本机模式。
- Docker 沙箱已配置时，默认仍为 PowerShell；只有工作区明确选择 `execution_mode=sandbox` 才调用本地 Docker。
- Docker 沙箱未配置、Engine 不可用或镜像不存在但请求指定 `sandbox` 时，返回可操作错误，不回退到宿主机。
- 对普通函数格式和旧 `TOOL + run(arguments)` 格式，Windows worker 都必须提供对应入口；源码仍只从应用工具目录读取并校验源码未被替换。
- 非 Windows 保持现有行为，不引入 PowerShell；沙箱路径改为本机 Docker CLI（在支持 Docker 的环境中）。

## 5. 工作区默认值

`/api/workspaces` 相关逻辑：

- 默认选择 `local`；Windows UI 展示“本机 PowerShell”。
- `sandbox` 仅在本机 Docker 配置有效且镜像可用时可用；服务端仍验证显式选择。
- Agent 的 `sandbox_code_enabled` 只控制是否暴露生成代码工具，不再改变默认执行位置。
- 若旧会话保存了 `sandbox`，但当前 Docker 沙箱不可用，不自动执行；获取/运行时返回明确提示。

## 6. 前端设计

### 6.1 权限偏好

扩展现有 `ChatPreference`：

```ts
type ApprovalMode = 'allow_all' | 'user_approval';
type ChatPreference = {
  capabilities?: CapabilitySelection;
  model?: Pick<ModelOption, 'id' | 'name'>;
  approval_mode?: ApprovalMode;
};
```

读取旧浏览器数据时，缺失 `approval_mode` 视为 `allow_all`。继续使用现有按对话键控的 `sessionStorage`。

### 6.2 对话框

主对话和 Agent 调试对话在工具选项区域增加原生下拉框：

- `全部允许`：本次对话中的副作用工具直接执行，不弹审批；
- `用户审批`：每次副作用工具执行前询问。

发送请求时把 `approval_mode` 与 capabilities/model 一起传给对应 API。`allow_all` 下正常情况下不应收到审批事件；若服务端仍发送审批事件，保留现有审批处理作为防御性兼容，不自动批准意外事件。

### 6.3 工作区与 Docker 设置

- `WorkspacePicker` 默认显示“本机 PowerShell”；本机 Docker 配置有效时才启用“本地 Docker 沙箱”。
- Docker 不可用时隐藏或禁用沙箱选项，并提示先安装/启动 Docker Desktop、配置镜像。
- 插件库的沙箱设置页改为本地 Docker：镜像名、启用、超时、内存、CPU、网络开关和“测试本机 Docker”按钮。
- 不显示远程地址、Template ID、API Key、Registry 登录或远程 Docker 配置。

## 7. 测试设计

### 后端

1. 请求模型默认 `allow_all`，非法值返回 422。
2. `allow_all` 对生成代码、本地 Python Tool、数据库写入和其他副作用工具不创建 pending approval，且执行成功。
3. `user_approval` 保持现有 pending approval、批准和拒绝行为。
4. MCP 已选工具在两种模式下均不额外创建审批。
5. 工具未选中、数据库未授权、参数非法时，两种模式均拒绝执行。
6. Windows 无 Docker 沙箱配置时，普通格式、旧格式和生成代码均选择 PowerShell；显式 Docker 沙箱不可用时不回退本机。
7. Docker 配置有效但未明确选择时仍选择 PowerShell；明确选择后调用本机 Docker。
8. Docker CLI 不存在、Engine 未运行、镜像不存在、超时和超输出均返回脱敏错误并清理容器。
9. 非 Windows 行为保持原有测试预期。

### 前端

1. 主对话和调试对话包含两个权限选项，默认是 `allow_all`。
2. 选择保存到浏览器，按对话/Agent 恢复，旧存储缺字段时兼容默认值。
3. 请求 body 包含 `approval_mode`。
4. 沙箱设置页只出现本地 Docker 配置字段，不出现 CubeSandbox 或远程 Docker 字段。
5. WorkspacePicker 在 Docker 不可用时不把沙箱当作默认或可执行选项。
6. 运行前端 typecheck/build 和静态源码检查。

## 8. 依赖、迁移与文档

- 删除 `pyproject.toml` 中的 `cubesandbox` 依赖及对应 SDK 代码路径。
- 不增加数据库迁移；已有 Agent、会话和工具文件继续可读。
- `app_settings` 中旧的 CubeSandbox 配置读取后视为无效，不自动转换为 Docker 配置；用户需要在本地 Docker 设置页重新配置镜像。
- 旧客户端未发送 `approval_mode` 时后端默认 `allow_all`。
- 旧浏览器 `sessionStorage` 不清除；读取时补默认权限模式。
- README 明确说明：Windows 默认 PowerShell 不是安全沙箱；本地 Docker 仅在用户明确选择时运行；Docker 不可用时不会回退。
