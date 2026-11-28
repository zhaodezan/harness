import { describe, expect, it } from 'vitest';
import {
  createTabState,
  resolveDirtyDecision,
  tabsReducer,
  type FileTab,
} from './workspaceFileTabs';

const file = (path: string, content = 'content'): FileTab => ({
  id: `file:${path}`,
  kind: 'file',
  path,
  name: path.split('/').at(-1) ?? path,
  previewKind: 'text',
  content,
  revision: 'a'.repeat(64),
  dirty: false,
  size: content.length,
  modifiedAt: 'now',
});

describe('workspace file tabs reducer', () => {
  it('opens new tabs and focuses an existing tab without duplicating it', () => {
    let state = createTabState();
    state = tabsReducer(state, { type: 'open', tab: file('a.txt') });
    state = tabsReducer(state, { type: 'open', tab: file('b.txt') });
    state = tabsReducer(state, { type: 'open', tab: file('a.txt', 'new') });
    expect(state.tabs.map((tab) => tab.id)).toEqual(['file:a.txt', 'file:b.txt']);
    expect(state.activeId).toBe('file:a.txt');
  });

  it('marks content dirty and clears it after a saved replacement', () => {
    let state = tabsReducer(createTabState(), { type: 'open', tab: file('a.txt') });
    state = tabsReducer(state, { type: 'update-content', id: 'file:a.txt', content: 'draft' });
    expect(state.tabs[0]).toMatchObject({ content: 'draft', dirty: true });
    state = tabsReducer(state, { type: 'saved', id: 'file:a.txt', content: 'saved', revision: 'b'.repeat(64) });
    expect(state.tabs[0]).toMatchObject({ content: 'saved', revision: 'b'.repeat(64), dirty: false });
  });

  it('closes the active tab and focuses its neighbor', () => {
    let state = createTabState();
    state = tabsReducer(state, { type: 'open', tab: file('a.txt') });
    state = tabsReducer(state, { type: 'open', tab: file('b.txt') });
    state = tabsReducer(state, { type: 'open', tab: file('c.txt') });
    state = tabsReducer(state, { type: 'close', id: 'file:b.txt' });
    expect(state.tabs.map((tab) => tab.id)).toEqual(['file:a.txt', 'file:c.txt']);
    expect(state.activeId).toBe('file:c.txt');
  });

  it('resets all tabs when the session changes', () => {
    let state = tabsReducer(createTabState(), { type: 'open', tab: file('a.txt') });
    expect(tabsReducer(state, { type: 'reset' })).toEqual(createTabState());
  });
});

describe('resolveDirtyDecision', () => {
  it('keeps state for cancel and requests save or discard otherwise', () => {
    expect(resolveDirtyDecision('cancel')).toEqual({ action: 'cancel', close: false });
    expect(resolveDirtyDecision('save')).toEqual({ action: 'save', close: false });
    expect(resolveDirtyDecision('discard')).toEqual({ action: 'discard', close: true });
  });
});
