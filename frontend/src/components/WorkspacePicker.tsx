import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import './WorkspacePicker.css';

export type WorkspaceChoice = { group_id: string | null; execution_mode: 'local' | 'sandbox' };
type WorkspaceView = WorkspaceChoice & { name: string | null; path: string | null };
type WorkspaceGroup = { id: string; name: string; path: string };
type SandboxStatus = { configured: boolean };

const DEFAULT_CHOICE: WorkspaceChoice = { group_id: null, execution_mode: 'local' };
const ignoreReady = () => {};

class WorkspaceOutcomeUncertain extends Error {
  constructor() {
    super('工作区保存结果不确定，服务端可能仍在写入；此会话已停止发送。请新建本机对话，或联系管理员确认处理；普通重试无法安全恢复。');
  }
}

export function createWorkspaceCoordinator() {
  const pending = new Map<string, Promise<void>>();
  const uncertain = new Set<string>();
  return {
    isUncertain: (sessionId: string) => uncertain.has(sessionId),
    write<T>(sessionId: string, operation: () => Promise<T>): Promise<T> {
      const result = (pending.get(sessionId) ?? Promise.resolve()).then(async () => {
        if (uncertain.has(sessionId)) throw new WorkspaceOutcomeUncertain();
        try { return await operation(); }
        catch (error) {
          // These workspace endpoint errors are raised before its transaction commits.
          // Other responses, including gateway errors, may hide an in-flight upstream write.
          if (error instanceof ApiError && [404, 409, 422].includes(error.status)) throw error;
          uncertain.add(sessionId);
          throw new WorkspaceOutcomeUncertain();
        }
      });
      const settled = result.then(() => {}, () => {});
      pending.set(sessionId, settled);
      void settled.then(() => { if (pending.get(sessionId) === settled) pending.delete(sessionId); });
      return result;
    },
    async wait(sessionId: string) {
      while (pending.has(sessionId)) await pending.get(sessionId);
      if (uncertain.has(sessionId)) throw new WorkspaceOutcomeUncertain();
    },
  };
}

const workspaceCoordinator = createWorkspaceCoordinator();

export function createWorkspaceGeneration() {
  let current = 0;
  return { advance: () => ++current, snapshot: () => current, isCurrent: (value: number) => value === current,
    capture: () => { const value = current; return () => value === current; } };
}

type Props = {
  sessionId: string;
  disabled: boolean;
  onChoice: (choice: WorkspaceChoice) => void;
  onError: (message: string) => void;
  onReady?: (sessionId: string, ready: boolean) => void;
};

