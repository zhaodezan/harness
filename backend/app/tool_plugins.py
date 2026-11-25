from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from backend.app.runtime.windows_tool import WindowsToolError

from backend.app.function_tools import function_tool_metadata
from backend.app.runtime.cube_sandbox import SandboxUnavailable
from backend.app.tools import ToolDefinition, ToolRegistry

MAX_TOOL_SOURCE_BYTES = 256 * 1024
TOOL_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")


class LocalToolManager:
    def __init__(
        self, tools_dir: str | Path, registry: ToolRegistry,
        sandbox_executor: Callable[..., Any] | None = None,
        windows_executor=None,
    ) -> None:
        self.tools_dir = Path(tools_dir).expanduser().resolve()
        self.registry = registry
        self.sandbox_executor = sandbox_executor
        self.windows_executor = windows_executor
        self._installed: dict[str, dict[str, Any]] = {}

    def _sandbox_path(self, tool_id: str) -> Path:
        return self.tools_dir / f'{tool_id}.windows-sandbox'

    @staticmethod
    def _digest(source: str) -> str:
        return hashlib.sha256(source.encode('utf-8')).hexdigest()

    def _sandbox_selected(self, tool_id: str) -> bool:
        try:
            self._sandbox_path(tool_id).stat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def set_windows_trust(self, tool_id: str, *, source_sha256: str | None = None) -> dict[str, Any]:
        if not TOOL_ID_PATTERN.fullmatch(tool_id) or tool_id not in self._installed:
            raise KeyError(tool_id)
        item = self._installed[tool_id]
        if item['status'] != 'loaded':
            raise ValueError('请先修复工具源码')
        path = self._sandbox_path(tool_id)
        source = item['source']
        if source_sha256 is not None:
            if not item['windows_local_available']:
                raise ValueError('仅支持已静态验证的简单只读函数，其他工具继续使用 CubeSandbox')
            if source_sha256 != self._digest(source) or (self.tools_dir / f'{tool_id}.py').read_text(encoding='utf-8') != source:
                raise RuntimeError('工具源码已修改，请重新查看后确认')
            path.unlink(missing_ok=True)
        else:
            path.touch(exist_ok=True)
        definition, metadata = self._definition(source, tool_id)
        self.registry.unregister(item['name'])
        self.registry.register(definition)
        item.update(metadata)
        return dict(item)

    def install_source(self, source: str) -> dict[str, Any]:
        source = source.replace('\r\n', '\n').replace('\r', '\n')
        self._validate_source_size(source)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        tool_id = uuid.uuid4().hex
        path = self.tools_dir / f"{tool_id}.py"
        definition, metadata = self._definition(source, tool_id)
        self.registry.register(definition)
        try:
            path.write_text(source, encoding="utf-8")
        except OSError:
            self.registry.unregister(definition.name)
            raise
        item = {"id": tool_id, "source": source, **metadata, "status": "loaded", "error": ""}
        self._installed[tool_id] = item
        return dict(item)

    def load_installed(self) -> list[dict[str, Any]]:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.tools_dir.glob("*.py")):
            tool_id = path.stem
            if not TOOL_ID_PATTERN.fullmatch(tool_id) or tool_id in self._installed:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                definition, metadata = self._definition(source, tool_id)
                self.registry.register(definition)
                self._installed[tool_id] = {
                    "id": tool_id, "source": source, **metadata, "status": "loaded", "error": ""
                }
            except Exception as error:
                self._installed[tool_id] = {
                    "id": tool_id, "name": path.stem, "description": "", "risk": "side_effect",
                    "input_schema": {"type": "object", "properties": {}},
                    "source": path.read_text(encoding="utf-8", errors="replace"),
                    "status": "error", "error": str(error),
                }
        return self.list_tools()

    def update_source(self, tool_id: str, source: str) -> dict[str, Any]:
        if not TOOL_ID_PATTERN.fullmatch(tool_id):
            raise KeyError(tool_id)
        path = self.tools_dir / f"{tool_id}.py"
        current = self._installed.get(tool_id)
        if current is None or not path.is_file():
            raise KeyError(tool_id)
        source = source.replace('\r\n', '\n').replace('\r', '\n')
        self._validate_source_size(source)
        # Revalidate each revision while retaining a user's explicit sandbox preference.
        definition, metadata = self._definition(source, tool_id)
        previous_name = current["name"]
        temporary = path.with_suffix(".py.tmp")
        temporary.write_text(source, encoding="utf-8")
        if definition.name == previous_name:
            self.registry.unregister(previous_name)
            try:
                self.registry.register(definition)
            except Exception:
                old_definition, _ = self._definition(current.get("source", ""), tool_id)
                self.registry.register(old_definition)
                temporary.unlink(missing_ok=True)
                raise
        else:
            try:
                self.registry.register(definition)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            self.registry.unregister(previous_name)
        try:
            temporary.replace(path)
        except Exception:
            self.registry.unregister(definition.name)
            previous_source = current.get("source", "")
            if current.get("status") == "loaded" and previous_source:
                old_definition, _ = self._definition(previous_source, tool_id)
                self.registry.register(old_definition)
            raise
        item = {"id": tool_id, "source": source, **metadata, "status": "loaded", "error": ""}
        self._installed[tool_id] = item
        return dict(item)

    def delete_tool(self, tool_id: str) -> bool:
        if not TOOL_ID_PATTERN.fullmatch(tool_id):
            return False
        item = self._installed.pop(tool_id, None)
        if item is None:
            return False
        self.registry.unregister(item["name"])
        self._sandbox_path(tool_id).unlink(missing_ok=True)
        (self.tools_dir / f'{tool_id}.windows-trust').unlink(missing_ok=True)
        (self.tools_dir / f"{tool_id}.py").unlink(missing_ok=True)
        return True

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._installed.values()]

    @staticmethod
    def _validate_source_size(source: str) -> None:
        if len(source.encode("utf-8")) > MAX_TOOL_SOURCE_BYTES:
            raise ValueError("Tool 源码不能大于 256 KB")

    def _definition(self, source: str, tool_id: str) -> tuple[ToolDefinition, dict[str, Any]]:
        tree = ast.parse(source, filename=f"{tool_id}.py")
        metadata_node = next((node.value for node in tree.body if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL" for target in node.targets
        )), None)
        function_name = None
        if metadata_node is None:
            # Keep the legacy missing-docstring error useful for old run(arguments) files.
            if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'run'
                   and not ast.get_docstring(node) for node in tree.body):
                raise ValueError('旧格式需声明 TOOL 字典；普通函数格式需要类型注解和 docstring')
            metadata = function_tool_metadata(tree)
            function_name = metadata['name']
        else:
            try:
                metadata = ast.literal_eval(metadata_node)
            except (ValueError, TypeError, SyntaxError):
                raise ValueError("Tool 元数据 TOOL 必须只包含字面量") from None
            if not isinstance(metadata, dict):
                raise ValueError("Python 模块必须声明 TOOL 字典")
            if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run" for node in tree.body):
                raise ValueError("Python Tool 必须定义 run(arguments) 函数")
        name = metadata.get("name")
        description = metadata.get("description")
        schema = metadata.get("input_schema")
        risk = metadata.get("risk")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValueError("Tool 名称必须为 1-64 个字符")
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValueError("Tool 描述必须为 1-1000 个字符")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("Tool input_schema 必须是 object 类型的 JSON Schema")
        try:
            json.dumps(schema, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Tool input_schema 必须是有效 JSON") from None
        self._validate_schema(schema)
        if risk not in {"read_only", "side_effect"}:
            raise ValueError("Tool 风险级别必须是 read_only 或 side_effect")
        local_available = bool(function_name is not None and risk == 'read_only' and self.windows_executor is not None and self.windows_executor.available)
        local_mode = local_available and not self._sandbox_selected(tool_id)
        normalized = {"name": name.strip(), "description": description.strip(), "input_schema": schema, "risk": risk,
                      'execution_mode': 'windows_local' if local_mode else 'sandbox',
                      'windows_local_available': local_available, 'source_sha256': self._digest(source)}

        async def run_in_sandbox(arguments: dict[str, Any]):
            if local_mode:
                if self._sandbox_selected(tool_id):
                    raise WindowsToolError('工具已切换至 CubeSandbox，请重新调用')
                try:
                    unchanged = (self.tools_dir / f'{tool_id}.py').read_text(encoding='utf-8') == source
                except (OSError, UnicodeError):
                    unchanged = False
                if not unchanged:
                    raise WindowsToolError('工具源码已修改，请重新加载并确认')
                return await self.windows_executor.execute(source, arguments, function_name=function_name)
            if self.sandbox_executor is None:
                raise SandboxUnavailable("CubeSandbox 未配置，已拒绝在本机执行 Python Tool")
            if function_name is None:
                result = self.sandbox_executor(source, arguments)
            else:
                result = self.sandbox_executor(source, arguments, function_name=function_name)
            return await result if inspect.isawaitable(result) else result

        return ToolDefinition(
            name=normalized["name"], description=normalized["description"],
            input_schema=schema, risk=risk, handler=run_in_sandbox, plugin_id=tool_id,
        ), normalized

    @staticmethod
    def _validate_schema(schema: dict[str, Any], depth: int = 0) -> None:
        if depth > 12:
            raise ValueError("Tool 参数 Schema 嵌套不能超过 12 层")
        schema_type = schema.get("type")
        allowed_types = {"object", "string", "integer", "number", "boolean", "array"}
        if schema_type not in allowed_types:
            raise ValueError("Tool 参数 Schema 包含不支持的 type")
        if schema_type == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if not isinstance(properties, dict) or not all(isinstance(key, str) and isinstance(value, dict) for key, value in properties.items()):
                raise ValueError("Tool 参数 Schema 的 properties 必须是对象映射")
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                raise ValueError("Tool 参数 Schema 的 required 必须是字符串列表")
            missing = set(required) - set(properties)
            if missing:
                raise ValueError(f"Tool 参数 Schema 的 required 参数未定义：{', '.join(sorted(missing))}")
            if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
                raise ValueError("Tool 参数 Schema 的 additionalProperties 必须是布尔值")
            for child in properties.values():
                LocalToolManager._validate_schema(child, depth + 1)
        if schema_type == "array":
            items = schema.get("items")
            if not isinstance(items, dict):
                raise ValueError("Tool 数组参数必须定义 items Schema")
            LocalToolManager._validate_schema(items, depth + 1)
        if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
            raise ValueError("Tool 参数 Schema 的 enum 必须为非空列表")
