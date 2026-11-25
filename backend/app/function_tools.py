"""Translate a documented Python function into a tool schema without importing it."""
import ast
import json
import re
from typing import Any

from backend.app.tools import ToolRegistry


_TYPES = {'str': 'string', 'int': 'integer', 'float': 'number', 'bool': 'boolean'}


def _schema(annotation: ast.expr | None, parameter: str, depth: int = 0) -> dict[str, Any]:
    if depth > 10:
        raise ValueError('Tool 参数类型嵌套不能超过 10 层')
    if isinstance(annotation, ast.Name) and annotation.id in _TYPES:
        return {'type': _TYPES[annotation.id]}
    if (isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name)
            and annotation.value.id == 'list'):
        return {'type': 'array', 'items': _schema(annotation.slice, parameter, depth + 1)}
    raise ValueError(f'参数 {parameter} 需要支持的类型注解：str、int、float、bool 或 list[T]')


def _documentation(doc: str) -> tuple[str, dict[str, str]]:
    description: list[str] = []
    parameters: dict[str, str] = {}
    in_args = False
    current = None
    for line in doc.splitlines():
        if not in_args:
            if line.strip() in {'Args:', 'Arguments:'}:
                in_args = True
            else:
                description.append(line)
            continue
        if line and not line[0].isspace():
            break  # e.g. Returns:
        match = re.fullmatch(r'\s+([^\s:(]+)(?:\s+\([^)]*\))?:\s*(.*)', line)
        if match and match[1].isidentifier():
            current = match[1]
            parameters[current] = match[2].strip()
        elif current and line.strip():
            parameters[current] = (parameters[current] + ' ' + line.strip()).strip()
    return '\n'.join(description).strip(), parameters


def _is_simple_read_only(tree: ast.Module, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Recognize only a small, inert return-expression subset. Everything else needs approval."""
    def without_docstring(body):
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            return body[1:]
        return body

    # Imports, helpers, module expressions and any other executable statements are not allowed.
    if without_docstring(tree.body) != [function] or function.decorator_list:
        return False
    if function.returns is not None:
        try:
            _schema(function.returns, 'return')
        except ValueError:
            return False
    body = without_docstring(function.body)
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    names = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
    expression = body[0].value
    if expression is None:
        return True
    allowed = (ast.Constant, ast.Name, ast.Load, ast.JoinedStr, ast.FormattedValue,
               ast.BinOp, ast.Add, ast.Sub, ast.UnaryOp, ast.UAdd, ast.USub)
    for node in ast.walk(expression):
        if not isinstance(node, allowed):
            return False
        if isinstance(node, ast.Name) and node.id not in names:
            return False
        if isinstance(node, ast.Constant) and type(node.value) not in {str, int, float, bool, type(None)}:
            return False
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            return False
    return True


def function_tool_metadata(tree: ast.Module) -> dict[str, Any]:
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and not node.name.startswith('_')]
    if len(functions) != 1:
        raise ValueError('每个 Python Tool 文件必须定义一个公开函数；辅助函数使用下划线开头')
    function = functions[0]
    if function.decorator_list:
        raise ValueError('Python Tool 请使用普通函数，不需要添加装饰器')
    signature = function.args
    if signature.posonlyargs or signature.vararg or signature.kwarg:
        raise ValueError('Python Tool 不支持仅位置参数、*args 或 **kwargs；请使用命名参数')
    description, documentation = _documentation(ast.get_docstring(function) or '')
    if not description:
        raise ValueError('Python Tool 函数需要 docstring 说明工具用途')
    properties, required = {}, []
    defaults = [None] * (len(signature.args) - len(signature.defaults)) + signature.defaults + signature.kw_defaults
    for parameter, default in zip(signature.args + signature.kwonlyargs, defaults, strict=True):
        schema = _schema(parameter.annotation, parameter.arg)
        if documentation.get(parameter.arg):
            schema['description'] = documentation[parameter.arg]
        if default is None:
            required.append(parameter.arg)
        else:
            try:
                value = ast.literal_eval(default)
                json.dumps(value, allow_nan=False)
                ToolRegistry._validate_value(schema, value, path=parameter.arg)
            except (TypeError, ValueError, SyntaxError, OverflowError):
                raise ValueError(f'参数 {parameter.arg} 的默认值必须是与注解匹配的 JSON 字面量') from None
            schema['default'] = value
        properties[parameter.arg] = schema
    return {
        'name': function.name, 'description': description,
        'risk': 'read_only' if _is_simple_read_only(tree, function) else 'side_effect',
        'input_schema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
    }
