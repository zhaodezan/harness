import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

test('MCP stdio environment parser accepts KEY=VALUE lines and preserves equals in values', async () => {
  const vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  try {
    const { parseEnvironmentLines } = await vite.ssrLoadModule('/src/environment.ts');
    assert.deepEqual(parseEnvironmentLines('TOKEN=a=b\nREGION=west'), { TOKEN: 'a=b', REGION: 'west' });
  } finally { await vite.close(); }
});

test('MCP stdio environment parser rejects malformed names and lines', async () => {
  const vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  try {
    const { parseEnvironmentLines } = await vite.ssrLoadModule('/src/environment.ts');
    assert.throws(() => parseEnvironmentLines('BAD-NAME=value'), /名称无效/);
    assert.throws(() => parseEnvironmentLines('missing-separator'), /KEY=VALUE/);
  } finally { await vite.close(); }
});
