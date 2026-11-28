# Agent Harness

个人本机运行的 Agent 工作台（早期开发版本），包含 Python API 和 React/TypeScript Web 前端。

## 快速开始

Python 3.11 或更新版本；Node.js 20 或更新版本。Windows 用户可双击 `install.bat`；macOS/Linux 用户在项目目录运行 `python install.py`。安装器检查环境、安装 Python 包、用锁文件安装前端依赖并构建界面。随后运行 `python run.py` 启动。首次启动后，Web 界面会执行本地存储/界面/凭据库诊断，并引导测试模型连接。

```bash
python install.py
python run.py
```

启动器默认仅监听本机 `127.0.0.1:8765`，可选空闲端口，并尝试自动打开浏览器。停止时按 `Ctrl+C`。若需不自动开浏览器：`python run.py --no-browser`。

数据默认保存在用户主目录下的 `.agent-harness` 文件夹；可通过环境变量 `AGENT_HARNESS_HOME` 指定数据目录。API 密钥不写入 SQLite 或备份。Windows 系统凭据库写入失败时，模型密钥改用当前 Windows 用户的 DPAPI 加密保存在数据目录的 `model-credentials/`，不能直接拷贝到其他用户或机器使用。填写模型地址时，推荐使用 HTTPS；远程 HTTP 会明文传输凭据与消息。

## 添加 Python Tool

在「模型及插件库 → Tool」保存编辑器代码，或导入 UTF-8 `.py` 文件。每个文件定义一个公开函数，用 docstring 说明用途，并在 `Args:` 中说明参数；私有辅助函数以 `_` 开头。无需写 `TOOL` 字典或 MCP 装饰器。

```python
def greet(name: str, punctuation: str = "!") -> str:
    """生成一条简短的问候语。

    Args:
        name: 要问候的人的名字。
        punctuation: 句末标点，默认为感叹号。
    """
    return f"你好，{name}{punctuation}"
```

支持 `def`、`async def`，以及 `str`、`int`、`float`、`bool` 和 `list[T]` 参数。默认值需是匹配类型的 JSON 字面量；返回值需可序列化为 JSON。程序静态读取函数签名，不在本机导入源码。已有 `TOOL` + `run(arguments)` 工具可继续使用。

**Windows 本机执行：** 后端检测原生 Windows、系统 PowerShell 和运行依赖。普通、复杂及旧版 Python Tool、生成的 Python 代码和 Skill 脚本在 Windows 默认通过 PowerShell 子进程在本机执行。PowerShell 和本机 Python 执行不是安全沙箱，代码可访问当前账号有权访问的资源。执行位置由对话选择，而不是 Tool 静态信任开关。

**Skill 命令行脚本：** Windows 本机模式可用 `skill:run_python_cli` 执行已加载 Skill 包内的 `.py` 入口。脚本可导入同目录模块；`argv` 按参数逐项传递，不经 Shell 解析。当前目录是对话工作区，需要 `--workspace` 的脚本可传 `['--workspace', '.', 'check']`。用现有对话审批设置，最长运行 15 分钟；失败时轨迹只提示退出码和安全错误类别，不回显原始异常或标准错误。不支持交互式输入或额外的标准输入，请勿把密钥放进 `argv`。Docker 模式不提供此入口，也不会回退到本机；旧的 `skill:run_python` 不变。

**聊天图片：** 已加载 Skill 的本机 CLI 返回工作区内的图片时，普通聊天和 Agent 调试页会在助手回复下展示预览，点击可查看大图。普通聊天保留本机预览，调试预览会过期。预览图片不随 JSON 备份导出；原图仍保存在工作区。

**聊天分组和生成代码：** 工作区聊天与 Agent 对话输入框下方可选择已有分组、新建分组，或从本机目录选择/粘贴路径建立分组。分组直接关联本机目录；未选择分组时会为会话创建独立目录。生成 Python 代码本机运行不是沙箱。对话权限默认“全部允许”，可改为“用户审批”；后者对需要审批的 Tool、Skill 脚本和生成代码请求确认。已选 MCP 工具仍直接调用，数据库访问另有授权限制。

