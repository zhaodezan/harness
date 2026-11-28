export type FilePreviewKind = 'text' | 'image' | 'pdf' | 'media' | 'binary';

export type FileTab = {
  id: string;
  kind: 'file';
  path: string;
  name: string;
  previewKind: FilePreviewKind;
  content?: string;
  revision?: string;
  dirty: boolean;
  size: number;
  modifiedAt: string;
  mimeType?: string;
  blobUrl?: string;
  loading?: boolean;
  error?: string;
};

export type WebTab = {
  id: string;
  kind: 'web';
  href: string;
  title: string;
};

export type WorkspaceTab = FileTab | WebTab;

export type TabState = {
  tabs: WorkspaceTab[];
  activeId: string | null;
};

export type TabAction =
  | { type: 'open'; tab: WorkspaceTab }
  | { type: 'activate'; id: string }
  | { type: 'close'; id: string }
  | { type: 'update-content'; id: string; content: string }
  | { type: 'saved'; id: string; content: string; revision: string }
  | { type: 'update-file'; id: string; changes: Partial<FileTab> }
  | { type: 'reset' }
  | { type: 'reset-files' };

export type DirtyDecision = 'save' | 'discard' | 'cancel';

export function createTabState(): TabState {
  return { tabs: [], activeId: null };
}

export function resolveDirtyDecision(choice: DirtyDecision): { action: DirtyDecision; close: boolean } {
  return { action: choice, close: choice === 'discard' };
}

export function tabsReducer(state: TabState, action: TabAction): TabState {
  switch (action.type) {
    case 'open': {
      const existing = state.tabs.find((tab) => tab.id === action.tab.id);
      return existing
        ? { ...state, activeId: existing.id }
        : { tabs: [...state.tabs, action.tab], activeId: action.tab.id };
    }
    case 'activate':
      return state.tabs.some((tab) => tab.id === action.id) ? { ...state, activeId: action.id } : state;
    case 'close': {
      const index = state.tabs.findIndex((tab) => tab.id === action.id);
      if (index < 0) return state;
      const tabs = state.tabs.filter((tab) => tab.id !== action.id);
      if (state.activeId !== action.id) return { ...state, tabs };
      const next = tabs[index] ?? tabs[index - 1] ?? null;
      return { tabs, activeId: next?.id ?? null };
    }
    case 'update-content':
      return {
        ...state,
        tabs: state.tabs.map((tab) => tab.kind === 'file' && tab.id === action.id
          ? { ...tab, content: action.content, dirty: true }
          : tab),
      };
    case 'saved':
      return {
        ...state,
        tabs: state.tabs.map((tab) => tab.kind === 'file' && tab.id === action.id
          ? { ...tab, content: action.content, revision: action.revision, dirty: false, error: undefined }
          : tab),
      };
    case 'update-file':
      return {
        ...state,
        tabs: state.tabs.map((tab) => tab.kind === 'file' && tab.id === action.id
          ? { ...tab, ...action.changes }
          : tab),
      };
    case 'reset':
      return createTabState();
    case 'reset-files': {
      const tabs = state.tabs.filter((tab) => tab.kind === 'web');
      return { tabs, activeId: tabs.at(-1)?.id ?? null };
    }
    default:
      return state;
  }
}
