import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

test('completed tool rows disclose only their own previews, using plain text', async () => {
  const vite = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, ws: false }, appType: 'custom',
  });
  try {
    const { RunTracePanel } = await vite.ssrLoadModule('/src/components/RunTracePanel.tsx');
    const tool = { event_type: 'tool_completed', status: 'completed', summary: '工具完成' };
    const preview = (text, truncated = false) => ({ text, truncated });
    const html = renderToStaticMarkup(createElement(RunTracePanel, { events: [
      tool,
      { ...tool, id: 1, tool_details: { tool_name: 'example', input: preview('{}'), output: preview('<sample>', true) } },
      { ...tool, id: 2, status: 'failed', tool_details: { tool_name: 'example', input: preview('{}'), error: preview('something failed') } },
    ] }));
    assert.equal(html.match(/<details\b/g)?.length, 3); // Outer panel and two rows, not the history-only row.
    assert.doesNotMatch(html, /<details[^>]*\sopen\b/);
    for (const text of ['工具名', 'example', '输入', '输出', '&lt;sample&gt;', '已截断', '错误', 'something failed']) {
      assert.ok(html.includes(text), `Missing ${text}`);
    }
    assert.ok(!html.includes('<sample>'));
  } finally { await vite.close(); }
});
