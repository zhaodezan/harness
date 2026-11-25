import { FormEvent, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { AgentDebugChat } from './AgentDebugChat';

type Agent = {
  id: string;
  name: string;
  model_config_id?: string;
  model_config_name?: string;
  model: string;
  mode: string;
  system_prompt?: string;
  context_window?: number;
  skill_directory?: string;
  selected_skills?: string[];
  selected_mcp_tools?: string[];
  selected_local_tools?: string[];
  selected_knowledge_bases?: string[];
  selected_database_connections?: string[];
  selected_agents?: string[];
  sandbox_code_enabled?: boolean;
  prompt_variables?: Record<string, string>;
  input_rate_per_million?: number | null;
  output_rate_per_million?: number | null;
};
type ModelConfig = { id: string; name: string; base_url: string; model: string; credential_configured: boolean; last_tested_at: string | null };
type PromptVersion = { id: string; content: string; created_at: string };
type SkillSummary = { name: string; description: string };
type KnowledgeBaseSummary = { id: string; name: string; document_count: number; ready_count: number; status: string };
type DatabaseSummary = { id: string; name: string; db_type: string; readonly: boolean; password_configured: boolean; last_tested_at: string | null };
type DiagnosticCheck = { name: string; ok: boolean; detail: string };
type AgentConfigPageProps = { onSaved: (agent: Agent, isNew: boolean) => void };

export function AgentConfigPage({ onSaved }: AgentConfigPageProps) {
  const { agentId: routeAgentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const editingAgentId = routeAgentId ?? '';
  const isNew = !editingAgentId;

  const [setupStep, setSetupStep] = useState(1);
  const [agentName, setAgentName] = useState('灵感伙伴');
  const [modelConfigs, setModelConfigs] = useState<ModelConfig[]>([]);
  const [modelConfigId, setModelConfigId] = useState('');
  const [contextWindow, setContextWindow] = useState(32768);
  const [mode, setMode] = useState<'react' | 'plan_execute'>('react');
  const [connectionStatus, setConnectionStatus] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [promptVariables, setPromptVariables] = useState<Record<string, string>>({});
  const [promptPreview, setPromptPreview] = useState('');
  const [skillChoices, setSkillChoices] = useState<SkillSummary[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [mcpChoices, setMcpChoices] = useState<{ id: string; name: string; description: string; risk: string }[]>([]);
  const [selectedMcpTools, setSelectedMcpTools] = useState<string[]>([]);
  const [localToolChoices, setLocalToolChoices] = useState<{ id: string; name: string; description: string; risk: string }[]>([]);
  const [selectedLocalTools, setSelectedLocalTools] = useState<string[]>([]);
  const [knowledgeBaseChoices, setKnowledgeBaseChoices] = useState<KnowledgeBaseSummary[]>([]);
  const [selectedKnowledgeBases, setSelectedKnowledgeBases] = useState<string[]>([]);
  const [databaseChoices, setDatabaseChoices] = useState<DatabaseSummary[]>([]);
  const [selectedDatabaseConnections, setSelectedDatabaseConnections] = useState<string[]>([]);
  const [agentChoices, setAgentChoices] = useState<Agent[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [sandboxCodeEnabled, setSandboxCodeEnabled] = useState(false);
  const [legacySkillsWarning, setLegacySkillsWarning] = useState(false);
  const [modelMigrationWarnings, setModelMigrationWarnings] = useState<string[]>([]);
  const [promptVersions, setPromptVersions] = useState<PromptVersion[]>([]);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveRevision, setSaveRevision] = useState(0);
  const hydrated = useRef(false);
  const saveQueue = useRef(Promise.resolve());
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const [diagnosticChecks, setDiagnosticChecks] = useState<DiagnosticCheck[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!isNew);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError('');
    setNotFound(false);
    hydrated.current = false;
    api<{ warnings: string[] }>('/models/migration-status')
      .then((result) => { if (!cancelled) setModelMigrationWarnings(result.warnings); })
      .catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    if (isNew) {
      setLoading(false);
      setSetupStep(1);
      setAgentName('灵感伙伴');
      setModelConfigs([]); setModelConfigId(''); setContextWindow(32768);
      setMode('react');
      setConnectionStatus(''); setSystemPrompt(''); setPromptVariables({});
      setPromptPreview(''); setSkillChoices([]); setMcpChoices([]); setLocalToolChoices([]);
      setSelectedSkills([]); setSelectedMcpTools([]); setSelectedLocalTools([]); setSelectedKnowledgeBases([]); setKnowledgeBaseChoices([]); setSelectedDatabaseConnections([]); setDatabaseChoices([]); setAgentChoices([]); setSelectedAgents([]); setSandboxCodeEnabled(false); setPromptVersions([]);
      hydrated.current = true;
      Promise.all([
        api<ModelConfig[]>('/models'),
        api<SkillSummary[]>('/skills'),
        api<{ id: string; name: string; connected: boolean }[]>('/mcp/servers'),
        api<{ id: string; name: string; description: string; risk: string }[]>('/tools'),
        api<KnowledgeBaseSummary[]>('/knowledge-bases'),
        api<DatabaseSummary[]>('/databases'),
        api<Agent[]>('/agents'),
      ]).then(async ([models, skills, servers, tools, knowledgeBases, databases, allAgents]) => {
        const mcpTools = (await Promise.all(servers.filter((server) => server.connected).map(async (server) => {
          const toolsForServer = await api<{ name: string; description: string; risk: string }[]>(`/mcp/servers/${server.id}/tools`).catch(() => []);
          return toolsForServer.map((tool) => ({ ...tool, id: `mcp:${server.id}:${tool.name.split('/').pop()}` }));
        }))).flat();
        setModelConfigs(models); setModelConfigId(models.find((item) => item.credential_configured && item.last_tested_at)?.id ?? '');
        setSkillChoices(skills); setMcpChoices(mcpTools); setLocalToolChoices(tools); setKnowledgeBaseChoices(knowledgeBases); setDatabaseChoices(databases); setAgentChoices(allAgents);
      }).catch((reason: Error) => setError(reason.message));
      api<{ checks: DiagnosticCheck[] }>('/setup/diagnostics')
        .then((result) => { if (!cancelled) setDiagnosticChecks(result.checks); })
        .catch((reason: Error) => { if (!cancelled) setError(reason.message); });
      return () => { cancelled = true; };
    }

    setLoading(true);
    setSetupStep(2);
    Promise.all([
      api<Agent>(`/agents/${editingAgentId}`),
      api<PromptVersion[]>(`/agents/${editingAgentId}/prompts`),
      api<ModelConfig[]>('/models'),
      api<SkillSummary[]>('/skills'),
      api<{ id: string; name: string; connected: boolean }[]>('/mcp/servers'),
      api<{ id: string; name: string; description: string; risk: string }[]>('/tools'),
      api<{ warnings: string[] }>('/skills/migration-status'),
      api<KnowledgeBaseSummary[]>('/knowledge-bases'),
      api<DatabaseSummary[]>('/databases'),
      api<Agent[]>('/agents'),
    ]).then(async ([agent, versions, models, skills, servers, tools, migrationStatus, knowledgeBases, databases, allAgents]) => {
      const mcpTools = (await Promise.all(servers.filter((server) => server.connected).map(async (server) => {
        const toolsForServer = await api<{ id: string; name: string; description: string; risk: string }[]>(`/mcp/servers/${server.id}/tools`).catch(() => []);
        return toolsForServer.map((tool) => ({ ...tool, id: tool.id }));
      }))).flat();
      if (cancelled) return;
      setAgentName(agent.name);
      setModelConfigs(models); setModelConfigId(agent.model_config_id ?? '');
      setContextWindow(agent.context_window ?? 32768);
      setMode(agent.mode === 'plan_execute' ? 'plan_execute' : 'react');
      setSystemPrompt(agent.system_prompt ?? '');
      setPromptVariables(agent.prompt_variables ?? {});
      setPromptPreview('');
      setSkillChoices(skills);
      setMcpChoices(mcpTools); setLocalToolChoices(tools); setKnowledgeBaseChoices(knowledgeBases); setDatabaseChoices(databases); setAgentChoices(allAgents);
      setSelectedSkills(agent.selected_skills ?? []);
      setSelectedMcpTools(agent.selected_mcp_tools ?? []);
      setSelectedLocalTools(agent.selected_local_tools ?? []);
      setSelectedKnowledgeBases(agent.selected_knowledge_bases ?? []);
      setSelectedDatabaseConnections(agent.selected_database_connections ?? []);
      setSelectedAgents(agent.selected_agents ?? []);
      setSandboxCodeEnabled(agent.sandbox_code_enabled ?? false);
      setLegacySkillsWarning(migrationStatus.warnings.some((warning) => warning.includes(agent.name)));
      setPromptVersions(versions);
      setLoading(false);
      hydrated.current = true;
    }).catch((reason: Error) => {
      if (cancelled) return;
      setNotFound(reason.message === 'Agent 不存在');
      setError(reason.message);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [editingAgentId, isNew]);

  useEffect(() => {
    let cancelled = false;
    if (!editingAgentId || loading || !hydrated.current) return;
    const payload = {
      name: agentName, model_config_id: modelConfigId, mode, system_prompt: systemPrompt,
      context_window: contextWindow, selected_skills: selectedSkills,
      selected_mcp_tools: selectedMcpTools, selected_local_tools: selectedLocalTools,
      selected_knowledge_bases: selectedKnowledgeBases,
      selected_database_connections: selectedDatabaseConnections, selected_agents: selectedAgents, sandbox_code_enabled: sandboxCodeEnabled,
      prompt_variables: promptVariables, input_rate_per_million: null, output_rate_per_million: null,
    };
    setSaveStatus((status) => status === 'error' ? status : 'idle');
    const timer = window.setTimeout(() => {
      setSaveStatus('saving');
      saveQueue.current = saveQueue.current.then(async () => {
        const saved = await api<Agent>(`/agents/${editingAgentId}`, { method: 'PATCH', body: JSON.stringify(payload) });
        if (!cancelled) {
          setSaveStatus('saved');
          onSavedRef.current(saved, false);
        }
      }).catch((reason: Error) => {
        if (!cancelled) { setSaveStatus('error'); setError(reason.message); }
      });
    }, 500);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [editingAgentId, loading, agentName, modelConfigId, mode, systemPrompt, contextWindow, selectedSkills, selectedMcpTools, selectedLocalTools, selectedKnowledgeBases, selectedDatabaseConnections, selectedAgents, sandboxCodeEnabled, promptVariables, saveRevision]);

  useEffect(() => {
    if (!editingAgentId || (saveStatus !== 'idle' && saveStatus !== 'saving' && saveStatus !== 'error')) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [editingAgentId, saveStatus]);

  const createAgent = async () => {
    if (!modelConfigId) { setError('请先添加并测试模型连接。'); return; }
    setError(''); setConnectionStatus('正在创建智能体…');
    try {
      const created = await api<{ id: string }>('/setup/model', {
        method: 'POST', body: JSON.stringify({
          name: agentName, model_config_id: modelConfigId, mode,
          system_prompt: systemPrompt, context_window: contextWindow,
          selected_skills: selectedSkills, selected_mcp_tools: selectedMcpTools,
          selected_local_tools: selectedLocalTools, selected_knowledge_bases: selectedKnowledgeBases,
          selected_database_connections: selectedDatabaseConnections, selected_agents: selectedAgents, sandbox_code_enabled: sandboxCodeEnabled,
          prompt_variables: promptVariables,
        }),
      });
      const saved = await api<Agent>(`/agents/${created.id}`);
      setConnectionStatus('已使用所选 Model 创建智能体；后续修改会自动保存。');
      onSavedRef.current(saved, true);
      navigate(`/agents/${created.id}/config`);
      setSetupStep(2);
    } catch (reason) {
      setConnectionStatus(''); setError((reason as Error).message);
    }
  };

  const applyAgentTemplate = (template: 'assistant' | 'planner') => {
    setAgentName(template === 'assistant' ? '日常助手' : '计划助手');
    setMode(template === 'assistant' ? 'react' : 'plan_execute');
    setSystemPrompt(template === 'assistant'
      ? '你是一位清晰、友好、可靠的中文助手。先理解用户需求，再用简洁、可执行的方式回答。'
      : '你是一位善于规划并推进任务的助手。先把目标分解为清晰步骤，然后依次执行；遇到变化时及时调整计划，并说明进度。');
  };

  const restorePrompt = async (versionId: string) => {
    if (!editingAgentId) return;
    try {
      const updated = await api<Agent>(`/agents/${editingAgentId}/prompts/${versionId}/restore`, { method: 'POST' });
      setSystemPrompt(updated.system_prompt ?? '');
      setPromptVersions(await api<PromptVersion[]>(`/agents/${editingAgentId}/prompts`));
    } catch (reason) { setError((reason as Error).message); }
  };

  const previewPrompt = async () => {
    if (!editingAgentId) return;
    try {
      const result = await api<{ rendered_prompt: string }>(`/agents/${editingAgentId}/prompts/preview`, {
        method: 'POST', body: JSON.stringify({ variables: promptVariables }),
      });
      setPromptPreview(result.rendered_prompt);
    } catch (reason) { setError((reason as Error).message); }
  };

  const testAgentConnection = async () => {
    if (!editingAgentId) return;
    try {
      const result = await api<{ status: string; elapsed_ms: number }>(`/agents/${editingAgentId}/test-connection`, { method: 'POST' });
      setConnectionStatus(`连接成功 · ${result.elapsed_ms} ms`);
    } catch (reason) { setConnectionStatus(''); setError((reason as Error).message); }
  };

  const saveNow = async (): Promise<boolean> => {
    if (!editingAgentId) return true;
    const payload = {
      name: agentName, model_config_id: modelConfigId, mode, system_prompt: systemPrompt,
      context_window: contextWindow, selected_skills: selectedSkills,
      selected_mcp_tools: selectedMcpTools, selected_local_tools: selectedLocalTools,
      selected_knowledge_bases: selectedKnowledgeBases,
      selected_database_connections: selectedDatabaseConnections, selected_agents: selectedAgents, sandbox_code_enabled: sandboxCodeEnabled,
      prompt_variables: promptVariables, input_rate_per_million: null, output_rate_per_million: null,
    };
    setSaveStatus('saving');
    let succeeded = true;
    saveQueue.current = saveQueue.current.then(async () => {
      const saved = await api<Agent>(`/agents/${editingAgentId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      onSavedRef.current(saved, false);
    }).catch((reason: Error) => { succeeded = false; setSaveStatus('error'); setError(reason.message); });
    await saveQueue.current;
    if (succeeded) setSaveStatus('saved');
    return succeeded;
  };

  const leaveConfig = async () => {
    if (editingAgentId && !(await saveNow())) return;
    navigate('/agents');
  };

  const retrySave = (event: FormEvent) => {
    event.preventDefault();
    if (isNew && setupStep === 1) {
      if (!modelConfigId) { setError('请先添加并测试模型连接。'); return; }
      setSetupStep(2); return;
    }
    if (isNew) { void createAgent(); return; }
    if (saveStatus === 'error') setSaveStatus('idle');
    setSaveRevision((revision) => revision + 1);
  };

  return (
    <section className="agent-config-page">
      <button className="agent-page-back" type="button" onClick={leaveConfig}>← 返回智能体列表</button>
      {error && !notFound && <div className="agent-page-error" role="status"><span>{error}</span><button type="button" aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
      {modelMigrationWarnings.length > 0 && <div className="agent-page-error model-migration-warning" role="alert">{modelMigrationWarnings.join(' ')}</div>}
      {loading ? <div className="agent-page-loading">正在加载智能体配置…</div> : notFound ? (
        <div className="agent-not-found"><h1>没有找到这个智能体</h1><p>它可能已被删除，或配置链接无效。</p><Link to="/agents">返回智能体列表</Link></div>
      ) : (
        <div className="agent-config-columns">
        <div className="agent-config-column">
        <form className="settings-card agent-config-form" onSubmit={retrySave}>
          <div className="eyebrow">{editingAgentId ? '智能体设置' : '开始使用'}</div>
          <h1>{editingAgentId ? '编辑智能体' : '配置你的智能体'}</h1>
          <p>{editingAgentId ? '调整智能体配置。' : '两步完成设置，API 密钥仅保存在本机。'}</p>
          {isNew && <div className="wizard-diagnostics">{diagnosticChecks.map((check) => <div className={check.ok ? 'diagnostic-check ok' : 'diagnostic-check warn'} key={check.name}><span>{check.ok ? '✓' : '!'}</span><small>{check.detail}</small></div>)}</div>}
          {isNew && <div className="wizard-progress"><span className={setupStep === 1 ? 'current' : 'complete'}>1 <small>选择模型</small></span><i/><span className={setupStep === 2 ? 'current' : ''}>2 <small>创建智能体</small></span></div>}
          {isNew && setupStep === 1 ? <>
            {!modelConfigs.length ? <div className="model-selection-empty"><p>请先添加并测试模型连接，再创建智能体。</p><button type="button" className="primary-button" onClick={() => navigate('/plugins?tab=Model')}>前往模型及插件库</button></div> : <>
              <label>选择模型<select value={modelConfigId} onChange={(event) => setModelConfigId(event.target.value)} required><option value="">请选择已测试的模型连接</option>{modelConfigs.filter((item) => item.credential_configured && item.last_tested_at).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              {!modelConfigs.some((item) => item.credential_configured && item.last_tested_at) && <p className="plugin-helper">当前没有已测试并配置凭据的模型。<button type="button" className="inline-link-button" onClick={() => navigate('/plugins?tab=Model')}>前往添加或测试模型</button></p>}
              <div className="plugin-helper">模型连接与密钥由模型及插件库统一管理，多个智能体可共享同一模型。</div>
              <button className="primary-button" type="button" disabled={!modelConfigId} onClick={() => setSetupStep(2)}>继续配置智能体 <span>→</span></button>
            </>}
          </> : <>
            {isNew && <div className="setup-connection-summary"><span>模型：{modelConfigs.find((item) => item.id === modelConfigId)?.name ?? '未选择'}</span><button type="button" onClick={() => setSetupStep(1)}>更换模型</button></div>}
            <label>智能体名称<input value={agentName} onChange={(event) => setAgentName(event.target.value)} required/></label>
            {editingAgentId && <label>使用模型<select value={modelConfigId} onChange={(event) => setModelConfigId(event.target.value)} required><option value="">请选择模型</option>{modelConfigs.map((item) => <option key={item.id} value={item.id}>{item.name}{!item.credential_configured ? '（缺少凭据）' : ''}</option>)}</select><small className="plugin-helper">共享模型的地址、型号或密钥变更会应用到所有关联智能体。</small><button className="inline-link-button" type="button" onClick={() => navigate('/plugins?tab=Model')}>管理模型</button></label>}
            <label>运行方式<select value={mode} onChange={(event) => setMode(event.target.value as 'react' | 'plan_execute')}><option value="react">灵感对话（默认）</option><option value="plan_execute">计划并执行</option></select></label>
            <label>上下文窗口（Token）<input type="number" min={1024} max={2000000} step={1024} value={contextWindow} onChange={(event) => setContextWindow(Number(event.target.value))}/></label>
            <label>系统提示词<textarea className="prompt-input" value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} rows={4} placeholder="支持 {{variable}} 变量，例如：称呼用户为 {{user_name}}"/></label>
            {systemPrompt.match(/\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}/g)?.filter((token, index, all) => all.indexOf(token) === index).map((token) => { const variable = token.replace(/[{}\s]/g, ''); return <label className="prompt-variable" key={variable}>{variable}<input value={promptVariables[variable] ?? ''} onChange={(event) => setPromptVariables((values) => ({ ...values, [variable]: event.target.value }))} placeholder={`填写 ${variable}`}/></label>; })}
            {editingAgentId && <div className="prompt-preview-actions"><button type="button" onClick={previewPrompt}>预览解析结果（本地，不调用模型）</button></div>}{promptPreview && <pre className="prompt-preview">{promptPreview}</pre>}
            {legacySkillsWarning && <div className="plugin-message error">旧版 Skills 目录迁移未完成，原配置路径已保留；请检查文件夹权限，并将需要的 Skill 从 ZIP 加载到插件库后重新勾选。</div>}
            <div className="skill-choice-list"><small>可委派的智能体（默认无授权；仅允许所选 Agent 接收任务）</small>{agentChoices.filter((item) => item.id !== editingAgentId).map((agent) => <label key={agent.id} className="skill-choice"><input type="checkbox" checked={selectedAgents.includes(agent.id)} onChange={(event) => setSelectedAgents((items) => event.target.checked ? [...items, agent.id] : items.filter((id) => id !== agent.id))}/><span><strong>{agent.name}</strong><small>{agent.model_config_name ?? agent.model}</small></span></label>)}{!agentChoices.some((item) => item.id !== editingAgentId) && <small>暂无其他智能体；创建后可以配置委派目标。</small>}</div>
            <label className="skill-choice"><input type="checkbox" checked={sandboxCodeEnabled} onChange={(event) => setSandboxCodeEnabled(event.target.checked)}/><span><strong>允许在 CubeSandbox 运行生成的 Python</strong><small>沙箱未配置时不会回退到本机；每次运行都需要审批。</small></span></label>
            <div className="skill-choice-list"><small>为此智能体启用已加载的 Skills。</small>{skillChoices.map((skill) => <label key={skill.name} className="skill-choice"><input type="checkbox" checked={selectedSkills.includes(skill.name)} onChange={(event) => setSelectedSkills((items) => event.target.checked ? [...items, skill.name] : items.filter((name) => name !== skill.name))}/><span><strong>{skill.name}</strong><small>{skill.description}</small></span></label>)}{!skillChoices.length && <small>暂无已加载 Skill。请先在插件库导入 ZIP。</small>}</div>
            <div className="skill-choice-list"><small>为此智能体启用 MCP 工具。</small>{mcpChoices.map((tool) => <label key={tool.id} className="skill-choice"><input type="checkbox" checked={selectedMcpTools.includes(tool.id)} onChange={(event) => setSelectedMcpTools((items) => event.target.checked ? [...items, tool.id] : items.filter((id) => id !== tool.id))}/><span><strong>{tool.name}</strong><small>{tool.description} · {tool.risk === 'read_only' ? '只读' : '需批准'}</small></span></label>)}{!mcpChoices.length && <small>暂无已连接 MCP 工具。请在插件库配置并连接。</small>}</div>
            <div className="skill-choice-list"><small>为此智能体启用本地 Python Tools。</small>{localToolChoices.map((tool) => <label key={tool.id} className="skill-choice"><input type="checkbox" checked={selectedLocalTools.includes(tool.id)} onChange={(event) => setSelectedLocalTools((items) => event.target.checked ? [...items, tool.id] : items.filter((id) => id !== tool.id))}/><span><strong>{tool.name}</strong><small>{tool.description} · {tool.risk === 'read_only' ? '只读' : '需批准'}</small></span></label>)}{!localToolChoices.length && <small>暂无已加载 Tool。请先在插件库导入或编写。</small>}</div>
            <div className="skill-choice-list"><small>为此智能体授权知识库；授权后会自动检索相关内容。</small>{knowledgeBaseChoices.map((knowledgeBase) => <label key={knowledgeBase.id} className="skill-choice"><input type="checkbox" checked={selectedKnowledgeBases.includes(knowledgeBase.id)} onChange={(event) => setSelectedKnowledgeBases((items) => event.target.checked ? [...items, knowledgeBase.id] : items.filter((id) => id !== knowledgeBase.id))}/><span><strong>{knowledgeBase.name}</strong><small>{knowledgeBase.document_count} 个文档 · {knowledgeBase.status === 'has_errors' ? '包含解析失败文档' : '可用'}</small></span></label>)}{!knowledgeBaseChoices.length && <small>暂无知识库。请先在知识库页面导入资料。</small>}</div>
            <div className="skill-choice-list"><small>为此智能体授权数据库连接；只读连接禁止写入，变更连接仍需逐次审批。</small>{databaseChoices.map((database) => <label key={database.id} className="skill-choice"><input type="checkbox" checked={selectedDatabaseConnections.includes(database.id)} onChange={(event) => setSelectedDatabaseConnections((items) => event.target.checked ? [...items, database.id] : items.filter((id) => id !== database.id))}/><span><strong>{database.name}</strong><small>{database.db_type.toUpperCase()} · {database.readonly ? '只读' : '写操作逐次审批'} · {database.last_tested_at ? '已测试' : '未测试'}{database.db_type === 'mysql' && !database.password_configured ? ' · 缺少密码' : ''}</small></span></label>)}{!databaseChoices.length && <small>暂无数据库连接。请添加并测试连接。</small>}<button type="button" className="inline-link-button" onClick={() => navigate('/databases')}>管理数据库连接</button></div>

            {editingAgentId && promptVersions.length > 0 && <details className="prompt-history"><summary>历史版本（{promptVersions.length}）</summary>{promptVersions.map((version) => <div className="prompt-version" key={version.id}><span>{version.content || '（空提示词）'}<small>{new Date(version.created_at).toLocaleString()}</small></span><button type="button" onClick={() => restorePrompt(version.id)}>恢复</button></div>)}</details>}
            {editingAgentId && <div className="connection-row"><button type="button" onClick={testAgentConnection}>测试关联模型</button><small>{connectionStatus}</small></div>}
            <div className="wizard-actions">{editingAgentId && <span className={`save-indicator ${saveStatus}`}>{saveStatus === 'saving' ? '保存中…' : saveStatus === 'saved' ? '已自动保存' : saveStatus === 'error' ? '保存失败' : '自动保存已开启'}</span>}{saveStatus === 'error' && <button type="button" className="secondary-button" onClick={() => setSaveRevision((revision) => revision + 1)}>重试保存</button>}{isNew && <button type="button" className="secondary-button" onClick={() => setSetupStep(1)}>上一步</button>}{isNew && <button className="primary-button" type="button" disabled={!modelConfigId} onClick={() => void createAgent()}>创建智能体 <span>→</span></button>}</div>
          </>}
        </form>
        </div>
        {editingAgentId && <AgentDebugChat key={editingAgentId} agentId={editingAgentId} config={{
          model_config_id: modelConfigId, mode, context_window: contextWindow, system_prompt: systemPrompt,
          prompt_variables: promptVariables, selected_skills: selectedSkills,
          selected_mcp_tools: selectedMcpTools, selected_local_tools: selectedLocalTools,
          selected_knowledge_bases: selectedKnowledgeBases,
          selected_database_connections: selectedDatabaseConnections, selected_agents: selectedAgents, sandbox_code_enabled: sandboxCodeEnabled,
        }}/>}
        </div>
      )}
    </section>
  );
}
