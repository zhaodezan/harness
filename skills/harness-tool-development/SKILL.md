---
name: harness-tool-development
description: 用户要求为 Agent Harness 创建、生成、修改或安装本地 Python Tool，或询问 Tool 的普通函数格式时使用。适用于输入校验、单位转换、数据整理等可复用工具，不用于普通单次计算或 MCP 服务开发。
---

# Harness Python Tool 开发

先区分用户是想看函数示例，还是要在聊天中提交一个可安装的工具。只问格式时直接解释，不提交草稿。

## 函数格式

- 每个 UTF-8 `.py` 文件只定义一个顶层公开 `def` 或 `async def`；辅助函数名以 `_` 开头。不写 `TOOL` 字典、MCP 装饰器或 `run(arguments)` 适配层。
- 所有参数都用 `str`、`int`、`float`、`bool` 或 `list[T]` 注解（支持嵌套列表）。使用普通命名参数或仅限关键字参数，不用仅限位置参数、`*args`、`**kwargs`。默认值是与类型匹配的 JSON 字面量，例如 `1`、`False` 或 `[1, 2]`。
- 在 docstring 中描述用途，用 `Args:` 描述各参数。函数返回可序列化为 JSON 的值，不依赖 `print` 交付结果。模块加载时不做 I/O；优先使用标准库，不把凭据写进源码。

```python
def celsius_to_fahrenheit(celsius: float, digits: int = 1) -> float:
    """把摄氏温度转换为华氏温度。

    Args:
        celsius: 摄氏温度。
        digits: 保留的小数位数，默认 1。
    """
    return round(celsius * 9 / 5 + 32, digits)
```

## 在对话中提交草稿

1. 从需求确定函数名、参数、输出及是否读写文件或访问网络。只有这些内容存在实质歧义时才追问；否则按合理默认值写出一个完整模块。
2. 展示**完整源码**，说明用途、参数和实际影响，并请用户回复「确认安装 `<函数名>`」。安装成功后默认只为**已确认的目标 Agent**启用，不执行。若不知道当前 Agent 的 ID，请让用户提供 Agent 专属对话或配置页链接（`/agents/<ID>/chat` 或 `/agents/<ID>/config`）；不要猜 ID，也不要按名字猜测。工作区聊天没有默认 Agent；让用户指定，或确认仅安装到 Tool 库。改稿或改变目标 Agent 后，重新确认。
3. 在 **Windows 本机模式**用已加载 Skill 的 `skill:run_python_cli` 运行 [`scripts/install_tool.py`](scripts/install_tool.py)。建议选「用户审批」；审批被拒绝就停止，不换途径。`argv` 传刚才审阅的完整源码（无 Markdown 围栏），要启用时另外传 `"--agent-id", "<已确认的 ID>"`。脚本只访问 `127.0.0.1` 上的 Harness 后端，默认端口 `8765`；如用户确认了其他后端端口，才传 `"--port", "实际端口"`。源码会出现在工具调用参数里，不要写入凭据。
4. **同名冲突**：首次调用不要传 `--overwrite`。收到 `name_conflict` 时，告诉用户同名 Tool 已存在，覆盖后已关联的 Agent 将使用新版，并单独询问「是否确认覆盖 `<函数名>`？」首次安装确认不算覆盖确认。仅在用户明确确认后，使用同一份源码、同一 Agent 和端口再次调用，并添加 `"--overwrite", "<脚本返回的 overwrite_confirmation>"`。若再次提示冲突或返回 `conflict_changed`，停止并重新确认；不删除旧工具，不擅自重试覆盖。
5. 读取 JSON：`ok: true` 表示 Tool 已保存，只有 `enabled: true` 才报告已为目标 Agent 启用。若保存成功但启用未确认，明确区分两者，提示检查 Agent 配置。已打开的对话可能需刷新工具列表；有手动选择时也可能需在「＋」菜单选中新工具。**安装和启用后都不要自动调用工具，包括试跑或验证。** `unknown_result` 时先检查 Tool 库和 Agent 配置，不自动重试。若没有 Skill CLI（如 Docker 模式），请改用 Windows 本机模式，或到 Tool 库手动保存并在 Agent 配置中启用；不绕过限制。

CLI 调用示例（仅在用户确认安装这份示例源码后调用）：

```json
{
  "skill_name": "harness-tool-development",
  "path": "scripts/install_tool.py",
  "argv": [
    "--source-chunk=def greet(name: str) -> str:\n    \"\"\"生成问候语。\"\"\"\n    return '你好，' + name\n"
  ]
}
```

`--source-chunk=` 后面是原始文本，不用编码，不用保存到临时文件。需要时按原顺序拆成多个 `--source-chunk=…` 参数，脚本直接拼接，不添加分隔符。每段源码不超过 4000 个字符，整个 `argv` 仍受平台的 64 个参数、64 KiB 限制（JSON 转义后）；超限请改用 Tool 库导入 `.py`。
