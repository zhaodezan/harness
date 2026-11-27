import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

test('chat renders only local attachment images and links to their originals', async () => {
  const vite = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, ws: false }, appType: 'custom' });
  try {
    const { ChatImages } = await vite.ssrLoadModule('/src/components/ChatImages.tsx');
    const id = 'a'.repeat(32);
    const local = `/api/chat-images/messages/${'b'.repeat(32)}/${'c'.repeat(32)}/${id}`;
    const html = renderToStaticMarkup(createElement(ChatImages, { images: [
      { id, label: '测试图', url: local },
      { id: 'd'.repeat(32), label: 'external', url: 'https://external.test/track.png' },
      { id: 'e'.repeat(32), label: 'local path', url: 'file:///C:/secret.png' },
    ] }));
    assert.match(html, new RegExp(`<img[^>]*src="${local}"`));
    assert.match(html, new RegExp(`<a[^>]*href="${local}"`));
    assert.match(html, /alt="测试图"/);
    assert.doesNotMatch(html, /external\.test|file:\/\//);
  } finally { await vite.close(); }
});
