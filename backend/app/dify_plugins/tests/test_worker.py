from enum import Enum
from types import SimpleNamespace


class MessageType(Enum):
    TEXT = 'text'
    JSON = 'json'

import pytest

from backend.app.dify_plugins.runner.worker import WorkerError, execute


class Tool:
    @classmethod
    def from_credentials(cls, credentials):
        assert credentials == {'key': 'example'}
        return cls()

    def invoke(self, arguments):
        yield SimpleNamespace(type=MessageType.TEXT, message=SimpleNamespace(text=arguments['text']))
        yield SimpleNamespace(type=MessageType.JSON, message=SimpleNamespace(json_object={'ok': True}))


class Registry:
    def get_tool_cls(self, provider, name):
        return Tool if (provider, name) == ('provider', 'tool') else None


def test_worker_uses_empty_session_constructor_and_normalizes_messages():
    result = execute({'provider': 'provider', 'tool': 'tool', 'credentials': {'key': 'example'},
                      'arguments': {'text': 'hello'}}, Registry())
    assert result == [{'type': 'text', 'text': 'hello'}, {'type': 'json', 'data': {'ok': True}}]


def test_probe_checks_every_tool_without_invoking_it():
    assert execute({'probe': [{'provider': 'provider', 'tool': 'tool'}]}, Registry()) == []
    with pytest.raises(WorkerError, match='incompatible'):
        execute({'probe': [{'provider': 'provider', 'tool': 'other'}]}, Registry())


def test_host_service_and_non_text_outputs_are_not_exposed():
    class DifySessionTool(Tool):
        def invoke(self, arguments):
            raise ValueError('Session is not initialized: internal credential')

    class BinaryTool(Tool):
        def invoke(self, arguments):
            yield SimpleNamespace(type='blob', message=None)

    for cls in (DifySessionTool, BinaryTool):
        registry = SimpleNamespace(get_tool_cls=lambda *args: cls)
        with pytest.raises(WorkerError, match='unsupported') as error:
            execute({'provider': 'provider', 'tool': 'tool', 'credentials': {'key': 'example'}, 'arguments': {}}, registry)
        assert 'credential' not in str(error.value)
