import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { Link, matchPath, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { parseEnvironmentLines } from './environment';
import { api } from './api';
import { submitChatOnEnter } from './chatKeyboard';
import { createTypewriter } from './typewriter';
import { RunTracePanel } from './components/RunTracePanel';
import { ChatImages } from './components/ChatImages';
import type { ChatImage } from './components/ChatImages';
import { groupRunTraces, isTerminalTrace, mergeRunTraces } from './runTrace';
import type { RunTrace, SessionTrace } from './runTrace';
import { ApprovalModePicker, ChatCapabilitiesPicker, ChatModelPicker, DEFAULT_APPROVAL_MODE, chatRequestOptions, defaultCapabilities, normalizeApprovalMode } from './ChatComposerOptions';
import type { ChatOptions, ChatPreference, ModelOption } from './ChatComposerOptions';
import { AgentListPage } from './pages/AgentListPage';
import { AgentConfigPage } from './pages/AgentConfigPage';
import { PluginLibraryPage } from './pages/PluginLibraryPage';
import { KnowledgeBasePage } from './pages/KnowledgeBasePage';
import { DatabasePage } from './pages/DatabasePage';
import { ScheduledTasksPage } from './pages/ScheduledTasksPage';
import { WorkspacePicker } from './components/WorkspacePicker';
import type { WorkspaceChoice, WorkspaceView } from './components/WorkspacePicker';
import { WorkspaceFileDrawer } from './components/WorkspaceFileDrawer';
import { MessageContent } from './ChartMessageContent';

type Agent = { id: string; name: string; model: string | null; model_config_name?: string | null; mode: string; base_url?: string; system_prompt?: string; context_window?: number; max_rounds?: number; skill_directory?: string; selected_skills?: string[]; prompt_variables?: Record<string, string>; input_rate_per_million?: number | null; output_rate_per_million?: number | null };
type PromptVersion = { id: string; content: string; created_at: string };
type Session = { id: string; agent_id: string | null; title: string };
type Message = { id: string; role: string; content: string; run_id?: string; plan?: string; images?: ChatImage[] };
type SearchHit = { session_id: string; agent_id: string | null; title: string; matched_message: string | null };
type Draft = { text: string; branchMessageId: string };
type Memory = { id: string; content: string; scope: string; agent_id: string | null };
type PendingApproval = { approval_id: string; tool_name: string; description: string; arguments: Record<string, unknown>; execution_mode?: 'local' | 'sandbox'; working_directory?: string };
type LogEvent = { id: number; occurred_at: string; run_id: string; session_id: string | null; level: string; subsystem: string; event_type: string; status: string; duration_ms: number | null; summary: string; input_tokens: number | null; output_tokens: number | null; estimated_cost: number | null; cost_currency: string | null };
type ContextSummary = { id: string; run_id: string; summary: string; summarized_message_count: number; created_at: string };
type MCPTool = { name: string; description: string; risk: string };
type MCPTransport = 'stdio' | 'sse' | 'streamable_http';
type MCPServer = { id: string; name: string; transport: string; url: string; command: string; args: string[]; cwd: string; connected: boolean };
type SkillSummary = { name: string; description: string };
type StartupStatus = { completed: boolean; agent: { id: string; name: string; model: string | null } | null };
type DiagnosticCheck = { name: string; ok: boolean; detail: string };
type ImportPreview = { payload: Record<string, unknown>; agents: number; sessions: number; messages: number; memories: number; mcp_servers: number; mcp_name_conflicts: string[]; warnings: string[] };

const EMPTY_DRAFT: Draft = { text: '', branchMessageId: '' };
const CHAT_PREFERENCES_KEY = 'agent-harness:chat-preferences:v1';

function useChatTabState<T>(key: string) {
  const [value, setValue] = useState<Record<string, T>>(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(key) ?? '{}');
      return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    } catch { return {}; }
  });
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify(value)); }
    catch { /* Navigation still preserves the state if browser storage is unavailable. */ }
  }, [key, value]);
  return [value, setValue] as const;
}

function App() {
  const { pathname } = useLocation();
  const agentId = matchPath('/agents/:agentId/chat', pathname)?.params.agentId;
  const scopeKey = agentId ? `agent:${agentId}` : 'workspace';
  const [selectedSessions, setSelectedSessions] = useChatTabState<string>('agent-harness:selected-sessions:v1');
  const [drafts, setDrafts] = useChatTabState<Draft>('agent-harness:chat-drafts:v1');
  const [composerByChat, setComposerByChat] = useChatTabState<ChatPreference>(CHAT_PREFERENCES_KEY);
  return <ScopedApp key={scopeKey} agentId={agentId} sessionId={selectedSessions[scopeKey] ?? ''}
    setSessionId={(update) => setSelectedSessions((previous) => {
      const next = typeof update === 'function' ? update(previous[scopeKey] ?? '') : update;
      return next === (previous[scopeKey] ?? '') ? previous : { ...previous, [scopeKey]: next };
    })} drafts={drafts} setDrafts={setDrafts} composerByChat={composerByChat} setComposerByChat={setComposerByChat}/>;
}

type ScopedAppProps = {
  agentId?: string;
  sessionId: string;
  setSessionId: Dispatch<SetStateAction<string>>;
  drafts: Record<string, Draft>;
  setDrafts: Dispatch<SetStateAction<Record<string, Draft>>>;
  composerByChat: Record<string, ChatPreference>;
  setComposerByChat: Dispatch<SetStateAction<Record<string, ChatPreference>>>;
};

