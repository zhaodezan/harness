from pathlib import Path
import json
import os
import shutil
import socket
import subprocess
import time

import pytest


FRONTEND = Path(__file__).parents[1] / "frontend" / "src"


def test_agent_config_autosaves_and_selects_all_plugin_types():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "setTimeout" in source
    assert "PATCH" in source
    assert "已自动保存" in source
    assert "重试保存" in source
    assert "selected_skills" in source
    assert "selected_mcp_tools" in source
    assert "selected_local_tools" in source
    assert "agent-config-columns" in source
    assert "max_rounds" in source
    assert "-1 表示不限" in source
    assert "cost-rates" not in source


def test_agent_config_can_authorize_other_agents_and_enable_sandboxed_python():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "selected_agents" in source
    assert "可委派的智能体" in source
    assert "sandbox_code_enabled" in source
    assert "沙箱" in source


def test_plugin_library_exposes_local_docker_controls_and_fail_closed_copy():
    source = (FRONTEND / "pages" / "PluginLibraryPage.tsx").read_text(encoding="utf-8")
    assert "CubeSandbox" not in source
    assert "windows-trust" not in source
    assert "保存沙箱配置" in source
    assert "测试本机 Docker" in source
    assert "不会回退到本机" in source
    assert "不自动拉取" in source
    for field in ("image", "timeout_seconds", "memory_mb", "cpu_count", "network_enabled"):
        assert f"sandbox.{field}" in source
    assert "sandbox.api_url" not in source
    assert "sandbox.template_id" not in source


def test_docker_settings_button_in_real_browser(tmp_path):
    sync_api = pytest.importorskip('playwright.sync_api')
    node = shutil.which('node')
    frontend = FRONTEND.parent
    vite = frontend / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if not node or not vite.exists():
        pytest.skip('Node and installed frontend dependencies are required')
    edge = Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    executable = str(edge) if edge.exists() else None
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([node, str(vite), '--host', '127.0.0.1', '--port', str(port), '--strictPort'],
                              cwd=frontend, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail('Vite server did not start')
                time.sleep(.1)
        with sync_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, executable_path=executable)
            except sync_api.Error as error:
                pytest.skip(f'No usable browser for Playwright: {error}')
            try:
                page = browser.new_page()
                requests = []
                failure = False
                delay = False
                pending = []
                delay_initial_get = True
                failures_left = 2
                settings = {'enabled': True, 'image': 'python:3.11-slim', 'timeout_seconds': 45,
                            'memory_mb': 1024, 'cpu_count': 2, 'network_enabled': True, 'configured': True}

                def mock_api(route):
                    nonlocal failure, delay_initial_get, failures_left
                    path = route.request.url.split('/api/', 1)[1].split('?', 1)[0]
                    if failures_left and path == 'sandbox' and route.request.method == 'GET':
                        failures_left -= 1
                        route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': '读取失败'}))
                        return
                    if delay_initial_get and path == 'sandbox' and route.request.method == 'GET':
                        delay_initial_get = False
                        pending.append(route)
                        return
                    if delay and path in ('sandbox', 'sandbox/test') and route.request.method in ('PUT', 'POST'):
                        pending.append(route)
                        return
                    if path == 'sandbox/test':
                        requests.append(route.request.method)
                        route.fulfill(status=409 if failure else 200, content_type='application/json',
                                      body=json.dumps({'detail': '镜像不可用'} if failure else {'ok': True}))
                    else:
                        value = settings if path == 'sandbox' else {'warnings': []} if path == 'models/migration-status' else []
                        route.fulfill(status=200, content_type='application/json', body=json.dumps(value))

                def await_pending():
                    deadline = time.monotonic() + 5
                    while not pending and time.monotonic() < deadline:
                        page.wait_for_timeout(25)
                    assert pending, 'Expected request did not reach the route handler'
                    return pending.pop(0)

                page.route('**/api/**', mock_api)
                page.goto(f'http://127.0.0.1:{port}/plugins?tab=Docker%20%E6%B2%99%E7%AE%B1')
                image = page.get_by_label('本地镜像')
                sync_api.expect(image).to_be_disabled()
                sync_api.expect(page.get_by_role('alert').filter(has_text='读取失败')).to_be_visible()
                page.get_by_role('button', name='重试加载沙箱配置').click()
                initial_get = await_pending()
                sync_api.expect(image).to_be_disabled()
                initial_get.fulfill(status=200, content_type='application/json', body=json.dumps(settings))
                sync_api.expect(image).to_be_enabled()
                sync_api.expect(page.get_by_label('启用 Docker 沙箱')).to_be_checked()
                sync_api.expect(page.get_by_label('允许容器联网')).to_be_checked()
                image.fill('my-local-image:latest')
                page.get_by_label('单次执行超时（秒）').fill('60')
                sync_api.expect(image).to_have_value('my-local-image:latest')
                button = page.locator('form').filter(has=page.get_by_label('本地镜像')).locator('button[type="button"]')
                button.click()
                sync_api.expect(page.get_by_role('status')).to_contain_text('Docker CLI、Engine 和本地镜像检查通过')
                failure = True
                button.click()
                sync_api.expect(page.get_by_role('alert')).to_contain_text('镜像不可用')
                sync_api.expect(page.get_by_role('alert')).to_contain_text('Docker CLI、Engine 和本地镜像')
                assert requests == ['POST', 'POST']
                delay = True
                save = page.locator('form').filter(has=page.get_by_label('本地镜像')).locator('button[type="submit"]')
                save.click()
                sync_api.expect(image).to_be_disabled()
                sync_api.expect(button).to_be_disabled()
                put = await_pending()
                assert put.request.method == 'PUT'
                assert put.request.post_data_json == {
                    'enabled': True, 'image': 'my-local-image:latest', 'timeout_seconds': 60,
                    'memory_mb': 1024, 'cpu_count': 2, 'network_enabled': True,
                }
                settings.update(image='my-local-image:latest', timeout_seconds=60)
                put.fulfill(status=200, content_type='application/json', body=json.dumps(settings))
                sync_api.expect(image).to_be_enabled()
                sync_api.expect(image).to_have_value('my-local-image:latest')
                button.click()
                sync_api.expect(image).to_be_disabled()
                sync_api.expect(save).to_be_disabled()
                post = await_pending()
                assert post.request.method == 'POST'
                post.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True}))
                sync_api.expect(image).to_be_enabled()
                sync_api.expect(page.get_by_role('status')).to_contain_text('未运行容器')
            finally:
                browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_debug_chat_uses_current_unsaved_config_object():
    source = (FRONTEND / "pages" / "AgentConfigPage.tsx").read_text(encoding="utf-8")
    assert "<AgentDebugChat" in source
    assert "model_config_id: modelConfigId" in source
    assert "system_prompt: systemPrompt" in source