export function WorkspacePicker({ sessionId, disabled, onChoice, onError, onReady = ignoreReady }: Props) {
  const [groups, setGroups] = useState<WorkspaceGroup[]>([]);
  const [choice, setChoice] = useState<WorkspaceChoice>(DEFAULT_CHOICE);
  const [workspace, setWorkspace] = useState<WorkspaceView | null>(null);
  const [groupName, setGroupName] = useState('');
  const [folderPath, setFolderPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [loadedSessionId, setLoadedSessionId] = useState('');
  const [loadedGeneration, setLoadedGeneration] = useState(-1);
  const [workspaceLoadError, setWorkspaceLoadError] = useState('');
  const [workspaceRetry, setWorkspaceRetry] = useState(0);
  const currentSession = useRef(sessionId);
  const generation = useRef(createWorkspaceGeneration());
  if (currentSession.current !== sessionId) generation.current.advance();
  currentSession.current = sessionId;
  const workspaceReady = !sessionId || (loadedSessionId === sessionId && loadedGeneration === generation.current.snapshot() && !loadingWorkspace && !workspaceLoadError);
  const [pickerAvailable, setPickerAvailable] = useState(true);
  const applyInFlight = useRef(false);
  const [sandboxConfigured, setSandboxConfigured] = useState(false);
  const [sandboxStatusLoading, setSandboxStatusLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    api<WorkspaceGroup[]>('/workspaces', { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setGroups(items); })
      .catch((error: Error) => { if (!controller.signal.aborted) onError(error.message); });
    return () => controller.abort();
  }, [onError]);

  useEffect(() => {
    const controller = new AbortController();
    setSandboxStatusLoading(true);
    api<SandboxStatus>('/sandbox', { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        setSandboxConfigured(value.configured === true);
        setSandboxStatusLoading(false);
      })
      .catch((reason: Error) => {
        if (controller.signal.aborted) return;
        setSandboxConfigured(false);
        setSandboxStatusLoading(false);
        onError(`无法检查本地 Docker 沙箱状态，请确认 Docker Desktop 已安装并运行，或先在插件库配置本地 Docker。${reason.message ? ` ${reason.message}` : ''}`);
      });
    return () => controller.abort();
  }, [onError]);

  useEffect(() => {
    const token = generation.current.advance();
    const isCurrent = () => generation.current.isCurrent(token) && currentSession.current === sessionId;
    setBusy(false);
    applyInFlight.current = false;
    setWorkspace(null);
    setLoadedSessionId('');
    setWorkspaceLoadError('');
    if (!sessionId) {
      setLoadingWorkspace(false);
      onReady(sessionId, true);
      return () => { if (isCurrent()) generation.current.advance(); };
    }
    const controller = new AbortController();
    onReady(sessionId, false);
    setLoadingWorkspace(true);
    workspaceCoordinator.wait(sessionId)
      .then(() => {
        if (controller.signal.aborted || !isCurrent()) return null;
        return api<WorkspaceView>(`/sessions/${sessionId}/workspace`, { signal: controller.signal });
      })
      .then((value) => {
        if (!value || controller.signal.aborted || !isCurrent()) return;
        setWorkspace(value);
        setChoice({ group_id: value.group_id, execution_mode: value.execution_mode });
        onChoice({ group_id: value.group_id, execution_mode: value.execution_mode });
        setLoadedSessionId(sessionId);
        setLoadedGeneration(token);
        onError('');
        onReady(sessionId, true);
      })
      .catch((error: Error) => {
        if (controller.signal.aborted || !isCurrent()) return;
        const message = error instanceof WorkspaceOutcomeUncertain ? error.message : `无法加载此会话的工作区，请检查 Docker 配置后重试：${error.message}`;
        setWorkspaceLoadError(message);
        onError(message);
      })
      .finally(() => { if (!controller.signal.aborted && isCurrent()) setLoadingWorkspace(false); });
    return () => { controller.abort(); if (isCurrent()) generation.current.advance(); };
  }, [sessionId, onChoice, onError, onReady, workspaceRetry]);

  const apply = async (next: WorkspaceChoice) => {
    if (applyInFlight.current || !workspaceReady || currentSession.current !== sessionId) return;
    const previousChoice = choice;
    const token = generation.current.snapshot();
    const isCurrent = () => generation.current.isCurrent(token) && currentSession.current === sessionId;
    applyInFlight.current = true;
    setChoice(next);
    onChoice(next);
    if (!sessionId) {
      applyInFlight.current = false;
      return;
    }
    setBusy(true);
    onReady(sessionId, false);
    try {
      const value = await workspaceCoordinator.write(sessionId, () => api<WorkspaceView>(`/sessions/${sessionId}/workspace`, {
        method: 'PUT', body: JSON.stringify(next),
      }));
      if (!isCurrent()) return;
      setWorkspace(value);
      onError('');
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof WorkspaceOutcomeUncertain) {
        setLoadedSessionId('');
        setWorkspaceLoadError(reason.message);
        onError(reason.message);
      } else {
        setChoice(previousChoice);
        onChoice(previousChoice);
        onError((reason as Error).message);
      }
    } finally {
      if (isCurrent()) {
        applyInFlight.current = false;
        if (!workspaceCoordinator.isUncertain(sessionId)) onReady(sessionId, true);
        setBusy(false);
      }
    }
  };

  const createGroup = async (payload: { name?: string; path?: string }) => {
    const generationIsCurrent = generation.current.capture();
    const isCurrent = () => generationIsCurrent() && currentSession.current === sessionId;
    if (!isCurrent() || !workspaceReady) return;
    setBusy(true);
    try {
      const group = await api<WorkspaceGroup>('/workspaces', { method: 'POST', body: JSON.stringify(payload) });
      if (!isCurrent()) return;
      setGroups((items) => [group, ...items.filter((item) => item.id !== group.id)]);
      setFolderPath(group.path);
      await apply({ ...choice, group_id: group.id });
      if (isCurrent()) setGroupName('');
    } catch (reason) { if (isCurrent()) onError((reason as Error).message); }
    finally { if (isCurrent()) setBusy(false); }
  };

  const browse = async () => {
    const generationIsCurrent = generation.current.capture();
    const isCurrent = () => generationIsCurrent() && currentSession.current === sessionId;
    if (!isCurrent() || !workspaceReady) return;
    setBusy(true);
    try {
      const picked = await api<{ path: string }>('/workspaces/pick-directory');
      if (!isCurrent()) return;
      setFolderPath(picked.path);
      await createGroup({ path: picked.path });
    } catch (reason) {
      if (!isCurrent()) return;
      const message = (reason as Error).message;
      setPickerAvailable(false);
      onError(message);
    } finally { if (isCurrent()) setBusy(false); }
  };

  return <section className="workspace-picker" aria-label="会话分组和代码执行位置">
    <label className="workspace-picker-label" htmlFor="session-workspace-group">分组 / 运行目录</label>
    <div className="workspace-picker-row">
      <select id="session-workspace-group" value={workspaceReady ? choice.group_id ?? '' : ''} disabled={disabled || busy || !workspaceReady}
        onChange={(event) => void apply({ ...choice, group_id: event.target.value || null })}>
        <option value="">默认 · {workspace?.group_id ? '此会话目录' : workspace?.name || '新会话目录'}</option>
        {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
      </select>
      <select className="workspace-picker-execution" aria-label="代码运行位置" value={workspaceReady ? choice.execution_mode : ''} disabled={disabled || busy || !workspaceReady}
        onChange={(event) => void apply({ ...choice, execution_mode: event.target.value as WorkspaceChoice['execution_mode'] })}>
        {!workspaceReady && <option value="">工作区尚未加载</option>}
        <option value="local">本机 PowerShell</option>
        <option value="sandbox" disabled={sandboxStatusLoading || !sandboxConfigured}>本地 Docker 沙箱</option>
      </select>
    </div>
    <div className="workspace-picker-row">
      <input aria-label="新建分组名称" value={groupName} onChange={(event) => setGroupName(event.target.value)}
        placeholder="新建分组名称" disabled={disabled || busy || !workspaceReady}/>
      <button type="button" disabled={disabled || busy || !workspaceReady || !groupName.trim()} onClick={() => void createGroup({ name: groupName })}>新建分组</button>
    </div>
    <div className="workspace-picker-row">
      <input aria-label="本地文件夹路径" value={folderPath} onChange={(event) => setFolderPath(event.target.value)}
        placeholder="选择或粘贴本地文件夹路径" disabled={disabled || busy || !workspaceReady}/>
      {pickerAvailable && <button type="button" disabled={disabled || busy || !workspaceReady} onClick={() => void browse()}>浏览文件夹</button>}
      <button type="button" disabled={disabled || busy || !workspaceReady || !folderPath.trim()} onClick={() => void createGroup({ path: folderPath.trim() })}>使用此文件夹</button>
    </div>
    {workspaceLoadError && <div role="alert">{workspaceLoadError} {!workspaceCoordinator.isUncertain(sessionId) && <button type="button" disabled={disabled || busy} onClick={() => { onReady(sessionId, false); setWorkspaceRetry((n) => n + 1); }}>重试加载工作区</button>}</div>}
    <small className="workspace-picker-hint">{workspaceReady ? (workspace?.name ? `${workspace.name}${workspace?.path ? ` · ${workspace.path}` : ''}` : choice.execution_mode === 'sandbox' ? '本地 Docker 沙箱不使用本机工作目录。' : '未选择分组时，为此会话创建独立的时间戳文件夹。') : '工作区尚未加载。'}{!sandboxStatusLoading && !sandboxConfigured ? ' · 本地 Docker 沙箱未配置，请先在插件库配置并确认 Docker。' : ''}{workspaceReady && (choice.execution_mode === 'local' ? ' · 本机 PowerShell 不是安全沙箱；权限由执行权限选择控制。' : ' · 本地 Docker 沙箱不访问本机文件。')}</small>
  </section>;
}