function ScopedApp({ agentId, sessionId, setSessionId, drafts, setDrafts, composerByChat, setComposerByChat }: ScopedAppProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const chatPath = agentId ? `/agents/${encodeURIComponent(agentId)}/chat` : '/chat';
  const isChatPage = !!agentId || !!matchPath('/chat', location.pathname);
  const sessionScope = agentId ? `agent_id=${encodeURIComponent(agentId)}` : 'workspace=true';
  const memoryScope = agentId ? `scope=agent&${sessionScope}` : 'scope=global';
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const activeAgent = agents.find((agent) => agent.id === agentId);
  useEffect(() => {
    if (!agentId) return;
    const previousTitle = document.title;
    document.title = `${activeAgent?.name || 'Agent'} · 专属对话`;
    return () => { document.title = previousTitle; };
  }, [agentId, activeAgent?.name]);
  const canChat = !agentId || !!activeAgent;
  const [sessions, setSessions] = useState<Session[]>([]);
  const sessionListRequest = useRef(0);
  const preparedSession = useRef('');
  const [loadedHistoryId, setLoadedHistoryId] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [historyRetry, setHistoryRetry] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [contextSummaries, setContextSummaries] = useState<ContextSummary[]>([]);
  const [liveTrace, setLiveTrace] = useState<SessionTrace | null>(null);
  const [savedTrace, setSavedTrace] = useState<(SessionTrace & { loading: boolean; error: boolean }) | null>(null);
  const [traceRefresh, setTraceRefresh] = useState(0);
  const currentTrace = savedTrace?.sessionId === sessionId ? savedTrace : null;
  const tracesByRun = groupRunTraces(mergeRunTraces(sessionId, currentTrace?.events ?? [], liveTrace));
  const [sessionSearch, setSessionSearch] = useState('');
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [catalogState, setCatalogState] = useState<{ data?: ChatOptions; loading: boolean; error: string }>();
  const [modelState, setModelState] = useState({ data: [] as ModelOption[], loading: true, error: '' });
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [modelRefresh, setModelRefresh] = useState(0);
  const composerPrefix = agentId ?? 'workspace';
  const composerKey = `${composerPrefix}:${sessionId || 'draft'}`;
  const { text: draft, branchMessageId } = drafts[composerKey] ?? EMPTY_DRAFT;
  const updateDraft = (change: Partial<Draft>) => setDrafts((previous) => ({
    ...previous, [composerKey]: { ...(previous[composerKey] ?? EMPTY_DRAFT), ...change },
  }));
  const setDraft = (text: string) => updateDraft({ text });
  const setBranchMessageId = (id: string) => updateDraft({ branchMessageId: id });
  const preference = composerByChat[composerKey];
  const approvalMode = normalizeApprovalMode(preference?.approval_mode ?? DEFAULT_APPROVAL_MODE);
  const chatCatalog = catalogState?.data;
  const capabilitySelection = preference?.capabilities ?? (chatCatalog && defaultCapabilities(chatCatalog));
  const updateComposer = (change: ChatPreference) => {
    setComposerByChat((previous) => ({ ...previous, [composerKey]: { ...previous[composerKey], ...change } }));
  };
  const copyComposerToSession = (id: string) => {
    if (!preference) return;
    setComposerByChat((previous) => ({ ...previous, [`${composerPrefix}:${id}`]: preference }));
  };
  const [busy, setBusy] = useState(false);
  const activeController = useRef<AbortController | null>(null);
  const [creatingSession, setCreatingSession] = useState(false);
  const [error, setError] = useState('');
  const [workspaceChoice, setWorkspaceChoice] = useState<WorkspaceChoice>({ group_id: null, execution_mode: 'local' });
  const workspaceEpoch = useRef({ sessionId, value: 0 });
  if (workspaceEpoch.current.sessionId !== sessionId) workspaceEpoch.current = { sessionId, value: workspaceEpoch.current.value + 1 };
  const epoch = workspaceEpoch.current.value;
  const [workspaceStatus, setWorkspaceStatus] = useState<{ sessionId: string; ready: boolean; epoch: number }>({ sessionId: '', ready: true, epoch: 0 });
  const reportWorkspaceChoice = useCallback((choice: WorkspaceChoice) => setWorkspaceChoice(choice), []);
  const reportWorkspaceReady = useCallback((id: string, ready: boolean) => setWorkspaceStatus({ sessionId: id, ready, epoch }), [epoch]);
  const reportWorkspaceError = useCallback((message: string) => setError(message), []);
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView | null>(null);
  const [fileDrawerOpen, setFileDrawerOpen] = useState(false);
  const [fileRequest, setFileRequest] = useState<{ path: string; nonce: number } | null>(null);
  const [webRequest, setWebRequest] = useState<{ href: string; nonce: number } | null>(null);
  const fileRequestNonce = useRef(0);
  const webRequestNonce = useRef(0);
  const reportWorkspaceView = useCallback((view: WorkspaceView | null) => setWorkspaceView(view), []);
  const openWorkspaceFile = useCallback((path: string) => {
    if (!sessionId || workspaceView?.execution_mode !== 'local' || !workspaceView.path) {
      setError('当前会话没有可浏览的本机工作区。');
      return;
    }
    const nonce = ++fileRequestNonce.current;
    setFileDrawerOpen(true);
    setFileRequest({ path, nonce });
  }, [sessionId, workspaceView]);
  const openWebLink = useCallback((href: string) => {
    const nonce = ++webRequestNonce.current;
    setFileDrawerOpen(true);
    setWebRequest({ href, nonce });
  }, []);
  useEffect(() => {
    setWorkspaceView(null);
    setFileDrawerOpen(false);
    setFileRequest(null);
    setWebRequest(null);
  }, [sessionId]);
  const [showSettings, setShowSettings] = useState(false);
  const [setupStep, setSetupStep] = useState(1);
  const [editingAgentId, setEditingAgentId] = useState('');
  const [promptVersions, setPromptVersions] = useState<PromptVersion[]>([]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [promptVariables, setPromptVariables] = useState<Record<string, string>>({});
  const [promptPreview, setPromptPreview] = useState('');
  const [skillDirectory, setSkillDirectory] = useState('');
  const [skillChoices, setSkillChoices] = useState<SkillSummary[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [showMemory, setShowMemory] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [showDataTransfer, setShowDataTransfer] = useState(false);
  const [dataTransferStatus, setDataTransferStatus] = useState('');
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [showMcp, setShowMcp] = useState(false);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [mcpTools, setMcpTools] = useState<Record<string, MCPTool[]>>({});
  const [mcpName, setMcpName] = useState('');
  const [mcpTransport, setMcpTransport] = useState<MCPTransport>('stdio');
  const [mcpCommand, setMcpCommand] = useState('');
  const [mcpArgs, setMcpArgs] = useState('');
  const [mcpCwd, setMcpCwd] = useState('');
  const [mcpEnvText, setMcpEnvText] = useState('');
  const [mcpUrl, setMcpUrl] = useState('');
  const [mcpBearerToken, setMcpBearerToken] = useState('');
  const [logQuery, setLogQuery] = useState('');
  const [logLevel, setLogLevel] = useState('');
  const [logSubsystem, setLogSubsystem] = useState('');
  const [logSince, setLogSince] = useState('');
  const [logUntil, setLogUntil] = useState('');
  const [logEvents, setLogEvents] = useState<LogEvent[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoryDraft, setMemoryDraft] = useState('');
  const [memoryQuery, setMemoryQuery] = useState('');
  const [editingMemoryId, setEditingMemoryId] = useState('');
  const [memoryEdit, setMemoryEdit] = useState('');
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [agentName, setAgentName] = useState('灵感伙伴');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [contextWindow, setContextWindow] = useState(32768);
  const [inputRate, setInputRate] = useState('');
  const [outputRate, setOutputRate] = useState('');
  const [mode, setMode] = useState<'react' | 'plan_execute'>('react');
  const [apiKey, setApiKey] = useState('');
  const [connectionStatus, setConnectionStatus] = useState('');
  const [startupStatus, setStartupStatus] = useState<StartupStatus | null>(null);
  const [diagnosticChecks, setDiagnosticChecks] = useState<DiagnosticCheck[]>([]);

  const loadAgents = async (signal?: AbortSignal) => {
    const list = await api<Agent[]>('/agents', { signal });
    if (signal?.aborted) return;
    setAgents(list);
    setAgentsLoaded(true);
  };

  const loadSessions = async (signal?: AbortSignal) => {
    const request = ++sessionListRequest.current;
    const list = await api<Session[]>(`/sessions?${sessionScope}`, { signal });
    if (signal?.aborted || request !== sessionListRequest.current) return;
    setSessions(list);
    setSessionId((current) => list.some((item) => item.id === current) ? current : list[0]?.id ?? '');
  };

  const loadMemories = async (signal?: AbortSignal) => {
    const list = await api<Memory[]>(`/memories?${memoryScope}&q=${encodeURIComponent(memoryQuery)}`, { signal });
    if (!signal?.aborted) setMemories(list);
  };

  const saveMemory = async (event: FormEvent) => {
    event.preventDefault();
    if (!memoryDraft.trim()) return;
    try {
      await api('/memories', { method: 'POST', body: JSON.stringify({ content: memoryDraft.trim(), scope: agentId ? 'agent' : 'global', agent_id: agentId || null, source: 'user_confirmed' }) });
      setMemoryDraft(''); await loadMemories();
    } catch (e) { setError((e as Error).message); }
  };

  const deleteSession = async (session: Session) => {
    if (!window.confirm(`删除对话「${session.title}」？此操作无法撤销。`)) return;
    try {
      await api(`/sessions/${session.id}`, { method: 'DELETE' });
      setSessions((items) => items.filter((item) => item.id !== session.id));
      if (sessionId === session.id) { setSessionId(''); setMessages([]); }
      await loadSessions();
    } catch (e) { setError((e as Error).message); }
  };

  const renameSession = async (session: Session) => {
    const title = window.prompt('输入新的对话名称', session.title);
    if (title === null || !title.trim()) return;
    try {
      await api(`/sessions/${session.id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim() }) });
      setSessions((items) => items.map((item) => item.id === session.id ? { ...item, title: title.trim() } : item));
    } catch (e) { setError((e as Error).message); }
  };

  const deleteMemory = async (id: string) => {
    try { await api(`/memories/${id}`, { method: 'DELETE' }); await loadMemories(); }
    catch (e) { setError((e as Error).message); }
  };

  const updateMemory = async (id: string) => {
    try { await api(`/memories/${id}`, { method: 'PATCH', body: JSON.stringify({ content: memoryEdit }) }); setEditingMemoryId(''); setMemoryEdit(''); await loadMemories(); }
    catch (e) { setError((e as Error).message); }
  };

  const clearMemories = async () => {
    if (!window.confirm(agentId ? '确定清空此智能体的专属记忆？此操作无法撤销。' : '确定清空全局长期记忆？此操作无法撤销。')) return;
    try { await api(`/memories?${memoryScope}`, { method: 'DELETE' }); await loadMemories(); }
    catch (e) { setError((e as Error).message); }
  };

  const decideApproval = async (approved: boolean) => {
    if (!pendingApproval) return;
    try { await api(`/approvals/${pendingApproval.approval_id}`, { method: 'POST', body: JSON.stringify({ approved }) }); setPendingApproval(null); }
    catch (e) { setError((e as Error).message); }
  };

  const loadMcpServers = async () => {
    const servers = await api<MCPServer[]>('/mcp/servers'); setMcpServers(servers);
    const discovered: Record<string, MCPTool[]> = {};
    for (const server of servers.filter((item) => item.connected)) discovered[server.id] = await api<MCPTool[]>(`/mcp/servers/${server.id}/tools`).catch(() => []);
    setMcpTools(discovered);
  };

  const createMcpServer = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const env = mcpTransport === 'stdio' ? parseEnvironmentLines(mcpEnvText) : {};
      await api('/mcp/servers', { method: 'POST', body: JSON.stringify({ name: mcpName, transport: mcpTransport, url: mcpTransport === 'stdio' ? '' : mcpUrl, command: mcpTransport === 'stdio' ? mcpCommand : '', args: mcpTransport === 'stdio' ? mcpArgs.split(/\s+/).filter(Boolean) : [], cwd: mcpCwd, bearer_token: mcpTransport === 'stdio' ? '' : mcpBearerToken, env: mcpTransport === 'stdio' ? env : {} }) });
      setMcpName(''); setMcpCommand(''); setMcpArgs(''); setMcpCwd(''); setMcpEnvText(''); setMcpUrl(''); setMcpBearerToken(''); await loadMcpServers();
    } catch (e) { setError((e as Error).message); }
  };

  const connectMcp = async (server: MCPServer) => {
    const target = server.transport === 'stdio' ? `${server.command} ${server.args.join(' ')}` : server.url;
    if (!window.confirm(`即将连接 MCP Server：\n\n${target}\n\n请确认这是你信任的服务。`)) return;
    try { await api(`/mcp/servers/${server.id}/connect`, { method: 'POST', body: JSON.stringify({ confirmed: true }) }); await loadMcpServers(); }
    catch (e) { setError((e as Error).message); }
  };

  const disconnectMcp = async (server: MCPServer) => {
    try { await api(`/mcp/servers/${server.id}/disconnect`, { method: 'POST' }); await loadMcpServers(); }
    catch (e) { setError((e as Error).message); }
  };

  const deleteMcp = async (server: MCPServer) => {
    if (!window.confirm(`删除 MCP Server「${server.name}」？`)) return;
    try { await api(`/mcp/servers/${server.id}`, { method: 'DELETE' }); await loadMcpServers(); }
    catch (e) { setError((e as Error).message); }
  };

  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activeController.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!canChat || !isChatPage) return;
    const controller = new AbortController();
    setCatalogState((previous) => ({ data: previous?.data, loading: true, error: '' }));
    const path = agentId ? `/agents/${encodeURIComponent(agentId)}/chat-options` : '/sessions/chat-options';
    api<ChatOptions>(path, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setCatalogState({ data, loading: false, error: '' }); })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setCatalogState((previous) => ({ data: previous?.data, loading: false, error: reason.message }));
      });
    return () => controller.abort();
  }, [agentId, canChat, isChatPage, catalogRefresh]);
  useEffect(() => {
    if (!canChat || !isChatPage) return;
    const controller = new AbortController();
    setModelState((previous) => ({ ...previous, loading: true, error: '' }));
    api<ModelOption[]>('/models', { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setModelState({ data, loading: false, error: '' }); })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setModelState((previous) => ({ ...previous, loading: false, error: reason.message }));
      });
    return () => controller.abort();
  }, [canChat, isChatPage, modelRefresh]);
  useEffect(() => {
    const controller = new AbortController();
    loadAgents(controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!canChat) return;
    const controller = new AbortController();
    loadSessions(controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [canChat, sessionScope]);
  useEffect(() => {
    const query = sessionSearch.trim();
    setSearchHits([]);
    if (!canChat || !query) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<SearchHit[]>(`/sessions/search?${sessionScope}&q=${encodeURIComponent(query)}`, { signal: controller.signal })
        .then((hits) => { if (!controller.signal.aborted) setSearchHits(hits); })
        .catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [canChat, sessionScope, sessionSearch]);
  useEffect(() => {
    setContextSummaries([]);
    setHistoryError('');
    if (!canChat || !sessionId) { setMessages([]); setLoadedHistoryId(''); return; }
    const controller = new AbortController();
    if (preparedSession.current === sessionId) preparedSession.current = '';
    else api<Message[]>(`/sessions/${sessionId}/messages`, { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) { setMessages(items); setLoadedHistoryId(sessionId); } })
      .catch((e: Error) => { if (!controller.signal.aborted) { setHistoryError(e.message); setError(e.message); } });
    api<ContextSummary[]>(`/sessions/${sessionId}/summaries`, { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setContextSummaries(items); })
      .catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [canChat, sessionId, historyRetry]);
  useEffect(() => {
    if (!canChat || !isChatPage || !sessionId) return;
    const controller = new AbortController();
    const { signal } = controller;
    setSavedTrace((previous) => ({ sessionId, events: previous?.sessionId === sessionId ? previous.events : [], loading: true, error: false }));
    api<RunTrace[]>(`/logs?session_id=${encodeURIComponent(sessionId)}`, { signal })
      .then((items) => { if (!signal.aborted) setSavedTrace({ sessionId, events: items.reverse(), loading: false, error: false }); })
      .catch(() => {
        if (!signal.aborted) setSavedTrace((previous) => ({ sessionId, events: previous?.sessionId === sessionId ? previous.events : [], loading: false, error: true }));
      });
    return () => controller.abort();
  }, [canChat, isChatPage, sessionId, traceRefresh]);
  useEffect(() => {
    if (!showMemory) return;
    const controller = new AbortController();
    loadMemories(controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [showMemory, memoryScope, memoryQuery]);
  useEffect(() => { if (showMcp) loadMcpServers().catch((e: Error) => setError(e.message)); }, [showMcp]);
  useEffect(() => {
    if (!showLogs) return;
    const timer = window.setTimeout(() => api<LogEvent[]>(`/logs?q=${encodeURIComponent(logQuery)}&level=${encodeURIComponent(logLevel)}&subsystem=${encodeURIComponent(logSubsystem)}&since=${encodeURIComponent(logSince)}&until=${encodeURIComponent(logUntil ? `${logUntil}T23:59:59` : '')}&limit=300`).then(setLogEvents).catch((e: Error) => setError(e.message)), 200);
    return () => window.clearTimeout(timer);
  }, [showLogs, logQuery, logLevel, logSubsystem, logSince, logUntil]);

  const openAgentSettings = (id = agentId) => {
    navigate(id ? `/agents/${encodeURIComponent(id)}/config` : '/agents/new');
  };

  const selectSession = (id: string) => {
    if (busy || creatingSession) return;
    if (id !== sessionId) {
      setMessages([]);
      setContextSummaries([]);
      setError('');
      setLoadedHistoryId('');
      setSessionId(id);
    }
    if (!isChatPage) navigate(chatPath);
  };

  const deleteAgent = async (agent: Agent) => {
    const warning = `删除本地智能体「${agent.name}」？此操作无法撤销，并会同时删除该智能体的会话、消息、专属记忆和 Prompt 版本。`;
    if (!window.confirm(warning)) return;
    setError('');
    try {
      await api(`/agents/${agent.id}`, { method: 'DELETE' });
      setAgents((items) => items.filter((item) => item.id !== agent.id));
      setCatalogRefresh((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const saveAgentToList = (saved: Agent, isNew: boolean) => {
    setAgents((items) => isNew
      ? [saved, ...items.filter((item) => item.id !== saved.id)]
      : items.map((item) => item.id === saved.id ? saved : item));
  };

  const restorePrompt = async (versionId: string) => {
    if (!editingAgentId) return;
    try { const updated = await api<Agent>(`/agents/${editingAgentId}/prompts/${versionId}/restore`, { method: 'POST' }); setSystemPrompt(updated.system_prompt ?? ''); setAgents((items) => items.map((agent) => agent.id === updated.id ? updated : agent)); setPromptVersions(await api<PromptVersion[]>(`/agents/${editingAgentId}/prompts`)); }
    catch (e) { setError((e as Error).message); }
  };

  const previewPrompt = async () => {
    if (!editingAgentId) { setError('先保存智能体设置，再预览 Prompt。'); return; }
    try { const result = await api<{ rendered_prompt: string }>(`/agents/${editingAgentId}/prompts/preview`, { method: 'POST', body: JSON.stringify({ variables: promptVariables }) }); setPromptPreview(result.rendered_prompt); }
    catch (e) { setError((e as Error).message); }
  };

  const probeSetupConnection = async () => {
    setError(''); setConnectionStatus('正在测试模型连接…');
    try {
      await api('/setup/test-connection', { method: 'POST', body: JSON.stringify({ base_url: baseUrl, model, api_key: apiKey }) });
      setConnectionStatus('连接成功。下一步设置智能体。');
      setSetupStep(2);
    } catch (e) { setConnectionStatus(''); setError((e as Error).message); }
  };

  const applyAgentTemplate = (template: 'assistant' | 'planner') => {
    setAgentName(template === 'assistant' ? '日常助手' : '计划助手');
    setMode(template === 'assistant' ? 'react' : 'plan_execute');
    setSystemPrompt(template === 'assistant' ? '你是一位清晰、友好、可靠的中文助手。先理解用户需求，再用简洁、可执行的方式回答。' : '你是一位善于规划并推进任务的助手。先把目标分解为清晰步骤，然后依次执行；遇到变化时及时调整计划，并说明进度。');
    setEditingAgentId(''); setPromptVersions([]);
  };

  const discoverSkills = async () => {
    if (!skillDirectory.trim()) return;
    try { const skills = await api<SkillSummary[]>(`/skills/discover?directory=${encodeURIComponent(skillDirectory.trim())}`); setSkillChoices(skills); setSelectedSkills((current) => current.filter((name) => skills.some((skill) => skill.name === name))); if (!skills.length) setError('这个文件夹下没有找到有效的 SKILL.md'); }
    catch (e) { setError((e as Error).message); }
  };

  const createAgent = async (event: FormEvent) => {
    event.preventDefault(); setError('');
    if (!editingAgentId && setupStep === 1) { await probeSetupConnection(); return; }
    try {
      const payload = { name: agentName, base_url: baseUrl, model, mode, system_prompt: systemPrompt, context_window: contextWindow, skill_directory: skillDirectory, selected_skills: selectedSkills, prompt_variables: promptVariables, input_rate_per_million: inputRate.trim() ? Number(inputRate) : null, output_rate_per_million: outputRate.trim() ? Number(outputRate) : null };
      let saved: Agent;
      if (editingAgentId) {
        saved = await api<Agent>(`/agents/${editingAgentId}`, { method: 'PATCH', body: JSON.stringify(payload) });
        if (apiKey) await api(`/agents/${saved.id}/credentials`, { method: 'PUT', body: JSON.stringify({ api_key: apiKey }) });
      } else {
        if (!apiKey.trim()) { setError('请输入模型 API 密钥。'); setSetupStep(1); return; }
        const created = await api<{ id: string }>('/setup/model', {
          method: 'POST', body: JSON.stringify({ ...payload, api_key: apiKey }),
        });
        saved = await api<Agent>(`/agents/${created.id}`);
      }
      setApiKey(''); setAgents((items) => editingAgentId ? items.map((item) => item.id === saved.id ? saved : item) : [saved, ...items]); setPromptVersions(await api<PromptVersion[]>(`/agents/${saved.id}/prompts`)); setEditingAgentId(saved.id); setShowSettings(false); setStartupStatus({ completed: true, agent: { id: saved.id, name: saved.name, model: saved.model } }); setError('配置已保存。现在可以开始对话。');
    } catch (e) { setError((e as Error).message); }
  };

  const testAgentConnection = async () => {
    if (!editingAgentId) { setError('先保存智能体设置，再测试连接。'); return; }
    try { const result = await api<{ status: string; elapsed_ms: number }>(`/agents/${editingAgentId}/test-connection`, { method: 'POST' }); setConnectionStatus(`连接成功 · ${result.elapsed_ms} ms`); }
    catch (e) { setConnectionStatus(''); setError((e as Error).message); }
  };

  const historyLoading = !!sessionId && loadedHistoryId !== sessionId;
  const workspaceReady = !sessionId || (workspaceStatus.sessionId === sessionId && workspaceStatus.epoch === epoch && workspaceStatus.ready);
  const createSession = (signal?: AbortSignal, workspace: WorkspaceChoice = workspaceChoice) => api<Session>('/sessions', {
    method: 'POST', body: JSON.stringify({ agent_id: agentId ?? null, workspace }), signal,
  });
  const attachSession = (session: Session, history: Message[]) => {
    ++sessionListRequest.current;
    preparedSession.current = session.id;
    setLoadedHistoryId(session.id);
    setSessionId(session.id);
    setSessions((items) => [session, ...items.filter((item) => item.id !== session.id)]);
    setMessages(history);
  };

  const newChat = async () => {
    if (!canChat || busy || creatingSession) return;
    setCreatingSession(true);
    try {
      const session = await createSession(undefined, workspaceReady ? workspaceChoice : { group_id: null, execution_mode: 'local' });
      if (!mounted.current) return;
      attachSession(session, []);
      setError('');
      if (!isChatPage) navigate(chatPath);
    } catch (e) { setError((e as Error).message); }
    finally { setCreatingSession(false); }
  };

  const send = async () => {
    if (!draft.trim() || activeController.current || creatingSession || historyLoading || !workspaceReady || !canChat) return;
    if (!agentId && !preference?.model) { setError('请先为工作区对话选择模型。'); return; }
    if (!capabilitySelection) {
      setError(catalogState?.error ? '无法加载对话工具，请通过「＋」重试。' : '正在加载对话工具，请稍后发送。');
      return;
    }
    const sendOptions = chatRequestOptions(capabilitySelection, preference?.model, approvalMode);
    const text = draft.trim();
    let currentSession = sessionId;
    let activeRunId = '';
    let terminalReceived = false;
    let appendTrace: ((event: RunTrace) => void) | undefined;
    const createBranch = Boolean(branchMessageId && sessionId);
    const controller = new AbortController();
    const { signal } = controller;
    activeController.current = controller;
    const userMessageId = crypto.randomUUID(); const assistantMessageId = crypto.randomUUID();
    const writer = createTypewriter((text) => {
      if (!mounted.current) return;
      setMessages((items) => items.map((message) => message.id === assistantMessageId
        ? { ...message, content: message.content + text } : message));
    });
    const stopTyping = () => mounted.current ? writer.stop() : writer.dispose();
    signal.addEventListener('abort', stopTyping, { once: true });
    setError('');
    setBusy(true);
    try {
      if (createBranch) {
        const created = await api<Session>(`/sessions/${sessionId}/branch`, { method: 'POST', body: JSON.stringify({ message_id: branchMessageId, content: text }), signal });
        const history = await api<Message[]>(`/sessions/${created.id}/messages`, { signal });
        if (signal.aborted) return;
        currentSession = created.id;
        copyComposerToSession(created.id);
        attachSession(created, history);
      } else if (!currentSession) {
        const created = await createSession(signal);
        if (signal.aborted) return;
        currentSession = created.id;
        copyComposerToSession(created.id);
        attachSession(created, []);
      }
      setDrafts((previous) => {
        const next = { ...previous };
        delete next[composerKey];
        return next;
      });
      const updateReply = (patch: Partial<Message>) => setMessages((items) => items.map((item) =>
        item.id === assistantMessageId ? { ...item, ...patch } : item));
      setMessages((items) => [...items, ...(createBranch ? [] : [{ id: userMessageId, role: 'user', content: text }]),
        { id: assistantMessageId, role: 'assistant', content: '', run_id: assistantMessageId }]);
      appendTrace = (event: RunTrace) => {
        const traceSession = currentSession;
        setLiveTrace((previous) => ({ sessionId: traceSession, events: [
          ...(previous?.sessionId === traceSession ? previous.events : []), event,
        ].slice(-200) }));
      };
      const response = await fetch(`/api/sessions/${currentSession}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: text, reuse_last_user: createBranch, ...sendOptions }), signal });
      if (signal.aborted) return;
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        if (signal.aborted) return;
        setMessages((items) => items.filter((item) => item.id !== userMessageId && item.id !== assistantMessageId));
        if (!createBranch) {
          const key = `${composerPrefix}:${currentSession}`;
          setDrafts((previous) => previous[key]?.text ? previous : { ...previous, [key]: { text, branchMessageId: '' } });
        }
        if (response.status === 422 || response.status === 409) { setCatalogRefresh((n) => n + 1); setModelRefresh((n) => n + 1); }
        throw new Error(typeof body.detail === 'string' ? body.detail : '无法发送消息，请检查对话选项。');
      }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { value, done } = await reader.read(); if (done || signal.aborted) break;
        buffer += decoder.decode(value, { stream: true }); const frames = buffer.split('\n\n'); buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const type = frame.match(/^event: (.+)$/m)?.[1]; const data = frame.match(/^data: (.+)$/m)?.[1]; if (!data) continue;
          if (type === 'delta') writer.push(JSON.parse(data).text as string);
          else if (type === 'trace') {
            const item = JSON.parse(data) as RunTrace;
            if (!activeRunId && item.run_id) {
              activeRunId = item.run_id;
              updateReply({ run_id: activeRunId });
            }
            terminalReceived ||= isTerminalTrace(item);
            appendTrace({ ...item, run_id: activeRunId || assistantMessageId });
          } else if (type === 'images') updateReply({ images: JSON.parse(data).images as ChatImage[] });
          else if (type === 'done') terminalReceived = true;
          else if (type === 'plan') updateReply({ plan: JSON.parse(data).plan as string });
          else if (type === 'approval_required') setPendingApproval(JSON.parse(data) as PendingApproval);
          else if (type === 'error') throw new Error(JSON.parse(data).message);
        }
      }
      if (signal.aborted) return;
      await writer.finish();
      if (signal.aborted) return;
      await loadSessions(signal);
    } catch (e) { if (!signal.aborted) setError((e as Error).message); }
    finally {
      stopTyping();
      signal.removeEventListener('abort', stopTyping);
      activeController.current = null;
      if (mounted.current) {
        if (appendTrace && !terminalReceived) {
          appendTrace({
            run_id: activeRunId || assistantMessageId,
            event_type: signal.aborted ? 'run_cancelled' : 'run_failed',
            status: signal.aborted ? 'cancelled' : 'failed',
            summary: signal.aborted ? '已停止接收回复' : '未能完成本次对话',
          });
        }
        if (appendTrace) setTraceRefresh((n) => n + 1);
        setBusy(false);
        setPendingApproval(null);
      }
    }
  };

  const importDataFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setError('导入文件不能大于 10 MB'); return; }
    try { const snapshot = JSON.parse(await file.text()); const preview = await api<Omit<ImportPreview, 'payload'>>('/data/preview', { method: 'POST', body: JSON.stringify(snapshot) }); setImportPreview({ ...preview, payload: snapshot }); }
    catch (e) { setError((e as Error).message || '文件不是有效的备份'); } finally { event.target.value = ''; }
  };
  const confirmImport = async () => {
    if (!importPreview) return;
    try { const result = await api<{ agents_imported: number; sessions_imported: number; messages_imported: number; mcp_servers_imported: number }>('/data/import', { method: 'POST', body: JSON.stringify(importPreview.payload) }); setDataTransferStatus(`已导入 ${result.agents_imported} 个智能体、${result.sessions_imported} 个对话和 ${result.messages_imported} 条消息。凭据需重新配置。`); setImportPreview(null); await loadAgents(); if (mounted.current && canChat) await loadSessions(); }
    catch (e) { setError((e as Error).message); }
  };

  const chatPage = !canChat ? <section className="app-page-loading">
    <p>{error || (agentsLoaded ? '未找到此智能体' : '正在加载智能体…')}</p>
    <Link to="/chat">返回工作区对话</Link>
  </section> : <>
      <header className="topbar"><div className="breadcrumb">{activeAgent ? <><span>Agent 专属对话</span> <span>/</span> <strong>{activeAgent.name}</strong></> : <strong>工作区对话</strong>}</div><div className="top-actions"><span className="local-indicator"><i /> 本地运行</span>{isChatPage && <button className="file-drawer-toggle" type="button" aria-expanded={fileDrawerOpen} aria-controls="workspace-file-drawer" onClick={() => setFileDrawerOpen((value) => !value)}>▤ 文件</button>}{!agentId && <button className="icon-button" aria-label="打开模型库" onClick={() => navigate('/plugins?tab=Model')}>⚙</button>}</div></header>
      {historyLoading ? <div className="app-page-loading" role="status">{historyError ? <>无法加载对话历史 <button type="button" onClick={() => { setHistoryError(''); setHistoryRetry((n) => n + 1); }}>重试加载历史</button></> : '正在加载对话历史…'}</div> : !messages.length ? <section className="welcome"><div className="welcome-orb"><div className="orb-inner">✳</div><span className="orb-ring ring-one"/><span className="orb-ring ring-two"/></div><div className="eyebrow"><span /> {activeAgent ? 'AGENT CHAT' : 'YOUR PRIVATE AI WORKSPACE'}</div>{activeAgent ? <><h1>你好，我是<em>{activeAgent.name}</em></h1><p className="welcome-copy">在这里开启与此 Agent 的专属对话。<br/>本地对话历史独立保存。</p></> : <><h1>好想法，<em>从这里开始。</em></h1><p className="welcome-copy">一个安静、专注的空间，让想法自然生长。<br/>你的对话与数据，始终留在本地。</p></>}<div className="suggestions"><button onClick={() => setDraft('帮我梳理一个新项目的想法')}><span>✦</span> 梳理一个新想法 <b>↗</b></button><button onClick={() => setDraft('帮我总结今天的工作重点')}><span>◷</span> 整理工作重点 <b>↗</b></button><button onClick={() => setDraft('为我制定一个清晰的行动计划')}><span>⌁</span> 制定行动计划 <b>↗</b></button></div></section> : <section className="conversation">
        {contextSummaries.length > 0 && <details className="context-summary"><summary>上下文整理记录（{contextSummaries.length}）</summary>{contextSummaries.map((summary) => <article key={summary.id}><small>{new Date(summary.created_at).toLocaleString()} · 整理了 {summary.summarized_message_count} 条历史消息</small><p>{summary.summary}</p></article>)}</details>}
        <div className="message-list">{messages.map((message, index) => {
          const assistant = message.role === 'assistant';
          return <article className={`message ${message.role}`} key={message.id}>
            {assistant && <div className="message-avatar">✳</div>}
            <div className="message-body">
              <div className="message-author">{message.role === 'user' ? '你' : activeAgent?.name || '助手'}</div>
              {assistant && message.run_id && <RunTracePanel events={tracesByRun.get(message.run_id) ?? []}
                loading={currentTrace?.loading} error={currentTrace?.error} onRetry={() => setTraceRefresh((n) => n + 1)}>
                {message.plan && <div className="plan-card"><div className="plan-heading"><span>◇</span><strong>执行计划</strong></div><pre>{message.plan}</pre></div>}
              </RunTracePanel>}
              <div className="message-content">{message.content ? <MessageContent content={message.content} onWorkspaceFile={openWorkspaceFile} onWebLink={openWebLink}/> : (busy && index === messages.length - 1 ? <span className="typing"><i/><i/><i/></span> : '')}</div>
              {assistant && <ChatImages images={message.images}/>}
              {message.role === 'user' && !busy && <button className="branch-message" type="button" onClick={() => { setBranchMessageId(message.id); setDraft(message.content); }}>编辑并从此处分支 ↗</button>}
              {assistant && !busy && message.content && <button className="branch-message" type="button" onClick={() => {
                const previousUser = messages.slice(0, index).reverse().find((item) => item.role === 'user');
                if (previousUser) { setBranchMessageId(previousUser.id); setDraft(previousUser.content); }
              }}>重新生成（新分支） ↻</button>}
            </div>
          </article>;
        })}</div>
      </section>}
      {error && <div className="notice" role="status"><span>ⓘ</span>{error}<button onClick={() => setError('')}>×</button></div>}
      <div className="composer-wrap">
        {branchMessageId && <div className="branch-notice">正在从选中的消息创建一个新对话分支<button type="button" onClick={() => setBranchMessageId('')}>取消</button></div>}
        <form className="composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
          <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => submitChatOnEnter(event, () => void send())} placeholder={agentId || preference?.model ? '写下你正在想的事…' : '选择模型后开始对话'} disabled={busy} rows={2}/>
          <div className="composer-footer">
            <div className="composer-tools">
              <ChatCapabilitiesPicker key={composerKey} catalog={chatCatalog} selection={capabilitySelection}
                onChange={(capabilities) => updateComposer({ capabilities })} canReset={!!agentId && !!preference?.capabilities && !!chatCatalog}
                onReset={() => updateComposer({ capabilities: undefined })} loading={!catalogState || catalogState.loading}
                error={catalogState?.error ?? ''} refresh={() => setCatalogRefresh((n) => n + 1)} disabled={busy || creatingSession}/>
              <ApprovalModePicker value={approvalMode}
                onChange={(approval_mode) => updateComposer({ approval_mode })}
                disabled={busy || creatingSession}/>
              <span className="mode-chip">{activeAgent?.mode === 'plan_execute' ? '◇ 计划执行' : '◉ 灵感对话'}</span>
            </div>
            <div className="send-tools">
              <span className="enter-hint">Enter 发送</span>
              <ChatModelPicker key={composerKey} models={modelState.data} model={preference?.model}
                defaultName={activeAgent?.model_config_name || activeAgent?.model || undefined} onChange={(model) => updateComposer({ model })}
                loading={modelState.loading} error={modelState.error} refresh={() => setModelRefresh((n) => n + 1)} disabled={busy || creatingSession}/>
              <button className={`send-button ${busy ? 'stop-button' : ''}`} type={busy ? 'button' : 'submit'} onClick={busy ? () => activeController.current?.abort() : undefined} disabled={!busy && (!draft.trim() || historyLoading || creatingSession || !workspaceReady)} aria-label={busy ? '停止运行' : '发送'}>{busy ? '■' : '↑'}</button>
            </div>
          </div>
        </form>
        {isChatPage && <WorkspacePicker sessionId={sessionId} disabled={busy || creatingSession}
          onChoice={reportWorkspaceChoice} onError={reportWorkspaceError} onReady={reportWorkspaceReady} onWorkspace={reportWorkspaceView}/>}
        <div className="composer-caption">模型输出可能有误，重要信息请自行核实。</div>
      </div>
      <footer className="footer"><span>BUILT FOR THOUGHTFUL WORK</span><span>隐私优先 <i/> 本地存储</span></footer>
  </>;

  return <div className={`app-shell${agentId ? ' published-agent-chat' : ''}`}>
    <aside className="sidebar" aria-label={agentId ? 'Agent 本地对话' : '工作区导航'}>
      {agentId ? <div className="brand published-agent-brand"><div className="brand-mark">{Array.from(activeAgent?.name || 'A')[0]}</div><span>{activeAgent?.name || 'Agent'}</span></div> : <div className="brand"><div className="brand-mark">a</div><span>atelier<span className="brand-dot">.</span></span><span className="edition">LOCAL</span></div>}
      <button className="new-chat" onClick={newChat} disabled={!canChat || busy || creatingSession}><span>＋</span> 新建对话</button>
      {!agentId && <>
      <div className="side-label">工作区</div>
      <button className={`nav-item ${isChatPage && !agentId ? 'active' : ''}`} onClick={() => navigate('/chat')}><span className="nav-icon">◉</span> 对话 <span className="nav-count">{!agentId && (sessions.length || '')}</span></button>
      <button className={`nav-item ${location.pathname.startsWith('/plugins') ? 'active' : ''}`} onClick={() => navigate('/plugins?tab=Model')}><span className="nav-icon">▦</span> 模型及插件库</button>
      <button className={`nav-item ${location.pathname === '/databases' ? 'active' : ''}`} onClick={() => navigate('/databases')}><span className="nav-icon">▤</span> 数据库</button>
      <button className={`nav-item ${location.pathname === '/knowledge-bases' ? 'active' : ''}`} onClick={() => navigate('/knowledge-bases')}><span className="nav-icon">▤</span> 知识库</button>
      <button className="nav-item" disabled={!canChat} onClick={() => setShowMemory(true)}><span className="nav-icon">◇</span> 长期记忆</button>
      <button className={`nav-item ${location.pathname === '/scheduled-tasks' ? 'active' : ''}`} onClick={() => navigate('/scheduled-tasks')}><span className="nav-icon">◷</span> 定时任务</button>
      <button className="nav-item" onClick={() => setShowLogs(true)}><span className="nav-icon">≋</span> 运行记录</button>
      <button className="nav-item" onClick={() => { setDataTransferStatus(''); setImportPreview(null); setShowDataTransfer(true); }}><span className="nav-icon">⇅</span> 备份与恢复</button>
      </>}
      <div className="side-label session-label">{agentId ? '本地对话' : '最近对话'} <button aria-label="刷新对话" disabled={!canChat || busy || creatingSession} onClick={() => loadSessions().catch((e: Error) => setError(e.message))}>↻</button></div>
      <input className="session-search" aria-label="搜索历史对话" placeholder="⌕ 搜索对话…" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} />
      <div className="session-list">
        {(sessionSearch.trim() ? searchHits.map((hit) => ({ id: hit.session_id, agent_id: hit.agent_id, title: hit.title })) : sessions).map((session) => <div key={session.id} className="session-row"><button className={`session-item ${session.id === sessionId ? 'selected' : ''}`} disabled={busy || creatingSession} onClick={() => selectSession(session.id)}><span className="session-bullet" />{session.title || '新对话'}</button><div className="session-actions"><button type="button" aria-label="重命名对话" onClick={() => renameSession(session)}>✎</button><button type="button" aria-label="删除对话" disabled={busy || creatingSession} onClick={() => deleteSession(session)}>×</button></div></div>)}
        {!sessions.length && !sessionSearch && <div className="empty-sessions">你的对话会显示在这里</div>}{sessionSearch && !searchHits.length && <div className="empty-sessions">没有找到相关对话</div>}
      </div>
      {!agentId && <div className="sidebar-bottom"><div className="avatar">A</div><div className="profile"><strong>本地工作区</strong><span>个人空间</span></div><button className="icon-button" aria-label="设置" onClick={() => navigate('/plugins?tab=Model')}>⚙</button></div>}
    </aside>
    <main className={`main-panel${fileDrawerOpen && isChatPage ? ' file-drawer-open' : ''}`}>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace/>}/>
        <Route path="/agents" element={<AgentListPage agents={agents} onConfigure={(id) => navigate(`/agents/${id}/config`)} onCreate={() => navigate('/agents/new')} onDelete={deleteAgent}/>}/>
        <Route path="/agents/new" element={<AgentConfigPage onSaved={saveAgentToList}/>}/>
        <Route path="/agents/:agentId/config" element={<AgentConfigPage onSaved={saveAgentToList}/>}/>
        <Route path="/plugins" element={<PluginLibraryPage agents={agents} onConfigure={(id) => navigate(`/agents/${id}/config`)} onCreate={() => navigate('/agents/new')} onDelete={deleteAgent} onCatalogDeleted={() => loadAgents()}/>}/>
        <Route path="/knowledge-bases" element={<KnowledgeBasePage/>}/>
        <Route path="/databases" element={<DatabasePage/>}/>
        <Route path="/scheduled-tasks" element={<ScheduledTasksPage/>}/>
        <Route path="/chat" element={chatPage}/>
        <Route path="/agents/:agentId/chat" element={chatPage}/>
        <Route path="*" element={<Navigate to="/" replace/>}/>
      </Routes>
      {isChatPage && <WorkspaceFileDrawer sessionId={sessionId} open={fileDrawerOpen} executionMode={workspaceView?.execution_mode ?? workspaceChoice.execution_mode} workspacePath={workspaceView?.path} fileRequest={fileRequest} webRequest={webRequest} onClose={() => setFileDrawerOpen(false)} onError={reportWorkspaceError}/>}
    </main>

    {showSettings && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowSettings(false); }}><form className="settings-card" onSubmit={createAgent}><button type="button" className="modal-close" onClick={() => setShowSettings(false)}>×</button><div className="eyebrow">{editingAgentId ? '智能体设置' : '开始使用'}</div><h2>{editingAgentId ? '编辑智能体' : '配置你的智能体'}</h2><p>{editingAgentId ? '调整智能体配置。' : '两步完成设置，API 密钥仅保存在本机。'}</p>
      {!editingAgentId && <div className="wizard-diagnostics">{diagnosticChecks.map((check) => <div className={check.ok ? 'diagnostic-check ok' : 'diagnostic-check warn'} key={check.name}><span>{check.ok ? '✓' : '!'}</span><small>{check.detail}</small></div>)}</div>}
      {!editingAgentId && <div className="wizard-progress"><span className={setupStep === 1 ? 'current' : 'complete'}>1 <small>连接模型</small></span><i/><span className={setupStep === 2 ? 'current' : ''}>2 <small>创建智能体</small></span></div>}
      {!editingAgentId && setupStep === 1 ? <>
        <div className="agent-templates"><button type="button" onClick={() => applyAgentTemplate('assistant')}>基础对话助手</button><button type="button" onClick={() => applyAgentTemplate('planner')}>计划执行助手</button></div>
        <label>模型服务地址<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" required/></label>
        <label>模型名称<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如 gpt-4o-mini" required/></label>
        <label>API 密钥<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-…" autoComplete="new-password" required/></label>
        <div className="cost-rates"><span>可选：按服务商标价估算用量费用</span><label>输入单价 / 1M Token<input type="number" min={0} step="any" value={inputRate} onChange={(event) => setInputRate(event.target.value)} placeholder="未配置"/></label><label>输出单价 / 1M Token<input type="number" min={0} step="any" value={outputRate} onChange={(event) => setOutputRate(event.target.value)} placeholder="未配置"/></label><small>仅为估算，不代表服务商最终账单。</small></div>
        <button className="primary-button" type="button" disabled={!baseUrl || !model || !apiKey || connectionStatus === '正在测试模型连接…'} onClick={probeSetupConnection}>测试连接并继续 <span>→</span></button>
        {connectionStatus && <div className="connection-status">{connectionStatus}</div>}
      </> : <>
        {!editingAgentId && <div className="setup-connection-summary"><span>✓ 模型连接已测试</span><button type="button" onClick={() => { setSetupStep(1); setConnectionStatus(''); }}>修改连接</button></div>}
        {editingAgentId ? <button type="button" className="new-agent-link" onClick={() => openAgentSettings('')}>＋ 新建另一个智能体</button> : null}
        <label>智能体名称<input value={agentName} onChange={(event) => setAgentName(event.target.value)} required/></label>
        {editingAgentId && <><label>模型服务地址<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1"/></label><label>模型名称<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如 gpt-4o-mini"/></label></>}
        <label>运行方式<select value={mode} onChange={(event) => setMode(event.target.value as 'react' | 'plan_execute')}><option value="react">灵感对话（默认）</option><option value="plan_execute">计划并执行</option></select></label>
        <label>上下文窗口（Token）<input type="number" min={1024} max={2000000} step={1024} value={contextWindow} onChange={(event) => setContextWindow(Number(event.target.value))}/></label>
        <div className="cost-rates"><span>可选：配置服务商标价，以估算单次运行费用</span><label>输入单价 / 1M Token<input type="number" min={0} step="any" value={inputRate} onChange={(event) => setInputRate(event.target.value)} placeholder="未配置"/></label><label>输出单价 / 1M Token<input type="number" min={0} step="any" value={outputRate} onChange={(event) => setOutputRate(event.target.value)} placeholder="未配置"/></label><small>价格由你根据服务商填写；显示的是估算值，不是账单。</small></div>
        <label>系统提示词<textarea className="prompt-input" value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} rows={4} placeholder="支持 {{variable}} 变量，例如：称呼用户为 {{user_name}}"/></label>
        {systemPrompt.match(/\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}/g)?.filter((token, index, all) => all.indexOf(token) === index).map((token) => { const variable = token.replace(/[{}\s]/g, ''); return <label className="prompt-variable" key={variable}>{variable}<input value={promptVariables[variable] ?? ''} onChange={(event) => setPromptVariables((values) => ({ ...values, [variable]: event.target.value }))} placeholder={`填写 ${variable}`}/></label>; })}
        {editingAgentId && <div className="prompt-preview-actions"><button type="button" onClick={previewPrompt}>预览解析结果（本地，不调用模型）</button></div>}{promptPreview && <pre className="prompt-preview">{promptPreview}</pre>}
        <div className="skill-config"><label>Skills 文件夹<input value={skillDirectory} onChange={(event) => setSkillDirectory(event.target.value)} placeholder="本机 Skills 目录路径"/></label><button type="button" onClick={discoverSkills}>发现 Skills</button></div>
        {skillChoices.length > 0 && <div className="skill-choice-list"><small>选择要启用的 Skills；未选择时会按当前任务自动匹配。</small>{skillChoices.map((skill) => <label key={skill.name} className="skill-choice"><input type="checkbox" checked={selectedSkills.includes(skill.name)} onChange={(event) => setSelectedSkills((items) => event.target.checked ? [...items, skill.name] : items.filter((name) => name !== skill.name))}/><span><strong>{skill.name}</strong><small>{skill.description}</small></span></label>)}</div>}
        {editingAgentId && promptVersions.length > 0 && <details className="prompt-history"><summary>历史版本（{promptVersions.length}）</summary>{promptVersions.map((version) => <div className="prompt-version" key={version.id}><span>{version.content || '（空提示词）'}<small>{new Date(version.created_at).toLocaleString()}</small></span><button type="button" onClick={() => restorePrompt(version.id)}>恢复</button></div>)}</details>}
        {editingAgentId && <label>API 密钥<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="留空保持当前密钥；sk-…" autoComplete="new-password"/></label>}
        {editingAgentId && <div className="connection-row"><button type="button" onClick={testAgentConnection}>测试模型连接</button><small>{connectionStatus}</small></div>}
        <div className="wizard-actions">{!editingAgentId && <button type="button" className="secondary-button" onClick={() => setSetupStep(1)}>上一步</button>}<button className="primary-button" type="submit">{editingAgentId ? '保存设置' : '创建智能体'} <span>→</span></button></div>
      </>}
      </form></div>}

    {showMemory && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowMemory(false); }}><section className="settings-card memory-card"><button type="button" className="modal-close" onClick={() => setShowMemory(false)}>×</button><div className="eyebrow">只属于你的个人知识</div><h2>{activeAgent ? `${activeAgent.name} · 专属记忆` : '全局长期记忆'}</h2><p>确认保存的信息会用于未来对话。你可以随时管理。</p><form onSubmit={saveMemory} className="memory-form"><label>添加一条记忆<input value={memoryDraft} onChange={(event) => setMemoryDraft(event.target.value)} placeholder="例如：我偏好简洁、条理清晰的回答"/></label><button className="primary-button" type="submit">保存记忆 <span>→</span></button></form><div className="memory-toolbar"><input aria-label="搜索记忆" value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} placeholder="搜索已保存的记忆…"/><button type="button" onClick={clearMemories}>清空</button></div><div className="memory-list">{memories.map((memory) => <div className="memory-item" key={memory.id}>{editingMemoryId === memory.id ? <input value={memoryEdit} onChange={(event) => setMemoryEdit(event.target.value)}/> : <span>{memory.content}</span>}<small>{memory.scope === 'global' ? '全局' : '此智能体'}</small>{editingMemoryId === memory.id ? <button type="button" onClick={() => updateMemory(memory.id)}>保存</button> : <button type="button" onClick={() => { setEditingMemoryId(memory.id); setMemoryEdit(memory.content); }}>编辑</button>}<button type="button" onClick={() => deleteMemory(memory.id)}>×</button></div>)}{!memories.length && <div className="empty-sessions">还没有保存的记忆</div>}</div></section></div>}
    {pendingApproval && <div className="modal-backdrop approval-backdrop"><section className="settings-card approval-card"><div className="eyebrow">需要你的确认</div><h2>允许这个操作吗？</h2><p>{pendingApproval.description}</p><div className="approval-tool">{pendingApproval.tool_name}</div><pre>{JSON.stringify(pendingApproval.arguments, null, 2)}</pre>{pendingApproval.execution_mode === 'local' ? <p className="approval-warning">代码将在本机目录运行：{pendingApproval.working_directory}。本机执行不是沙箱；代码可以访问当前系统账号有权访问的资源。请检查完整代码后再批准。</p> : pendingApproval.execution_mode === 'sandbox' ? <p className="approval-warning">代码将在本机 Docker 沙箱执行，不会挂载本机工作目录。</p> : <p className="approval-warning">此工具可能修改数据或联系外部服务。未确认前不会执行。</p>}<div className="approval-actions"><button type="button" className="secondary-button" onClick={() => decideApproval(false)}>拒绝</button><button type="button" className="primary-button" onClick={() => decideApproval(true)}>批准并继续</button></div></section></div>}
    {showMcp && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowMcp(false); }}><section className="settings-card mcp-card"><button type="button" className="modal-close" onClick={() => setShowMcp(false)}>×</button><div className="eyebrow">扩展连接</div><h2>MCP Server</h2><p>连接前展示命令或地址并要求你确认。</p><form onSubmit={createMcpServer} className="mcp-form"><label>显示名称<input value={mcpName} onChange={(event) => setMcpName(event.target.value)} required/></label><label>连接方式<select value={mcpTransport} onChange={(event) => setMcpTransport(event.target.value as MCPTransport)}><option value="stdio">Local（本机程序 / stdio）</option><option value="sse">SSE</option><option value="streamable_http">Streamable HTTP</option></select></label>{mcpTransport === 'stdio' ? <><label>启动命令<input value={mcpCommand} onChange={(event) => setMcpCommand(event.target.value)} required/></label><label>命令参数<input value={mcpArgs} onChange={(event) => setMcpArgs(event.target.value)}/></label><label>工作目录<input value={mcpCwd} onChange={(event) => setMcpCwd(event.target.value)}/></label><label>环境变量（KEY=VALUE，每行一项）<textarea className="prompt-input" value={mcpEnvText} onChange={(event) => setMcpEnvText(event.target.value)} rows={3} placeholder={'SERVICE_TOKEN=…\\nREGION=…'}/></label></> : <><label>服务地址<input type="url" value={mcpUrl} onChange={(event) => setMcpUrl(event.target.value)} required/></label><label>Bearer Token<input type="password" value={mcpBearerToken} onChange={(event) => setMcpBearerToken(event.target.value)} autoComplete="new-password"/></label></>}<button className="primary-button" type="submit">添加连接 <span>＋</span></button></form><div className="mcp-server-list">{mcpServers.map((server) => <article className="mcp-server" key={server.id}><div className="mcp-server-head"><div><strong>{server.name}</strong><small>{server.transport === 'stdio' ? `${server.command} ${server.args.join(' ')}` : server.url}</small></div><span className={server.connected ? 'mcp-status connected' : 'mcp-status'}>{server.connected ? '已连接' : '未连接'}</span></div><div className="mcp-actions">{server.connected ? <button type="button" onClick={() => disconnectMcp(server)}>断开</button> : <button type="button" onClick={() => connectMcp(server)}>连接</button>}<button type="button" onClick={() => deleteMcp(server)}>删除</button></div>{(mcpTools[server.id] ?? []).map((tool) => <div className="mcp-tool" key={tool.name}><span>{tool.name.split('/').pop()}</span><small>{tool.risk === 'read_only' ? '只读 · 直接调用' : '可能修改数据 · 直接调用'}</small></div>)}</article>)}{!mcpServers.length && <div className="empty-sessions">添加一个 MCP Server 来扩展智能体能力</div>}</div></section></div>}
    {showDataTransfer && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowDataTransfer(false); }}><section className="settings-card transfer-card"><button type="button" className="modal-close" onClick={() => setShowDataTransfer(false)}>×</button><div className="eyebrow">本机数据</div><h2>备份与恢复</h2><p>备份不包含 API 密钥和远程 MCP Token；恢复后需重新配置凭据。</p><button className="primary-button" type="button" onClick={() => window.open('/api/data/export', '_blank', 'noopener,noreferrer')}>下载备份 JSON <span>↓</span></button><label className="import-file-label">从备份恢复<input type="file" accept="application/json,.json" onChange={importDataFile}/></label>{importPreview && <div className="import-preview"><strong>导入前预览</strong><small>智能体 {importPreview.agents} · 对话 {importPreview.sessions} · 消息 {importPreview.messages} · 记忆 {importPreview.memories} · MCP {importPreview.mcp_servers}</small>{importPreview.mcp_name_conflicts.length > 0 && <small className="import-warning">MCP 名称冲突将自动添加导入后缀：{importPreview.mcp_name_conflicts.join('、')}</small>}{importPreview.warnings.map((warning) => <small className="import-warning" key={warning}>{warning}</small>)}<div className="import-actions"><button type="button" className="secondary-button" onClick={() => setImportPreview(null)}>取消</button><button type="button" className="primary-button" onClick={confirmImport}>确认导入</button></div></div>}{dataTransferStatus && <div className="transfer-success">{dataTransferStatus}</div>}</section></div>}
    {showLogs && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowLogs(false); }}><section className="settings-card logs-card"><button type="button" className="modal-close" onClick={() => setShowLogs(false)}>×</button><div className="eyebrow">最近 30 天</div><h2>运行记录</h2><p>运行轨迹、耗时和模型返回的 Token 用量。</p><div className="logs-toolbar"><input aria-label="搜索运行记录" value={logQuery} onChange={(event) => setLogQuery(event.target.value)} placeholder="搜索事件、工具或错误…"/><select aria-label="按级别筛选" value={logLevel} onChange={(event) => setLogLevel(event.target.value)}><option value="">所有级别</option><option value="info">信息</option><option value="warning">警告</option><option value="error">错误</option></select><select aria-label="按模块筛选" value={logSubsystem} onChange={(event) => setLogSubsystem(event.target.value)}><option value="">所有模块</option><option value="runtime">运行时</option><option value="mcp">MCP</option></select><input aria-label="开始日期" type="date" value={logSince} onChange={(event) => setLogSince(event.target.value)}/><input aria-label="结束日期" type="date" value={logUntil} onChange={(event) => setLogUntil(event.target.value)}/><button type="button" onClick={() => window.open(`/api/logs/export?format=jsonl&q=${encodeURIComponent(logQuery)}&level=${encodeURIComponent(logLevel)}&subsystem=${encodeURIComponent(logSubsystem)}&since=${encodeURIComponent(logSince)}&until=${encodeURIComponent(logUntil ? `${logUntil}T23:59:59` : '')}`, '_blank', 'noopener,noreferrer')}>JSONL</button><button type="button" onClick={() => window.open(`/api/logs/export?format=csv&q=${encodeURIComponent(logQuery)}&level=${encodeURIComponent(logLevel)}&subsystem=${encodeURIComponent(logSubsystem)}&since=${encodeURIComponent(logSince)}&until=${encodeURIComponent(logUntil ? `${logUntil}T23:59:59` : '')}`, '_blank', 'noopener,noreferrer')}>CSV</button></div><div className="log-list">{logEvents.map((item) => <article className="log-item" key={item.id}><i className={item.level === 'error' || item.status === 'failed' ? 'failed' : ''}/><div><strong>{item.summary || item.event_type}</strong><small>{item.subsystem} · {item.status} · {new Date(item.occurred_at).toLocaleString()}</small></div>{item.duration_ms != null && <em>{Math.round(item.duration_ms)} ms</em>}{(item.input_tokens != null || item.output_tokens != null) && <em>{item.input_tokens ?? '—'} in / {item.output_tokens ?? '—'} out</em>}{item.estimated_cost != null && <em>≈ {item.estimated_cost.toFixed(6)} {item.cost_currency}</em>}</article>)}{!logEvents.length && <div className="empty-sessions">没有匹配的运行记录</div>}</div></section></div>}
  </div>;
}

export default App;
