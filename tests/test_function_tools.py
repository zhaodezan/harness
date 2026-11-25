import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.tool_plugins import LocalToolManager
from backend.app.tools import ApprovalRequired, ToolRegistry


SOURCE = '''async def greet(name: str, punctuation: str = "!", *, counts: list[int] = [1]) -> str:
    """Return a greeting.

    Args:
        name: Recipient's display name.
        punctuation: Optional ending.
        counts: Repetition counts.
    """
    return (name + punctuation) * sum(counts)
'''


def test_function_tool_discovery_is_static_and_sandbox_receives_named_entrypoint(tmp_path):
    marker = tmp_path / 'host-execution'
    source = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n' + SOURCE
    calls = []

    async def sandbox(code, arguments, *, function_name):
        calls.append((code, arguments, function_name))
        return 'sandbox-result'

    registry = ToolRegistry()
    installed = LocalToolManager(tmp_path / 'tools', registry, sandbox).install_source(source)
    assert not marker.exists()
    assert installed['name'] == 'greet'
    assert installed['risk'] == 'side_effect'
    assert installed['description'] == 'Return a greeting.'
    assert installed['input_schema'] == {
        'type': 'object', 'required': ['name'], 'additionalProperties': False,
        'properties': {
            'name': {'type': 'string', 'description': "Recipient's display name."},
            'punctuation': {'type': 'string', 'description': 'Optional ending.', 'default': '!'},
            'counts': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Repetition counts.', 'default': [1]},
        },
    }
    with pytest.raises(ApprovalRequired):
        asyncio.run(registry.execute('greet', {'name': 'Ada'}, approved=False))
    assert not calls
    assert asyncio.run(registry.execute('greet', {'name': 'Ada'}, approved=True)) == 'sandbox-result'
    assert calls == [(source, {'name': 'Ada'}, 'greet')]
    assert not marker.exists()


@pytest.mark.parametrize('source', [
    'def greet(name: str, punctuation: str = "!") -> str:\n """Greeting."""\n return f"你好，{name}{punctuation}"',
    'def add(a: int, b: int) -> int:\n """Add numbers."""\n return a + b',
    'def describe(name: str, count: int) -> str:\n """Describe items."""\n return f"{name}: {count}"',
])
def test_simple_pure_function_runs_without_repeated_approval(tmp_path, source):
    calls = []

    async def sandbox(code, arguments, *, function_name):
        calls.append((code, arguments, function_name))
        return 'ok'

    registry = ToolRegistry()
    tool = LocalToolManager(tmp_path / 'tools', registry, sandbox).install_source(source)
    assert tool['risk'] == 'read_only'
    args = {name: 2 if prop['type'] == 'integer' else 'example' for name, prop in tool['input_schema']['properties'].items()}
    assert asyncio.run(registry.execute(tool['name'], args, approved=False)) == 'ok'
    assert calls == [(source, args, tool['name'])]


@pytest.mark.parametrize('source', [
    'import math\ndef f(x: int):\n """Import."""\n return x',
    'def f(x: int) -> result_type():\n """Annotation."""\n return x',
    'def f(x: int):\n """Call."""\n return helper(x)',
    'def f(x: str):\n """Attribute."""\n return x.upper()',
    'def f(x: int):\n """Assignment."""\n x = x + 1\n return x',
    'def f(x: int):\n """External name."""\n return unknown',
    'def f(x: int):\n """Formatting."""\n return "{}".format(x)',
    'def f(x: int):\n """Helper."""\n return x\ndef _helper():\n return 1',
])
def test_other_python_code_still_requires_approval(tmp_path, source):
    registry = ToolRegistry()
    item = LocalToolManager(tmp_path / 'tools', registry).install_source(source)
    assert item['risk'] == 'side_effect'
    args = {name: 2 if prop['type'] == 'integer' else 'example' for name, prop in item['input_schema']['properties'].items()}
    with pytest.raises(ApprovalRequired):
        asyncio.run(registry.execute(item['name'], args, approved=False))


def test_function_tool_import_update_and_startup_keep_the_same_id(tmp_path):
    db, data = tmp_path / 'db.sqlite', tmp_path / 'data'
    with TestClient(create_app(db, data_dir=data)) as client:
        item = client.post('/api/tools/import', files={'file': ('tool.py', SOURCE)}).json()
        result = client.put(f"/api/tools/{item['id']}", json={'source': SOURCE.replace('greet(', 'salute(')})
        assert result.status_code == 200, result.text
        assert result.json()['id'] == item['id']
    with TestClient(create_app(db, data_dir=data)) as client:
        [reloaded] = client.get('/api/tools').json()
        assert reloaded['id'] == item['id'] and reloaded['name'] == 'salute'
        assert reloaded['status'] == 'loaded'


@pytest.mark.parametrize(('source', 'message'), [
    ('def a(x: int):\n """First."""\ndef b():\n """Second."""', '一个公开'),
    ('def a(x=1):\n """Default."""', '类型注解'),
    ('def a(x: str = 1):\n """Invalid."""', '默认值'),
    ('def a(x: int, /):\n """Invalid."""', '位置'),
    ('def a(x: factory()):\n """Invalid."""', '类型注解'),
])
def test_unsupported_function_signatures_have_actionable_errors(tmp_path, source, message):
    with pytest.raises(ValueError, match=message):
        LocalToolManager(tmp_path / 'tools', ToolRegistry()).install_source(source)
