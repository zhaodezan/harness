import { FormEvent, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { AgentListPage, AgentListItem } from './AgentListPage';

type Tab = 'Model' | 'Agent' | 'Skill' | 'MCP' | 'Tool' | 'Docker 沙箱' | '第三方插件';
type ThirdPartyTab = 'Dify' | 'DSH';
type ModelConfig = { id: string; name: string; base_url: string; model: string; credential_configured: boolean; last_tested_at: string | null };
type Skill = { name: string; description: string };
type LocalTool = { id: string; name: string; description: string; risk: string; status: string; source: string; error?: string; execution_mode?: 'sandbox' | 'windows_local'; windows_local_available?: boolean; source_sha256?: string };
type MCPServer = { id: string; name: string; transport: string; url: string; command: string; args: string[]; connected: boolean; auto_connect: boolean; connection_error: string | null };
type MCPTool = { name: string; description: string; risk: string };
type MCPTransport = 'stdio' | 'sse' | 'streamable_http';
type SandboxSettings = { enabled: boolean; image: string; timeout_seconds: number; memory_mb: number; cpu_count: number; network_enabled: boolean; configured: boolean };
const tabs: Tab[] = ['Model', 'Agent', 'Skill', 'MCP', 'Tool', 'Docker 沙箱', '第三方插件'];

const TOOL_TEMPLATE = `def greet(name: str, punctuation: str = "!") -> str:
    """生成一条简短的问候语。

    Args:
        name: 要问候的人的名字。
        punctuation: 句末标点，默认为感叹号。
    """
    return f"你好，{name}{punctuation}"
`;

function isRemoteHttp(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  } catch { return false; }
}

function confirmModelTransport(url: string) {
  return !isRemoteHttp(url) || window.confirm('此服务使用 HTTP，API 密钥和请求内容不会加密。建议改用 HTTPS。是否仍要连接？');
}

type PluginLibraryPageProps = {
  agents: AgentListItem[];
  onConfigure: (agentId: string) => void;
  onCreate: () => void;
  onDelete: (agent: AgentListItem) => void;
};

export async function testLocalDocker(update: (status: { testing: boolean; message: string; error: string }) => void) {
  update({ testing: true, message: '', error: '' });
  try {
    await api('/sandbox/test', { method: 'POST' });
    update({ testing: false, message: 'Docker CLI、Engine 和本地镜像检查通过；未运行容器。', error: '' });
  } catch (reason) {
    update({ testing: false, message: '', error: `${(reason as Error).message} 请检查 Docker CLI、Engine 和本地镜像。` });
  }
}

