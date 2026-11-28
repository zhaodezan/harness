import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api';
import {
  createTabState,
  tabsReducer,
  type FilePreviewKind,
  type FileTab,
  type TabState,
  type WebTab,
  type WorkspaceTab,
} from './workspaceFileTabs';
import './WorkspaceFileDrawer.css';

type FileEntry = {
  name: string;
  path: string;
  kind: 'file' | 'directory';
  size: number | null;
  modified_at: string;
};

type FileListResponse = { path: string; entries: FileEntry[] };
type TextFileResponse = { path: string; content: string; revision: string };
type WorkspaceFileRequest = { path: string; nonce: number };
type WorkspaceWebRequest = { href: string; nonce: number };

type Props = {
  sessionId: string;
  open: boolean;
  executionMode: 'local' | 'sandbox';
  workspacePath?: string | null;
  fileRequest?: WorkspaceFileRequest | null;
  webRequest?: WorkspaceWebRequest | null;
  onClose: () => void;
  onError?: (message: string) => void;
};

const EDITABLE_SUFFIXES = new Set([
  '.txt', '.md', '.rst', '.json', '.yaml', '.yml', '.toml', '.ini', '.csv',
  '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.xml', '.sql',
  '.sh', '.ps1', '.bat', '.c', '.h', '.cpp', '.java', '.go', '.rs', '.rb', '.svg',
]);
const MEDIA_TYPES: Record<string, { kind: FilePreviewKind; mime: string }> = {
  '.png': { kind: 'image', mime: 'image/png' },
  '.jpg': { kind: 'image', mime: 'image/jpeg' },
  '.jpeg': { kind: 'image', mime: 'image/jpeg' },
  '.gif': { kind: 'image', mime: 'image/gif' },
  '.webp': { kind: 'image', mime: 'image/webp' },
  '.pdf': { kind: 'pdf', mime: 'application/pdf' },
  '.mp3': { kind: 'media', mime: 'audio/mpeg' },
  '.wav': { kind: 'media', mime: 'audio/wav' },
  '.mp4': { kind: 'media', mime: 'video/mp4' },
  '.webm': { kind: 'media', mime: 'video/webm' },
};

function extension(path: string): string {
  const dot = path.lastIndexOf('.');
  return dot >= 0 ? path.slice(dot).toLowerCase() : '';
}

function previewKind(path: string): FilePreviewKind {
  const suffix = extension(path);
  if (EDITABLE_SUFFIXES.has(suffix)) return 'text';
  return MEDIA_TYPES[suffix]?.kind ?? 'binary';
}

function fileName(path: string): string {
  return path.split('/').at(-1) || path;
}

function fileUrl(sessionId: string, action: 'preview' | 'download', path: string): string {
  const query = new URLSearchParams({ path });
  return `/api/sessions/${encodeURIComponent(sessionId)}/workspace-files/${action}?${query}`;
}

async function fetchBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `文件预览失败（${response.status}）`);
  }
  return response.blob();
}

function entryFor(path: string, entries: Record<string, FileEntry[]>): FileEntry | undefined {
  const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  return entries[parent]?.find((entry) => entry.path === path);
}

