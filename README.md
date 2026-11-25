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

支持 `def`、`async def`，以及 `str`、`int`、`float`、`bool` 和 `list[T]` 参数。默认值需是匹配类型的 JSON 字面量；返回值需可序列化为 JSON。程序静态读取函数签名，不在本机导入源码。可静态识别为简单无副作用的普通函数直接调用（例如只返回参数、常量、简单加减或 f-string 的函数）；其他普通 Python 函数仍需运行前确认。默认使用 CubeSandbox。已有 `TOOL` + `run(arguments)` 工具可继续使用。

**Windows 本机执行：** 后端自动检测原生 Windows、系统 PowerShell 和 Win32 运行依赖。通过静态验证的简单只读函数默认使用本机执行，无需配置 CubeSandbox 或逐次批准。在「模型及插件库 → Tool」可手动切换至 CubeSandbox，选择会持久保存；保存修改时重新验证源码。PowerShell 仅启动应用自带的受限表达式解释器，参数通过 JSON 传递，不运行插件模块或任意 shell 命令。PowerShell 不是沙箱；复杂 Python Tool、Skill 脚本及生成代码仍必须通过 CubeSandbox，不自动回退至宿主机。非 Windows 环境仍使用 CubeSandbox。

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

已连接且在对话或 Agent 配置中选中的 MCP 工具可由模型直接调用，不逐次审批。可能有副作用的 MCP 工具仍可能修改外部数据，请只启用信任的工具。连接 MCP Server 前仍须确认启动命令或服务地址；A2A 委派的只读限制不变。

## 下一版本候选

当前版本实现暂停，以下增强项不属于当前交付验收：安装器对已有依赖的安全重复运行；MCP OAuth 和更多 HTTP 认证选项；模型供应商能力预设；并发、断线、中断恢复和真实浏览器可访问性测试；备份导入冲突回滚。Multi-Agent 和独立聊天发布页也属于下一版本。

使用前请查看 `docs/superpowers/specs/2026-11-20-python-agent-harness-design.md` 与 `docs/superpowers/plans/2026-11-20-agent-harness-implementation.md`。
