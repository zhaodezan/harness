"""Fixed child entry point for the deliberately small, pure-expression Tool subset.

Interpret the expression rather than importing, compiling or executing plugin code.
"""
import ast
import json
from pathlib import Path
import sys

# This file is shipped with the application, not with the installed plugin.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.app.function_tools import function_tool_metadata
from backend.app.tools import ToolRegistry


def evaluate(node, values):
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.JoinedStr):
        result = ''.join(evaluate(part, values) for part in node.values)
    elif isinstance(node, ast.FormattedValue):
        value = evaluate(node.value, values)
        result = {115: str, 114: repr, 97: ascii, -1: str}[node.conversion](value)
    elif isinstance(node, ast.UnaryOp):
        value = evaluate(node.operand, values)
        return +value if isinstance(node.op, ast.UAdd) else -value
    elif isinstance(node, ast.BinOp):
        left, right = evaluate(node.left, values), evaluate(node.right, values)
        result = left + right if isinstance(node.op, ast.Add) else left - right
    else:
        raise ValueError('Unsupported expression')
    if isinstance(result, (str, list)) and len(result) > 65536:
        raise ValueError('Result too large')
    return result


def execute(payload):
    tree = ast.parse(payload['source'])
    metadata = function_tool_metadata(tree)
    if metadata['risk'] != 'read_only' or metadata['name'] != payload['function_name']:
        raise ValueError('Not a supported pure function')
    arguments = payload['arguments']
    ToolRegistry._validate(metadata['input_schema'], arguments)
    values = {name: spec['default'] for name, spec in metadata['input_schema']['properties'].items()
              if 'default' in spec}
    values.update(arguments)
    function = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return evaluate(function.body[-1].value, values)


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    print(json.dumps({'result': execute(payload)}, allow_nan=False))
