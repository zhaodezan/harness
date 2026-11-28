import asyncio
import json
import os
from pathlib import Path

import pytest

from backend.app.dify_plugins.docker import DockerError, DockerRunner
from backend.app.dify_plugins.package import inspect_package
from backend.app.dify_plugins.tests.samples import package


def test_build_uses_validated_source_in_separate_context(monkeypatch):
    runner = DockerRunner(executable='docker')
    data = package()
    calls = []

    async def command(args, **options):
        calls.append((args, options))
        if args[0] == 'build':
            directory = Path(args[-1])
            assert (directory / 'plugin/manifest.yaml').is_file()
            assert (directory / 'Dockerfile').is_file()
            assert (directory / 'requirements.harness.txt').read_text().splitlines() == ['jsonpath-ng==1.12.0', 'dify-plugin==0.11.1']
        return 0, b''

    monkeypatch.setattr(runner, '_command', command)
    digest = inspect_package(data).digest
    assert asyncio.run(runner.build(data, inspect_package(data))) == 'harness-dify-plugin:' + digest
    assert calls[0][0] == ['image', 'inspect', 'python:3.12-slim']
    assert '--pull=false' in calls[1][0]


def test_run_passes_credentials_on_stdin_and_cleans_container(monkeypatch):
    runner = DockerRunner(executable='docker')
    calls = []

    async def command(args, **options):
        calls.append((args, options))
        if args[0] == 'run':
            assert json.loads(options['payload'])['credentials'] == {'key': 'private'}
            return 0, b'{"messages":[{"type":"text","text":"value"}]}'
        return 0, b''

    monkeypatch.setattr(runner, '_command', command)
    assert asyncio.run(runner.run('harness-dify-plugin:' + 'a' * 64, {'credentials': {'key': 'private'}})) == [{'type': 'text', 'text': 'value'}]
    args = calls[0][0]
    for flag in ['--network=none', '--read-only', '--cap-drop=ALL', '--user=65532:65532', '--pids-limit=64']:
        assert flag in args
    assert '--privileged' not in args
    assert '--mount' not in args
    assert 'private' not in str(args)
    assert calls[1][0] == ['rm', '-f', args[args.index('--name') + 1]]


def test_run_sanitizes_output_and_handles_timeout(monkeypatch):
    runner = DockerRunner(executable='docker')
    calls = []

    async def command(args, **options):
        calls.append(args)
        if args[0] == 'run':
            raise DockerError('容器操作超时。')
        return 0, b''

    monkeypatch.setattr(runner, '_command', command)
    with pytest.raises(DockerError, match='超时'):
        asyncio.run(runner.run('harness-dify-plugin:' + 'a' * 64, {}, network=True))
    assert '--network=none' not in calls[0]
    assert calls[-1][0] == 'rm'


def test_docker_cli_does_not_inherit_remote_or_secret_environment(monkeypatch):
    runner = DockerRunner(executable='docker')
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote.example:123')
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote')
    monkeypatch.setenv('OTHER_TOKEN', 'private')
    seen = []

    class Process:
        returncode = 0
        async def wait(self):
            return 0

    async def create(*args, **kwargs):
        seen.append((args, kwargs))
        return Process()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    assert asyncio.run(runner.exists('harness-dify-plugin:' + 'a' * 64))
    assert seen[0][0][:3] == ('docker', '--context', 'default')
    env = seen[0][1]['env']
    assert all(name not in env for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'OTHER_TOKEN'))
    assert os.path.isabs(env['DOCKER_CONFIG'])