export function PluginLibraryPage({ agents, onConfigure, onCreate, onDelete }: PluginLibraryPageProps) {
  const [params, setParams] = useSearchParams();
  const activeTab = tabs.includes(params.get('tab') as Tab) ? params.get('tab') as Tab : 'Model';
  const [thirdPartyTab, setThirdPartyTab] = useState<ThirdPartyTab>('Dify');
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [modelMigrationWarnings, setModelMigrationWarnings] = useState<string[]>([]);
  const [modelName, setModelName] = useState('');
  const [modelBaseUrl, setModelBaseUrl] = useState('');
  const [modelId, setModelId] = useState('');
  const [modelApiKey, setModelApiKey] = useState('');
  const [editingModelId, setEditingModelId] = useState('');
  const [modelTestingId, setModelTestingId] = useState('');
  const [modelCandidateStatus, setModelCandidateStatus] = useState<'idle' | 'testing' | 'passed'>('idle');
  const [modelSaving, setModelSaving] = useState(false);
  const modelBusy = modelSaving || modelCandidateStatus === 'testing';
  const needsModelKey = !models.find((model) => model.id === editingModelId)?.credential_configured;
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<LocalTool[]>([]);
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [mcpTools, setMcpTools] = useState<Record<string, MCPTool[]>>({});
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [toolSource, setToolSource] = useState(TOOL_TEMPLATE);
  const [editingToolId, setEditingToolId] = useState('');
  const [mcpName, setMcpName] = useState('');
  const [mcpTransport, setMcpTransport] = useState<MCPTransport>('stdio');
  const [mcpCommand, setMcpCommand] = useState('');
  const [mcpArgs, setMcpArgs] = useState('');
  const [mcpCwd, setMcpCwd] = useState('');
  const [mcpEnvText, setMcpEnvText] = useState('');
  const [mcpUrl, setMcpUrl] = useState('');
  const [mcpToken, setMcpToken] = useState('');
  const [sandbox, setSandbox] = useState<SandboxSettings>({ enabled: false, image: 'python:3.11-slim', timeout_seconds: 30, memory_mb: 512, cpu_count: 1, network_enabled: false, configured: false });
  const [sandboxTesting, setSandboxTesting] = useState(false);
  const [sandboxSaving, setSandboxSaving] = useState(false);
  const sandboxBusy = sandboxTesting || sandboxSaving;
  const sandboxRequest = useRef(false);
  const sandboxRevision = useRef(0);
  const sandboxDirty = useRef(false);
  const sandboxLoadGeneration = useRef(0);
  const sandboxInitialized = useRef(false);
  const [sandboxLoadState, setSandboxLoadState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [sandboxLoadError, setSandboxLoadError] = useState('');
  const updateSandbox = (update: (value: SandboxSettings) => SandboxSettings) => {
    sandboxDirty.current = true;
    sandboxRevision.current += 1;
    setSandbox(update);
  };

  const loadSandbox = async () => {
    const generation = ++sandboxLoadGeneration.current;
    const revision = sandboxRevision.current;
    if (!sandboxInitialized.current) { setSandboxLoadState('loading'); setSandboxLoadError(''); }
    try {
      const sandboxConfig = await api<SandboxSettings>('/sandbox');
      if (generation === sandboxLoadGeneration.current && revision === sandboxRevision.current && !sandboxDirty.current && !sandboxRequest.current) {
        setSandbox(sandboxConfig);
        sandboxInitialized.current = true;
        setSandboxLoadState('ready');
      }
    } catch (reason) {
      if (generation === sandboxLoadGeneration.current && !sandboxInitialized.current) {
        setSandboxLoadError((reason as Error).message);
        setSandboxLoadState('error');
      }
    }
  };
  const load = async () => {
    const [modelList, skillList, toolList, serverList, migrationStatus] = await Promise.all([
      api<ModelConfig[]>('/models'), api<Skill[]>('/skills'), api<LocalTool[]>('/tools'), api<MCPServer[]>('/mcp/servers'), api<{ warnings: string[] }>('/models/migration-status'), loadSandbox(),
    ]);
    setModels(modelList); setModelMigrationWarnings(migrationStatus.warnings); setSkills(skillList); setTools(toolList); setServers(serverList);
    const discovered: Record<string, MCPTool[]> = {};
    await Promise.all(serverList.filter((server) => server.connected).map(async (server) => {
      discovered[server.id] = await api<MCPTool[]>(`/mcp/servers/${server.id}/tools`).catch(() => []);
    }));
    setMcpTools(discovered);
  };
  useEffect(() => { load().catch((reason: Error) => setError(reason.message)); }, []);

  const resetModelForm = () => {
    setEditingModelId(''); setModelName(''); setModelBaseUrl(''); setModelId(''); setModelApiKey(''); setModelCandidateStatus('idle');
  };
  const saveModel = async (event: FormEvent) => {
    event.preventDefault();
    if (modelBusy || !confirmModelTransport(modelBaseUrl)) return;
    setModelSaving(true); setError(''); setMessage('');
    let saved: ModelConfig;
    try {
      if (editingModelId) {
        saved = await api<ModelConfig>(`/models/${editingModelId}`, {
          method: 'PATCH', body: JSON.stringify({ name: modelName, base_url: modelBaseUrl, model: modelId, api_key: modelApiKey.trim() || undefined }),
        });
      } else {
        saved = await api<ModelConfig>('/models', {
          method: 'POST', body: JSON.stringify({ name: modelName, base_url: modelBaseUrl, model: modelId, api_key: modelApiKey }),
        });
      }
    } catch (reason) {
      const connected = reason instanceof ApiError && reason.code === 'model_credential_save_failed';
      setModelCandidateStatus(connected ? 'passed' : 'idle');
      setError((reason as Error).message);
      return;
    } finally { setModelSaving(false); }
    setModels((items) => [...items.filter((item) => item.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)));
    setMessage(`模型「${saved.name}」连接成功，API 密钥已安全保存。`);
    resetModelForm();
  };
  const testCandidateModel = async () => {
    if (modelBusy || !confirmModelTransport(modelBaseUrl)) return;
    setError(''); setMessage(''); setModelCandidateStatus('testing');
    try {
      await api('/models/test-connection', { method: 'POST', body: JSON.stringify({ base_url: modelBaseUrl, model: modelId, api_key: modelApiKey }) });
      setModelCandidateStatus('passed');
    } catch (reason) {
      setModelCandidateStatus('idle'); setError((reason as Error).message);
    }
  };
  const editModel = (item: ModelConfig) => {
    setEditingModelId(item.id); setModelName(item.name); setModelBaseUrl(item.base_url); setModelId(item.model); setModelApiKey(''); setModelCandidateStatus('idle');
    setError(''); setMessage('');
  };
  const testModel = async (item: ModelConfig) => {
    if (!confirmModelTransport(item.base_url)) return;
    setModelTestingId(item.id); setError(''); setMessage('');
    try {
      await api(`/models/${item.id}/test-connection`, { method: 'POST' });
      setMessage(`Model「${item.name}」连接成功`); await load();
    } catch (reason) { setError((reason as Error).message); }
    finally { setModelTestingId(''); }
  };
  const deleteModel = async (item: ModelConfig) => {
    if (!window.confirm(`删除 Model「${item.name}」？正在被 Agent 使用的 Model 不能删除。`)) return;
    try { await api(`/models/${item.id}`, { method: 'DELETE' }); setMessage(`Model「${item.name}」已删除`); await load(); }
    catch (reason) { setError((reason as Error).message); }
  };

  const importSkill = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const form = new FormData(); form.append('file', file);
    try { const result = await api<{ skills: Skill[] }>('/skills/import', { method: 'POST', body: form }); setMessage(`已加载 ${result.skills.map((item) => item.name).join('、')}`); setError(''); await load(); }
    catch (reason) { setError((reason as Error).message); }
    finally { event.target.value = ''; }
  };
  const saveTool = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const result = editingToolId
        ? await api<LocalTool>(`/tools/${editingToolId}`, { method: 'PUT', body: JSON.stringify({ source: toolSource }) })
        : await api<LocalTool>('/tools', { method: 'POST', body: JSON.stringify({ source: toolSource }) });
      setMessage(`已保存 Tool：${result.name}`); setError(''); setToolSource(TOOL_TEMPLATE); setEditingToolId(''); await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const importTool = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const form = new FormData(); form.append('file', file);
    try { await api('/tools/import', { method: 'POST', body: form }); setMessage(`已加载 ${file.name}`); setError(''); await load(); }
    catch (reason) { setError((reason as Error).message); }
    finally { event.target.value = ''; }
  };
  const saveSandbox = async (event: FormEvent) => {
    event.preventDefault();
    if (sandboxRequest.current || !sandboxInitialized.current) return;
    sandboxRequest.current = true; setSandboxSaving(true); setError(''); setMessage('');
    try {
      const saved = await api<SandboxSettings>('/sandbox', { method: 'PUT', body: JSON.stringify({
        enabled: sandbox.enabled, image: sandbox.image, timeout_seconds: sandbox.timeout_seconds,
        memory_mb: sandbox.memory_mb, cpu_count: sandbox.cpu_count, network_enabled: sandbox.network_enabled,
      }) });
      sandboxRevision.current += 1; sandboxDirty.current = false;
      setSandbox(saved); setMessage('本机 Docker 沙箱配置已保存。');
    } catch (reason) { setError(`${(reason as Error).message} 请检查 Docker CLI、Engine 和本地镜像。`); }
    finally { sandboxRequest.current = false; setSandboxSaving(false); }
  };
  const testSandbox = async () => {
    if (sandboxRequest.current || !sandboxInitialized.current) return;
    sandboxRequest.current = true;
    try {
      await testLocalDocker(({ testing, message, error }) => {
        setSandboxTesting(testing); setMessage(message); setError(error);
      });
    } finally { sandboxRequest.current = false; }
  };
  const createMcp = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const env = mcpTransport === 'stdio' ? Object.fromEntries(mcpEnvText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
        const index = line.indexOf('=');
        if (index < 1) throw new Error('环境变量需使用 KEY=VALUE 格式');
        return [line.slice(0, index).trim(), line.slice(index + 1)];
      })) : {};
      await api('/mcp/servers', { method: 'POST', body: JSON.stringify({ name: mcpName, transport: mcpTransport, url: mcpTransport === 'stdio' ? '' : mcpUrl, command: mcpTransport === 'stdio' ? mcpCommand : '', args: mcpTransport === 'stdio' ? mcpArgs.split(/\s+/).filter(Boolean) : [], cwd: mcpTransport === 'stdio' ? mcpCwd : '', env, bearer_token: mcpToken }) });
      setMcpName(''); setMcpCommand(''); setMcpArgs(''); setMcpCwd(''); setMcpEnvText(''); setMcpUrl(''); setMcpToken(''); setMessage('已添加 MCP 配置'); setError(''); await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const toggleMcp = async (server: MCPServer) => {
    try {
      if (server.connected) {
        await api(`/mcp/servers/${server.id}/disconnect`, { method: 'POST' });
        setMessage('已断开 MCP Server，下次启动不会自动连接。');
      } else {
        const target = server.transport === 'stdio' ? `${server.command} ${server.args.join(' ')}` : server.url;
        if (!server.auto_connect && !window.confirm(`即将连接 MCP Server：\n${target}\n请确认这是你信任的服务。连接成功后，应用启动时会自动连接。`)) return;
        await api(`/mcp/servers/${server.id}/connect`, { method: 'POST', body: JSON.stringify({ confirmed: true }) });
        setMessage('MCP Server 已连接，下次启动会自动连接。');
      }
      setError('');
      await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const disableMcpAutoConnect = async (id: string) => {
    try {
      await api(`/mcp/servers/${id}/disconnect`, { method: 'POST' });
      setError(''); setMessage('已取消 MCP Server 的启动自动连接。');
      await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const deleteSkill = async (name: string) => { try { await api(`/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }); await load(); } catch (reason) { setError((reason as Error).message); } };
  const deleteTool = async (id: string) => { try { await api(`/tools/${id}`, { method: 'DELETE' }); await load(); } catch (reason) { setError((reason as Error).message); } };
  const deleteServer = async (id: string) => { try { await api(`/mcp/servers/${id}`, { method: 'DELETE' }); await load(); } catch (reason) { setError((reason as Error).message); } };

  return <section className="plugin-library-page">
    <header className="plugin-library-heading"><div><div className="eyebrow">LOCAL PLUGINS</div><h1>模型及插件库</h1><p>管理共享模型连接与本地 Agent、Skills、MCP 和 Python Tools。</p></div></header>
    <nav className="plugin-tabs" aria-label="插件类型">{tabs.map((tab) => <button key={tab} className={activeTab === tab ? 'active' : ''} onClick={() => setParams({ tab })}>{tab}<small>{tab === 'Model' ? models.length : tab === 'Agent' ? agents.length : tab === 'Skill' ? skills.length : tab === 'MCP' ? servers.length : tab === 'Tool' ? tools.length : ''}</small></button>)}</nav>
    {error && <div className="plugin-message error" role="alert">{error}<button onClick={() => setError('')}>×</button></div>}{message && <div className="plugin-message" role="status">{message}<button onClick={() => setMessage('')}>×</button></div>}
    {activeTab === 'Model' && <div className="plugin-list model-list">
      {modelMigrationWarnings.length > 0 && <div className="plugin-message error" role="alert">{modelMigrationWarnings.join(' ')}</div>}
      <div className="plugin-list-head"><div><h2>模型连接</h2><p className="plugin-helper">模型可供工作区和多个智能体使用。API 密钥只在本机加密保存，不包含在备份中。</p></div></div>
      <form className="plugin-form model-form" onSubmit={saveModel} aria-busy={modelBusy}>
        <label>显示名称<input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="例如：团队主力模型" required maxLength={100} disabled={modelBusy}/></label>
        <label>API Base URL<input type="url" value={modelBaseUrl} onChange={(event) => { setModelBaseUrl(event.target.value); setModelCandidateStatus('idle'); }} placeholder="https://api.example.com/v1" required disabled={modelBusy}/></label>
        <label>模型标识<input value={modelId} onChange={(event) => { setModelId(event.target.value); setModelCandidateStatus('idle'); }} placeholder="请输入服务商提供的模型标识" required maxLength={200} disabled={modelBusy}/></label>
        <label>API 密钥<input type="password" value={modelApiKey} onChange={(event) => { setModelApiKey(event.target.value); setModelCandidateStatus('idle'); }} placeholder={needsModelKey ? '请输入 API 密钥' : '留空表示继续使用当前密钥'} autoComplete="new-password" required={needsModelKey} disabled={modelBusy}/></label>
        {isRemoteHttp(modelBaseUrl) && <p className="plugin-security-warning">此地址使用 HTTP，密钥和请求内容不会加密。建议改用 HTTPS。</p>}
        {modelCandidateStatus === 'passed' && <p className="plugin-helper" role="status">模型连接测试成功。此表单的改动尚未保存。</p>}
        <div className="model-form-actions">
          <button className="secondary-button" type="button" onClick={() => void testCandidateModel()} disabled={modelBusy || !modelBaseUrl.trim() || !modelId.trim() || !modelApiKey.trim()}>{modelCandidateStatus === 'testing' ? '测试中…' : '仅测试连接'}</button>
          <button className="primary-button" type="submit" disabled={modelBusy}>{modelSaving ? '正在测试并保存…' : '测试并保存'}</button>
          {editingModelId && <button type="button" className="secondary-button" disabled={modelBusy} onClick={resetModelForm}>取消编辑</button>}
        </div>
      </form>
      {models.map((item) => <article className="plugin-card model-card" key={item.id}>
        <div className="model-card-main"><strong>{item.name}</strong><small>{item.base_url}</small><small>{item.model}</small></div>
        <div className="model-card-status"><span className={`model-credential ${item.credential_configured ? 'configured' : 'missing'}`}>{item.credential_configured ? '凭据已配置' : '待配置凭据'}</span><small>{item.last_tested_at ? `已测试 · ${new Date(item.last_tested_at).toLocaleString()}` : '尚未测试连接'}</small></div>
        <div className="model-card-actions"><button type="button" disabled={!item.credential_configured || modelTestingId === item.id} onClick={() => testModel(item)}>{modelTestingId === item.id ? '测试中…' : '测试连接'}</button><button type="button" disabled={modelBusy} onClick={() => editModel(item)}>编辑</button><button type="button" className="danger-button" disabled={modelBusy} onClick={() => deleteModel(item)}>删除</button></div>
      </article>)}
      {!models.length && <p className="plugin-empty">尚未配置模型。添加并测试连接后，即可在 Agent 配置中选择。</p>}
    </div>}
    {activeTab === 'Agent' && <AgentListPage agents={agents} onConfigure={onConfigure} onCreate={onCreate} onDelete={onDelete}/>}
    {activeTab === 'Skill' && <div className="plugin-list"><div className="plugin-list-head"><h2>已加载 Skills</h2><label className="plugin-file-button">从 ZIP 加载<input type="file" accept=".zip,application/zip" onChange={importSkill}/></label></div><p className="plugin-helper">ZIP 中每个 Skill 需有包含 name 与 description 的 SKILL.md。安装在本机应用数据目录。</p>{skills.map((skill) => <article className="plugin-card" key={skill.name}><div><strong>{skill.name}</strong><small>{skill.description}</small></div><button className="danger-button" onClick={() => deleteSkill(skill.name)}>卸载</button></article>)}{!skills.length && <p className="plugin-empty">尚未加载 Skill ZIP。</p>}</div>}
    {activeTab === 'MCP' && <div className="plugin-list">
      <div className="plugin-list-head"><h2>MCP 配置</h2><span className="plugin-helper">连接前请确认服务命令或地址可信。已选中的 MCP 工具直接调用，不逐次审批。</span></div>
      <form className="plugin-form" onSubmit={createMcp}>
        <label>名称<input value={mcpName} onChange={(e) => setMcpName(e.target.value)} required /></label>
        <label>连接方式<select value={mcpTransport} onChange={(e) => setMcpTransport(e.target.value as MCPTransport)}><option value="stdio">Local（本机程序 / stdio）</option><option value="sse">SSE</option><option value="streamable_http">Streamable HTTP</option></select></label>
        {mcpTransport === 'stdio' ? <>
          <label>启动命令<input value={mcpCommand} onChange={(e) => setMcpCommand(e.target.value)} required /></label>
          <label>参数<input value={mcpArgs} onChange={(e) => setMcpArgs(e.target.value)} /></label>
          <label>工作目录<input value={mcpCwd} onChange={(e) => setMcpCwd(e.target.value)} /></label>
          <label>环境变量（KEY=VALUE，每行一项）<textarea className="prompt-input" value={mcpEnvText} onChange={(e) => setMcpEnvText(e.target.value)} rows={3} /></label>
        </> : <>
          <label>服务地址<input type="url" value={mcpUrl} onChange={(e) => setMcpUrl(e.target.value)} required /></label>
          <label>Bearer Token<input type="password" value={mcpToken} onChange={(e) => setMcpToken(e.target.value)} /></label>
        </>}
        <button className="primary-button" type="submit">添加 MCP</button>
      </form>
      {servers.map((server) => <article className="plugin-server-card" key={server.id}>
        <div className="plugin-card">
          <div><strong>{server.name}</strong><small>{server.transport === 'stdio' ? `${server.command} ${server.args.join(' ')}` : server.url} · {server.connected ? '已连接' : '未连接'}{server.auto_connect ? ' · 启动时自动连接' : ''}</small>
            {server.connection_error && <small role="status">{server.connection_error}</small>}
          </div>
          <button onClick={() => toggleMcp(server)}>{server.connected ? '断开' : server.connection_error ? '重试连接' : '连接'}</button>
          {!server.connected && server.auto_connect && <button onClick={() => disableMcpAutoConnect(server.id)}>取消自动连接</button>}
          <button className="danger-button" onClick={() => deleteServer(server.id)}>删除</button>
        </div>
        {(mcpTools[server.id] ?? []).map((tool) => <div className="mcp-tool" key={tool.name}><span>{tool.name}</span><small>{tool.risk === 'read_only' ? '只读 · 直接调用' : '可能修改数据 · 直接调用'} · {tool.description}</small></div>)}
      </article>)}
      {!servers.length && <p className="plugin-empty">尚未配置 MCP Server。</p>}
    </div>}
    {activeTab === 'Tool' && <div className="plugin-list">
      <div className="plugin-list-head"><h2>本地 Python Tools</h2><label className="plugin-file-button">加载 .py<input type="file" accept=".py,text/x-python" onChange={importTool}/></label></div>
      <div className="plugin-security-warning">Windows 上本地 Python Tool 默认通过 PowerShell 子进程执行，PowerShell 不是安全沙箱；在对话中明确选择 Docker 沙箱后才使用本地容器。执行权限可在对话框中选择“全部允许”或“用户审批”。</div>
      <form className="plugin-tool-editor" onSubmit={saveTool}>
        <h3>{editingToolId ? '编辑 Tool' : '编写 Python Tool'}</h3>
        <p className="plugin-helper">每个文件定义一个公开函数，用 Python 类型注解声明参数，在 docstring 的 Args: 中写参数说明。仍支持旧版 TOOL 字典。保存修改后自动重新检查执行条件。</p>
        <textarea aria-label="Python Tool 源码" value={toolSource} onChange={(e) => setToolSource(e.target.value)} spellCheck={false}/>
        <div><button className="primary-button" type="submit">{editingToolId ? '保存修改' : '加载 Tool'}</button>{editingToolId && <button type="button" onClick={() => { setEditingToolId(''); setToolSource(TOOL_TEMPLATE); }}>取消</button>}</div>
      </form>
      {tools.map((tool) => <article className="plugin-card" key={tool.id}>
        <div><strong>{tool.name}</strong><small>{tool.description} · {tool.risk === 'read_only' ? '只读' : '可能修改数据'}{tool.status === 'error' ? ` · ${tool.error}` : ''}</small>
          <small>{tool.execution_mode === 'windows_local' ? 'Windows PowerShell · 默认本机执行' : 'Docker 沙箱 · 由对话执行位置选择'}</small></div>
        <button type="button" onClick={() => { setEditingToolId(tool.id); setToolSource(tool.source); }}>编辑</button><button className="danger-button" onClick={() => deleteTool(tool.id)}>卸载</button>
      </article>)}
      {!tools.length && <p className="plugin-empty">尚未安装自定义 Python Tool。</p>}
    </div>}
    {activeTab === 'Docker 沙箱' && <div className="plugin-list">
      <div className="plugin-list-head"><div><h2>本机 Docker 沙箱</h2><p className="plugin-helper">对话中明确选择 Docker 沙箱时运行 Python Tools、生成代码和 Skill 脚本。</p></div><span className={`model-credential ${sandbox.configured ? 'configured' : 'missing'}`}>{sandboxLoadState === 'loading' ? '加载中' : sandboxLoadState === 'error' ? '加载失败' : sandbox.configured ? '已配置' : '未配置'}</span></div>
      <div className="plugin-security-warning">只使用本机 Docker Engine，不连接远程 Docker；镜像必须已存在，不自动拉取。沙箱不可用或执行失败时不会回退到本机。默认禁用网络，启用网络后容器可能访问网络。</div>
      {sandboxLoadState === 'loading' && <p role="status">正在加载沙箱配置…</p>}
      {sandboxLoadState === 'error' && <div className="plugin-message error" role="alert">沙箱配置加载失败：{sandboxLoadError}。请重试，加载成功前无法修改或测试。 <button type="button" onClick={() => void loadSandbox()}>重试加载沙箱配置</button></div>}
      <form className="plugin-form" onSubmit={saveSandbox}>
        <fieldset disabled={sandboxBusy || sandboxLoadState !== 'ready'} style={{ display: 'contents' }}>
        <label className="plugin-checkbox"><input type="checkbox" checked={sandbox.enabled} onChange={(event) => updateSandbox((value) => ({ ...value, enabled: event.target.checked }))}/>启用 Docker 沙箱</label>
        <label>本地镜像<input value={sandbox.image} onChange={(event) => updateSandbox((value) => ({ ...value, image: event.target.value }))} maxLength={256} required/></label>
        <label>单次执行超时（秒）<input type="number" min={1} max={300} value={sandbox.timeout_seconds} onChange={(event) => updateSandbox((value) => ({ ...value, timeout_seconds: Number(event.target.value) }))} required/></label>
        <label>内存（MB）<input type="number" min={128} max={8192} value={sandbox.memory_mb} onChange={(event) => updateSandbox((value) => ({ ...value, memory_mb: Number(event.target.value) }))} required/></label>
        <label>CPU 数量<input type="number" min={1} max={8} value={sandbox.cpu_count} onChange={(event) => updateSandbox((value) => ({ ...value, cpu_count: Number(event.target.value) }))} required/></label>
        <label className="plugin-checkbox"><input type="checkbox" checked={sandbox.network_enabled} onChange={(event) => updateSandbox((value) => ({ ...value, network_enabled: event.target.checked }))}/>允许容器联网</label>
        <p className="plugin-helper">测试使用已保存的配置；请先保存。请安装 Docker CLI、启动本机 Engine 并自行准备镜像。</p>
        <div className="model-form-actions"><button className="primary-button" type="submit">{sandboxSaving ? '保存中…' : '保存沙箱配置'}</button><button className="secondary-button" type="button" onClick={() => void testSandbox()}>{sandboxTesting ? '测试中…' : '测试本机 Docker'}</button></div>
        </fieldset>
      </form>
    </div>}
    {activeTab === '第三方插件' && <div className="plugin-list third-party-plugin-panel">
      <div className="plugin-list-head"><h2>第三方插件</h2><span className="plugin-helper">第三方平台集成预留入口</span></div>
      <nav className="plugin-tabs" aria-label="第三方插件类型">{(['Dify', 'DSH'] as ThirdPartyTab[]).map((tab) => <button key={tab} className={thirdPartyTab === tab ? 'active' : ''} onClick={() => setThirdPartyTab(tab)}>{tab}</button>)}</nav>
      <div className="third-party-placeholder"><strong>{thirdPartyTab}</strong><p>暂未实现，后续版本开放。</p></div>
    </div>}
  </section>;
}
