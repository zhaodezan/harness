import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { MemoryRouter } from 'react-router-dom';

const viteOptions = {
  server: { middlewareMode: true, hmr: false }, appType: 'custom',
  ssr: { resolve: { externalConditions: ['node', 'module-sync'] } },
};

test('workspace renders core navigation, conversation composer and administration entry points', async () => {
  const vite = await createServer(viteOptions);
  try {
    const { default: App } = await vite.ssrLoadModule('/src/App.tsx');
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/chat'] }, React.createElement(App)));
    for (const label of ['新建对话', '模型及插件库', '运行记录', '备份与恢复', '发送']) {
      assert.ok(html.includes(label), `missing ${label}`);
    }
    assert.doesNotMatch(html, /运行轨迹/);
  } finally {
    await vite.close();
  }
});