**Docker 沙箱：** 在插件库配置本机 Docker CLI、Engine 和已存在的 Python 镜像，并在对话中明确选择 Docker 沙箱。应用不自动拉取镜像，也不连接远程 Docker。可设置超时、内存、CPU 和网络（默认关闭）；容器不挂载本机工作目录。沙箱未配置或执行失败时不会回退到本机。

## 开发

后端测试：

```bash
python -m pytest -q
```

前端热更新：

```bash
npm --prefix frontend run dev
```

后端 API 默认地址为 `http://127.0.0.1:8765/api`。当前已有健康检查、Agent CRUD/Prompt 版本与系统凭据库、会话/消息持久化、OpenAI 兼容 SSE 对话、ReAct 与基础 Plan-Execute、Python 工具注册及模型工具调用/副作用审批、长期记忆管理/相关召回、上下文自动总结、结构化运行事件搜索/JSONL/CSV 导出、会话搜索/分支 API、MCP stdio 与 Streamable HTTP 配置/连接/工具发现，以及 Skills 目录发现与按需/自动匹配加载。

当前 UI 提供分步首次配置向导（模型连通测试后创建智能体）、可选 Token 费率与单次费用估算、Agent 编辑/Prompt 变量预览/版本恢复与模板、模型连接测试、ReAct/Plan-Execute 选择、Skills 选择、MCP 管理、会话搜索/重命名/删除/分支续聊、回答新分支重试、计划与压缩摘要展示、运行轨迹、工具审批、长期记忆搜索/编辑/删除/清空、运行记录时间/级别/模块筛选与 JSONL/CSV 导出，以及本地 JSON 备份恢复。`python run.py` 默认只监听回环地址并尝试打开浏览器。

已连接且在对话或 Agent 配置中选中的 MCP 工具可由模型直接调用，不逐次审批。可能有副作用的 MCP 工具仍可能修改外部数据，请只启用信任的工具。A2A 委派的只读限制不变。

在「模型及插件库 → MCP」确认服务的启动命令或地址并连接成功后，应用会记住该服务，在下次启动时自动连接并加载工具。旧配置和从备份导入的配置需先手动确认一次；手动断开会取消自动连接。启动连接失败不会阻止应用启动，可在 MCP 列表重试。

在「模型及插件库 → 第三方插件 → Dify」可以通过外链浏览官方 Dify Marketplace（官网禁止网页内嵌），也可以在本系统内搜索并预检插件详情链接、导入 `.difypkg`。安装只支持已验证兼容的 Python 3.12 工具插件：不需部署 Dify，但需要本机 Docker Desktop/Linux Engine、基础 `python:3.12-slim` 镜像，以及可访问 PyPI 以构建插件依赖。市场搜索不执行代码；安装在确认后才构建隔离镜像。运行时网络默认关闭；插件凭据通过系统凭据库本地保存，Agent 必须单独勾选工具，且工具调用沿用本系统逐次审批。需要 Dify 模型/工作流回调、动态参数、OAuth、文件/二进制输入输出或非工具类型插件的包不会作为兼容插件安装。Marketplace 安装下载由市场返回的固定版本包，但市场未提供给本系统可验证的包签名，UI 会提示此限制。备份包含工具授权但不含安装包、容器镜像或插件凭据。当前机器若 Docker Linux Engine 未启动，可以浏览市场但不能完成安装或执行插件。

## 下一版本候选

当前版本实现暂停，以下增强项不属于当前交付验收：安装器对已有依赖的安全重复运行；MCP OAuth 和更多 HTTP 认证选项；模型供应商能力预设；并发、断线、中断恢复和真实浏览器可访问性测试；备份导入冲突回滚。Multi-Agent 和独立聊天发布页也属于下一版本。

使用前请查看 `docs/superpowers/specs/2026-11-20-python-agent-harness-design.md` 与 `docs/superpowers/plans/2026-11-20-agent-harness-implementation.md`。
