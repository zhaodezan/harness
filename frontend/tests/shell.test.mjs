import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { MemoryRouter } from 'react-router-dom';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

const viteOptions = {
  root: fileURLToPath(new URL('../', import.meta.url)),
  server: { middlewareMode: true, hmr: false }, appType: 'custom',
  ssr: { resolve: { externalConditions: ['node', 'module-sync'] } },
};

test('plugin library uses local Docker configuration and no static trust toggle', () => {
  const page = read('src/pages/PluginLibraryPage.tsx');
  assert.doesNotMatch(page, /CubeSandbox|windows-trust|sandbox\.api_url|sandbox\.template_id/);
  for (const field of ['image', 'timeout_seconds', 'memory_mb', 'cpu_count', 'network_enabled']) {
    assert.match(page, new RegExp(`sandbox\\.${field}`));
  }
  assert.match(page, /测试本机 Docker/);
  assert.match(page, /不自动拉取/);
  assert.match(page, /不会回退到本机/);
});

test('Docker test action sends POST and reports success and actionable failure', async () => {
  const vite = await createServer(viteOptions);
  const originalFetch = globalThis.fetch;
  try {
    const { testLocalDocker } = await vite.ssrLoadModule('/src/pages/PluginLibraryPage.tsx');
    const requests = [];
    const statuses = [];
    globalThis.fetch = async (url, init) => {
      requests.push([url, init.method]);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    };
    await testLocalDocker((status) => statuses.push(status));
    assert.deepEqual(requests, [['/api/sandbox/test', 'POST']]);
    assert.deepEqual(statuses.map((status) => status.testing), [true, false]);
    assert.match(statuses[1].message, /检查通过.*未运行容器/);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '镜像不可用' }), { status: 409 });
    statuses.length = 0;
    await testLocalDocker((status) => statuses.push(status));
    assert.deepEqual(statuses.map((status) => status.testing), [true, false]);
    assert.match(statuses[1].error, /镜像不可用.*Docker CLI.*Engine.*本地镜像/);
  } finally { globalThis.fetch = originalFetch; await vite.close(); }
});

test('chat composer exposes approval modes and keeps preferences in session storage', () => {
  const composer = read('src/ChatComposerOptions.tsx');
  const app = read('src/App.tsx');
  assert.match(composer, /ApprovalMode/);
  assert.match(composer, /allow_all/);
  assert.match(composer, /全部允许/);
  assert.match(composer, /user_approval/);
  assert.match(composer, /用户审批/);
  assert.match(composer, /approval_mode:\s*approvalMode/);
  assert.match(app, /approval_mode/);
  assert.match(app, /sessionStorage/);
  assert.match(app, /composerKey/);
});


test('workspace generations reject delayed A responses after A-B-A and unmount', async () => {
  const vite = await createServer(viteOptions);
  try {
    const { createWorkspaceGeneration } = await vite.ssrLoadModule('/src/components/WorkspacePicker.tsx');
    const generation = createWorkspaceGeneration();
    const oldA = generation.advance();
    const groupOrBrowseIsCurrent = generation.capture();
    let applied = false;
    let release;
    const delayedPut = new Promise((resolve) => { release = resolve; }).then(() => {
      if (groupOrBrowseIsCurrent()) applied = true;
      return generation.isCurrent(oldA);
    });
    generation.advance();
    const newA = generation.advance();
    release();
    assert.equal(await delayedPut, false);
    assert.equal(applied, false);
    assert.equal(generation.isCurrent(newA), true);
    generation.advance();
    assert.equal(generation.isCurrent(newA), false);
  } finally { await vite.close(); }
});


test('workspace reads wait for an earlier pending write to the same session', async () => {
  const vite = await createServer(viteOptions);
  try {
    const { createWorkspaceCoordinator } = await vite.ssrLoadModule('/src/components/WorkspacePicker.tsx');
    const coordinator = createWorkspaceCoordinator();
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const write = coordinator.write('A', () => pending);
    let readStarted = false;
    const read = coordinator.wait('A').then(() => { readStarted = true; });
    await Promise.resolve();
    assert.equal(readStarted, false);
    await coordinator.wait('B');
    finish();
    await write;
    await read;
    assert.equal(readStarted, true);
  } finally { await vite.close(); }
});


test('unknown write outcome stays blocked after late server write and remount', async () => {
  const vite = await createServer(viteOptions);
  try {
    const { createWorkspaceCoordinator } = await vite.ssrLoadModule('/src/components/WorkspacePicker.tsx');
    const { ApiError } = await vite.ssrLoadModule('/src/api.ts');
    const coordinator = createWorkspaceCoordinator();
    let finishServerWrite;
    let persisted = 'local';
    const serverWrite = new Promise((resolve) => { finishServerWrite = () => { persisted = 'sandbox'; resolve(); }; });
    await assert.rejects(coordinator.write('A', async () => { throw new TypeError('network failed'); }));
    await coordinator.wait('B');
    await assert.rejects(coordinator.wait('A'), /结果不确定/);
    finishServerWrite();
    await serverWrite;
    assert.equal(persisted, 'sandbox');
    await assert.rejects(coordinator.wait('A'), /结果不确定/);
    await assert.rejects(coordinator.write('A', async () => 'must not run'), /结果不确定/);
    for (const status of [404, 409, 422]) {
      const id = `rejected-${status}`;
      await assert.rejects(coordinator.write(id, async () => { throw new ApiError('rejected', status, null); }), /rejected/);
      await coordinator.wait(id);
    }
    for (const status of [400, 408, 429, 500, 502, 504]) {
      const id = `gateway-${status}`;
      let finishLateWrite;
      const lateWrite = new Promise((resolve) => { finishLateWrite = resolve; });
      await assert.rejects(coordinator.write(id, async () => { throw new ApiError('gateway', status, null); }), /结果不确定/);
      await assert.rejects(coordinator.wait(id), /结果不确定/);
      finishLateWrite();
      await lateWrite;
      await assert.rejects(coordinator.wait(id), /结果不确定/);
    }
  } finally { await vite.close(); }
});


test('workspace renders core navigation, conversation composer and administration entry points', async () => {
  const picker = read('src/components/WorkspacePicker.tsx');
  assert.match(picker, /api<SandboxStatus>\('\/sandbox'/);
  assert.match(picker, /setSandboxConfigured\(value\.configured === true\)/);
  assert.match(picker, /disabled=\{sandboxStatusLoading \|\| !sandboxConfigured\}/);
  assert.match(picker, /const previousChoice = choice/);
  assert.match(picker, /setChoice\(previousChoice\)/);
  assert.match(picker, /onChoice\(previousChoice\)/);
  assert.match(picker, /if \(applyInFlight\.current \|\| !workspaceReady \|\| currentSession\.current !== sessionId\) return/);
  assert.doesNotMatch(picker, /CubeSandbox|远程/);
  const vite = await createServer(viteOptions);
  try {
    const { default: App } = await vite.ssrLoadModule('/src/App.tsx');
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/chat'] }, React.createElement(App)));
    for (const label of ['新建对话', '模型及插件库', '运行记录', '备份与恢复', '发送', '分组 / 运行目录', '本机 PowerShell', '本地 Docker 沙箱']) {
      assert.ok(html.includes(label), `missing ${label}`);
    }
    assert.match(html, /长期记忆<\/button><button[^>]*><span class="nav-icon">[^<]*<\/span> 定时任务<\/button>/);
    assert.doesNotMatch(html, /运行轨迹/);
  } finally {
    await vite.close();
  }
});