export function WorkspaceFileDrawer({
  sessionId,
  open,
  executionMode,
  workspacePath,
  fileRequest,
  webRequest,
  onClose,
  onError,
}: Props) {
  const [tabs, dispatch] = useReducer(tabsReducer, undefined, createTabState);
  const [entries, setEntries] = useState<Record<string, FileEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['']));
  const [loadingDirectories, setLoadingDirectories] = useState<Set<string>>(new Set());
  const [directoryError, setDirectoryError] = useState('');
  const [dirtyPrompt, setDirtyPrompt] = useState<{ id: string; destination: 'tab' | 'drawer' } | null>(null);
  const [lastFileNonce, setLastFileNonce] = useState(-1);
  const [lastWebNonce, setLastWebNonce] = useState(-1);
  const previousWorkspacePath = useRef(workspacePath);

  const reportError = useCallback((message: string) => {
    setDirectoryError(message);
    onError?.(message);
  }, [onError]);

  const loadDirectory = useCallback(async (path: string, signal?: AbortSignal) => {
    if (!sessionId || executionMode !== 'local') return;
    setLoadingDirectories((current) => new Set(current).add(path));
    try {
      const result = await api<FileListResponse>(`/sessions/${encodeURIComponent(sessionId)}/workspace-files?${new URLSearchParams({ path })}`, { signal });
      if (signal?.aborted) return;
      setEntries((current) => ({ ...current, [path]: result.entries }));
      setDirectoryError('');
    } catch (reason) {
      if (!signal?.aborted) reportError((reason as Error).message);
    } finally {
      if (!signal?.aborted) setLoadingDirectories((current) => {
        const next = new Set(current);
        next.delete(path);
        return next;
      });
    }
  }, [executionMode, reportError, sessionId]);

  useEffect(() => {
    dispatch({ type: 'reset' });
    setEntries({});
    setExpanded(new Set(['']));
    setDirectoryError('');
    setLastFileNonce(-1);
    setLastWebNonce(-1);
    previousWorkspacePath.current = workspacePath;
  }, [sessionId, executionMode]);

  useEffect(() => {
    if (previousWorkspacePath.current === workspacePath) return;
    previousWorkspacePath.current = workspacePath;
    dispatch({ type: 'reset-files' });
    setExpanded(new Set(['']));
    setDirectoryError('');
  }, [workspacePath]);

  useEffect(() => {
    if (!open || executionMode !== 'local' || !sessionId || !workspacePath) return;
    const controller = new AbortController();
    void loadDirectory('', controller.signal);
    return () => controller.abort();
  }, [executionMode, loadDirectory, open, sessionId, workspacePath]);

  const openWeb = useCallback((href: string) => {
    const tab: WebTab = { id: `web:${href}`, kind: 'web', href, title: href };
    dispatch({ type: 'open', tab });
  }, []);

  const openFile = useCallback(async (path: string) => {
    if (!sessionId || executionMode !== 'local') return;
    const known = entryFor(path, entries);
    const kind = previewKind(path);
    const initial: FileTab = {
      id: `file:${path}`,
      kind: 'file',
      path,
      name: fileName(path),
      previewKind: kind,
      content: kind === 'text' ? '' : undefined,
      revision: undefined,
      dirty: false,
      size: known?.size ?? 0,
      modifiedAt: known?.modified_at ?? '',
      mimeType: MEDIA_TYPES[extension(path)]?.mime,
      loading: true,
    };
    dispatch({ type: 'open', tab: initial });
    const controller = new AbortController();
    try {
      if (kind === 'text') {
        const result = await api<TextFileResponse>(`/sessions/${encodeURIComponent(sessionId)}/workspace-files/content?${new URLSearchParams({ path })}`, { signal: controller.signal });
        dispatch({ type: 'update-file', id: initial.id, changes: { content: result.content, revision: result.revision, dirty: false, loading: false, size: new TextEncoder().encode(result.content).length } });
      } else if (kind !== 'binary') {
        const blob = await fetchBlob(fileUrl(sessionId, 'preview', path), controller.signal);
        const blobUrl = URL.createObjectURL(blob);
        dispatch({ type: 'update-file', id: initial.id, changes: { blobUrl, loading: false } });
      } else {
        dispatch({ type: 'update-file', id: initial.id, changes: { loading: false } });
      }
    } catch (reason) {
      if (!controller.signal.aborted) dispatch({ type: 'update-file', id: initial.id, changes: { loading: false, error: (reason as Error).message } });
    }
  }, [entries, executionMode, sessionId]);

  useEffect(() => {
    if (!open || !fileRequest || fileRequest.nonce === lastFileNonce || !workspacePath || executionMode !== 'local') return;
    setLastFileNonce(fileRequest.nonce);
    void openFile(fileRequest.path);
  }, [executionMode, fileRequest, lastFileNonce, open, openFile, workspacePath]);

  useEffect(() => {
    if (!open || !webRequest || webRequest.nonce === lastWebNonce) return;
    setLastWebNonce(webRequest.nonce);
    openWeb(webRequest.href);
  }, [lastWebNonce, open, openWeb, webRequest]);

  const activeTab = tabs.tabs.find((tab) => tab.id === tabs.activeId) ?? null;
  const dirtyTabs = tabs.tabs.filter((tab): tab is FileTab => tab.kind === 'file' && tab.dirty);

  const saveTab = useCallback(async (tab: FileTab): Promise<boolean> => {
    if (tab.content === undefined || !tab.revision) return false;
    try {
      const result = await api<TextFileResponse>(`/sessions/${encodeURIComponent(sessionId)}/workspace-files/content`, {
        method: 'PUT',
        body: JSON.stringify({ path: tab.path, content: tab.content, expected_revision: tab.revision }),
      });
      dispatch({ type: 'saved', id: tab.id, content: result.content, revision: result.revision });
      return true;
    } catch (reason) {
      dispatch({ type: 'update-file', id: tab.id, changes: { error: (reason as Error).message } });
      reportError((reason as Error).message);
      return false;
    }
  }, [reportError, sessionId]);

  const closeTab = (tab: WorkspaceTab) => {
    if (tab.kind === 'file' && tab.dirty) setDirtyPrompt({ id: tab.id, destination: 'tab' });
    else dispatch({ type: 'close', id: tab.id });
  };

  const closeDrawer = () => {
    const dirty = dirtyTabs[0];
    if (dirty) setDirtyPrompt({ id: dirty.id, destination: 'drawer' });
    else onClose();
  };

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      const dirty = tabs.tabs.find((tab): tab is FileTab => tab.kind === 'file' && tab.dirty);
      if (dirty) setDirtyPrompt({ id: dirty.id, destination: 'drawer' });
      else onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, open, tabs.tabs]);

  const resolveDirty = async (choice: 'save' | 'discard' | 'cancel') => {
    if (!dirtyPrompt) return;
    const prompt = dirtyPrompt;
    setDirtyPrompt(null);
    if (choice === 'cancel') return;
    if (choice === 'discard') {
      if (prompt.destination === 'tab') dispatch({ type: 'close', id: prompt.id });
      else { dispatch({ type: 'reset' }); onClose(); }
      return;
    }
    if (prompt.destination === 'tab') {
      const tab = tabs.tabs.find((item): item is FileTab => item.id === prompt.id && item.kind === 'file');
      if (tab && await saveTab(tab)) dispatch({ type: 'close', id: prompt.id });
      return;
    }
    for (const tab of dirtyTabs) {
      if (!await saveTab(tab)) return;
    }
    dispatch({ type: 'reset' });
    onClose();
  };

  const toggleDirectory = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    if (!entries[path]) void loadDirectory(path);
  };

  const renderTree = (directory: string, depth = 0): ReactElement[] => {
    if (!expanded.has(directory)) return [];
    return (entries[directory] ?? []).flatMap((entry) => {
      const row = <button className={`workspace-file-entry ${entry.kind}`} key={entry.path} type="button" style={{ paddingLeft: `${10 + depth * 14}px` }} onClick={() => entry.kind === 'directory' ? toggleDirectory(entry.path) : void openFile(entry.path)}>
        <span className="workspace-file-entry-icon">{entry.kind === 'directory' ? (expanded.has(entry.path) ? '▾' : '▸') : '·'}</span><span>{entry.name}</span>
      </button>;
      return entry.kind === 'directory' ? [row, ...renderTree(entry.path, depth + 1)] : [row];
    });
  };

  const openExternal = (href: string) => { window.open(href, '_blank', 'noopener,noreferrer'); };
  const activeFile = activeTab?.kind === 'file' ? activeTab : null;
  const activeWeb = activeTab?.kind === 'web' ? activeTab : null;
  const loadingRoot = loadingDirectories.has('');
  const canBrowse = executionMode === 'local' && !!workspacePath && !!sessionId;

  return <aside id="workspace-file-drawer" className={`workspace-file-drawer${open ? ' open' : ''}`} aria-label="工作区文件" aria-hidden={!open}>
    <header className="workspace-file-drawer-header">
      <div><strong>工作区文件</strong><small>{canBrowse ? workspacePath : executionMode === 'sandbox' ? '沙箱会话没有可浏览的本机工作区' : '当前会话没有可浏览的工作区'}</small></div>
      <button type="button" aria-label="关闭文件抽屉" onClick={closeDrawer}>×</button>
    </header>
    {!canBrowse && !activeWeb ? <div className="workspace-file-empty">{executionMode === 'sandbox' ? '此会话在 Docker 沙箱中运行，不访问本机文件。' : '请选择本机工作区后查看文件。'}</div> : <>
      {canBrowse && <div className="workspace-file-tree" aria-label="工作区文件列表">
        {loadingRoot && <div className="workspace-file-state">正在加载文件…</div>}
        {directoryError && <div className="workspace-file-state error" role="alert">{directoryError}<button type="button" onClick={() => void loadDirectory('')}>重试</button></div>}
        {!loadingRoot && !directoryError && !entries['']?.length && <div className="workspace-file-state">目录为空</div>}
        {renderTree('')}
      </div>}
      <section className="workspace-file-editor" aria-label="文件预览和编辑">
        <div className="workspace-file-tabs" role="tablist">
          {tabs.tabs.map((tab) => <div className={`workspace-file-tab${tab.id === tabs.activeId ? ' active' : ''}`} key={tab.id}>
            <button type="button" role="tab" aria-selected={tab.id === tabs.activeId} onClick={() => dispatch({ type: 'activate', id: tab.id })}>{tab.kind === 'file' && tab.dirty ? '• ' : ''}{tab.kind === 'file' ? tab.name : tab.title}</button>
            <button type="button" aria-label={`关闭${tab.kind === 'file' ? tab.name : tab.title}`} onClick={() => closeTab(tab)}>×</button>
          </div>)}
        </div>
        {!activeTab && <div className="workspace-file-state editor-empty">选择文件或从聊天消息点击链接以打开。</div>}
        {activeWeb && <div className="workspace-web-preview"><iframe title={activeWeb.title} src={activeWeb.href} sandbox="allow-scripts" referrerPolicy="no-referrer"/><button type="button" className="workspace-file-external" onClick={() => openExternal(activeWeb.href)}>在系统浏览器打开</button></div>}
        {activeFile && <div className="workspace-file-content">
          {activeFile.loading && <div className="workspace-file-state">正在打开…</div>}
          {activeFile.error && <div className="workspace-file-state error" role="alert">{activeFile.error}</div>}
          {!activeFile.loading && !activeFile.error && activeFile.previewKind === 'text' && <>
            <div className="workspace-file-toolbar"><span>{activeFile.dirty ? '未保存' : '已保存'}</span><button type="button" disabled={!activeFile.dirty} onClick={() => void saveTab(activeFile)}>保存</button></div>
            <textarea value={activeFile.content ?? ''} onChange={(event) => dispatch({ type: 'update-content', id: activeFile.id, content: event.target.value })} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void saveTab(activeFile); } }} spellCheck={false}/>
          </>}
          {!activeFile.loading && !activeFile.error && activeFile.previewKind === 'image' && activeFile.blobUrl && <img className="workspace-file-media" src={activeFile.blobUrl} alt={activeFile.name}/>}
          {!activeFile.loading && !activeFile.error && activeFile.previewKind === 'pdf' && activeFile.blobUrl && <iframe className="workspace-file-pdf" title={activeFile.name} src={activeFile.blobUrl}/>}
          {!activeFile.loading && !activeFile.error && activeFile.previewKind === 'media' && activeFile.blobUrl && (activeFile.mimeType?.startsWith('video/') ? <video className="workspace-file-media" controls src={activeFile.blobUrl}/> : <audio className="workspace-file-audio" controls src={activeFile.blobUrl}/>)}
          {!activeFile.loading && !activeFile.error && activeFile.previewKind === 'binary' && <div className="workspace-file-binary"><strong>{activeFile.name}</strong><span>此文件暂不支持内嵌预览。</span><a href={fileUrl(sessionId, 'download', activeFile.path)} download>下载文件</a></div>}
        </div>}
      </section>
    </>}
    {dirtyPrompt && <div className="workspace-file-confirm" role="dialog" aria-modal="true" aria-label="未保存修改"><strong>文件有未保存修改</strong><p>保存后再{dirtyPrompt.destination === 'drawer' ? '关闭抽屉' : '关闭标签'}吗？</p><div><button type="button" onClick={() => void resolveDirty('cancel')}>取消</button><button type="button" onClick={() => void resolveDirty('discard')}>放弃修改</button><button type="button" className="primary-button" onClick={() => void resolveDirty('save')}>保存</button></div></div>}
  </aside>;
}
