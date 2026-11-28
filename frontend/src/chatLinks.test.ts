import { describe, expect, it } from 'vitest';
import { normalizeWorkspaceLinkPath, parseChatLinks } from './chatLinks';

function compact(text: string) {
  return parseChatLinks(text).map((segment) => [segment.type, 'value' in segment ? segment.value : segment.type === 'workspace-file' ? segment.path : segment.href]);
}

describe('normalizeWorkspaceLinkPath', () => {
  it('normalizes safe relative separators', () => {
    expect(normalizeWorkspaceLinkPath('docs\\report.md')).toBe('docs/report.md');
    expect(normalizeWorkspaceLinkPath('README.md')).toBe('README.md');
  });

  it('rejects empty, escaping, absolute, and device paths', () => {
    for (const value of ['', '.', './README.md', '../secret', 'docs/../../secret', '/secret', 'C:/secret', '\\server\\share', 'a\u0000b']) {
      expect(normalizeWorkspaceLinkPath(value)).toBeNull();
    }
  });
});

describe('parseChatLinks', () => {
  it('parses markdown workspace and web links', () => {
    expect(compact('查看 [报告](reports/result.md) 和 [网站](https://example.com/a?q=1)。')).toEqual([
      ['text', '查看 '], ['workspace-file', 'reports/result.md'], ['text', ' 和 '], ['web', 'https://example.com/a?q=1'], ['text', '。'],
    ]);
  });

  it('parses plain web URLs and preserves line breaks', () => {
    expect(compact('打开 https://example.com/path.\n下一行')).toEqual([
      ['text', '打开 '], ['web', 'https://example.com/path'], ['text', '.\n下一行'],
    ]);
  });

  it('keeps unsafe or invalid links as plain text', () => {
    expect(parseChatLinks('[逃逸](../secret) file:///C:/secret javascript:alert(1) data:text/html,x')).toEqual([
      { type: 'text', value: '[逃逸](../secret) file:///C:/secret javascript:alert(1) data:text/html,x' },
    ]);
  });

  it('does not include punctuation in a web URL', () => {
    const segments = parseChatLinks('https://example.com, https://example.com/test);');
    expect(segments.filter((segment) => segment.type === 'web')).toEqual([
      { type: 'web', label: 'https://example.com', href: 'https://example.com' },
      { type: 'web', label: 'https://example.com/test', href: 'https://example.com/test' },
    ]);
  });
});
