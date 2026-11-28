import { describe, expect, it } from 'vitest';
import { shouldFollowChatBottom } from './chatScroll';

describe('shouldFollowChatBottom', () => {
  it('follows when the message viewport is already near the bottom', () => {
    expect(shouldFollowChatBottom(1200, 1010, 100)).toBe(true);
    expect(shouldFollowChatBottom(1200, 920, 100)).toBe(false);
  });

  it('follows when content does not overflow the viewport', () => {
    expect(shouldFollowChatBottom(90, 0, 100)).toBe(true);
  });
});
