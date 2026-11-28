import { FormEvent, useEffect, useState } from 'react';
import { api } from '../../../../frontend/src/api';
import './dify.css';

type ToolSummary = { name: string; provider: string; label: string; description: string; parameters: Record<string, { type: string; description?: string }> };
type PluginPreview = {
  token: string; plugin_id: string; version: string; label: string; description: string; digest: string;
  source: string; warning: string; dependencies: string[]; network_required: boolean; identifier?: string;
  tools: ToolSummary[]; credentials: Record<string, { type: string; label: string; required: boolean }>;
};
type Installed = {
  id: string; plugin_id: string; version: string; label: string; description: string; digest: string; source: string;
  status: string; network_enabled: boolean; tools: ToolSummary[]; credentials: Record<string, { type: string; label: string; required: boolean; configured?: boolean }>;
};
type Result = { plugin_id: string; version: string; label: string; description: string; category: string; identifier: string };

type Tab = 'official' | 'search' | 'installed';

export function DifyPlugins() {
  const [tab, setTab] = useState<Tab>('official');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Result[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searching, setSearching] = useState(false);
  const [installed, setInstalled] = useState<Installed[]>([]);
  const [preview, setPreview] = useState<PluginPreview | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, Record<string, string>>>({});

  const refreshInstalled = async () => setInstalled(await api<Installed[]>('/dify-plugins/installed'));
  useEffect(() => { void refreshInstalled().catch((error: Error) => setMessage(error.message)); }, []);

  const search = async (event?: FormEvent, nextPage = 1) => {
    event?.preventDefault();
    setSearching(true); setMessage(''); setPage(nextPage);
    try {
      const response = await api<{ items: Result[]; total: number }>('/dify-plugins/market', {
        method: 'POST', body: JSON.stringify({ query: query.trim(), page: nextPage }),
      });
      setResults(response.items); setTotal(response.total); setTab('search');
      if (response.items.length === 0) setMessage('没有找到匹配的工具插件；也可以粘贴插件详情链接或导入 .difypkg。');
    } catch (error) { setMessage((error as Error).message); setResults([]); }
    finally { setSearching(false); }
  };

  const inspectLink = async (value: string) => {
    setBusy(true); setMessage('');
    try {
      const next = await api<PluginPreview>('/dify-plugins/preview/market', { method: 'POST', body: JSON.stringify({ link: value.trim() }) });
      setPreview(next);
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };

  const inspectFile = async (file?: File) => {
    if (!file) return;
    setBusy(true); setMessage('');
    try {
      const form = new FormData(); form.set('file', file);
      setPreview(await api<PluginPreview>('/dify-plugins/preview/file', { method: 'POST', body: form }));
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };

  const install = async () => {
    if (!preview) return;
    setBusy(true); setMessage('正在构建并检查独立插件运行环境…');
    try {
      await api('/dify-plugins/install', { method: 'POST', body: JSON.stringify({ token: preview.token, network_enabled: false }) });
      setPreview(null); await refreshInstalled(); setTab('installed'); setMessage('安装完成。请在 Agent 配置中单独启用需要的工具。');
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };

  const saveSettings = async (item: Installed) => {
    const credentials = credentialDrafts[item.id] ?? {};
    setBusy(true); setMessage('');
    try {
      await api(`/dify-plugins/installed/${item.id}/settings`, {
        method: 'PUT', body: JSON.stringify({ credentials, network_enabled: item.network_enabled }),
      });
      setCredentialDrafts((all) => ({ ...all, [item.id]: {} }));
      await refreshInstalled(); setMessage(`${item.label} 设置已保存。`);
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };

  const uninstall = async (item: Installed) => {
    if (!window.confirm(`卸载 ${item.label} ${item.version}？这会移除它提供的工具和已保存凭据。`)) return;
    setBusy(true);
    try { await api(`/dify-plugins/installed/${item.id}`, { method: 'DELETE' }); await refreshInstalled(); setMessage('插件已卸载。'); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };

  return <div className="dify-plugin-page">
    <div className="plugin-tabs" role="tablist" aria-label="Dify 插件页面">
      <button type="button" aria-selected={tab === 'official'} className={tab === 'official' ? 'active' : ''} onClick={() => setTab('official')}>官方市场</button>
      <button type="button" aria-selected={tab === 'search'} className={tab === 'search' ? 'active' : ''} onClick={() => setTab('search')}>搜索和安装</button>
      <button type="button" aria-selected={tab === 'installed'} className={tab === 'installed' ? 'active' : ''} onClick={() => { setTab('installed'); void refreshInstalled(); }}>已安装（{installed.length}）</button>
    </div>

    {tab === 'official' && <section className="dify-official-market" aria-label="Dify 官方市场">
      <div className="dify-market-actions"><strong>官方 Dify Marketplace</strong>
        <p>官方市场用于浏览。安装到本系统请到「搜索和安装」预检插件包。</p>
        <a className="secondary-button" href="https://marketplace.dify.ai/" target="_blank" rel="noopener noreferrer">在浏览器中打开官方市场 ↗</a>
      </div>
      <small>官网禁止网页内嵌。可在新标签页浏览，也可以留在本系统搜索和安装。</small>
    </section>}

    {tab === 'search' && <section className="dify-market-search">
      <div className="plugin-security-warning">Marketplace 页面上的安装按钮不会安装到本系统。安装前会固定版本并检查兼容性；插件代码将在本机 Docker 容器运行。Docker 不可用时不会执行插件或退回本机 Python。</div>
      <form className="dify-market-search-form" onSubmit={(event) => void search(event)}>
        <label className="dify-query">搜索 Dify 工具插件<input value={query} maxLength={100} placeholder="名称、发布者/名称，或粘贴官方详情链接" onChange={(event) => setQuery(event.target.value)} /></label>
        <button className="primary-button" type="submit" disabled={searching}>{searching ? '搜索中…' : '搜索市场'}</button>
        <label className="plugin-file-button">导入 .difypkg<input type="file" accept=".difypkg,application/zip" disabled={busy} onChange={(event) => { void inspectFile(event.target.files?.[0]); event.currentTarget.value = ''; }} /></label>
      </form>
      <form className="dify-link-form" onSubmit={(event) => { event.preventDefault(); void inspectLink(link); }}>
        <label>按官方详情链接或发布者/插件名安装<input value={link} maxLength={2048} placeholder="https://marketplace.dify.ai/plugin/langgenius/tavily" onChange={(event) => setLink(event.target.value)} /></label>
        <button type="submit" disabled={busy || !link.trim()}>{busy ? '检查中…' : '检查插件'}</button>
      </form>
      {message && <p className="plugin-helper" role="status">{message}</p>}
      {searching && <p role="status">正在读取 Dify 官方市场…</p>}
      {!searching && results.length > 0 && <>
        <p className="plugin-helper">显示 {results.length} 项，共 {total} 项；这些是市场信息，兼容性以本机预检结果为准。</p>
        <div className="dify-market-results">{results.map((item) => <article className="plugin-card" key={item.identifier}>
          <div><strong>{item.label}</strong><small>{item.plugin_id} · {item.category} · {item.version}</small><small>{item.description || '市场没有提供描述。'}</small></div>
          <button type="button" disabled={busy} onClick={() => void inspectLink(item.identifier)}>预检并查看安装选项</button>
        </article>)}</div>
        <div className="dify-pagination"><button type="button" disabled={page <= 1 || searching} onClick={() => void search(undefined, page - 1)}>上一页</button><span>第 {page} 页</span><button type="button" disabled={searching || page * 20 >= total} onClick={() => void search(undefined, page + 1)}>下一页</button></div>
      </>}
    </section>}

    {tab === 'installed' && <section className="dify-installed-list">
      <div className="plugin-security-warning">未设置的 Agent 不会调用这些工具。工具均按可能有副作用处理并遵循现有对话审批设置。允许联网会让插件访问网络，并可能传出输入和它自己的凭据。</div>
      {installed.length === 0 && <p className="plugin-empty">尚未安装 Dify 工具插件。</p>}
      {installed.map((item) => <article className="plugin-server-card" key={item.id}>
        <div className="plugin-card"><div><strong>{item.label} <small>{item.version}</small></strong><small>{item.plugin_id} · {item.status === 'ready' ? '运行器就绪' : item.status === 'cleanup_pending' ? '凭据清理失败，请再次点击卸载' : '缺少容器镜像，请重新检查并安装'}</small><small>SHA-256：{item.digest}</small></div>
          {item.status === 'image_missing' && <button type="button" disabled={busy} onClick={() => { setLink(item.plugin_id); setTab('search'); void inspectLink(item.plugin_id); }}>重新检查并修复</button>}
          <button type="button" className="danger-button" disabled={busy} onClick={() => void uninstall(item)}>卸载</button></div>
        <div className="dify-plugin-tools">{item.tools.map((tool) => <div key={tool.name}><strong>{tool.label}</strong><small>{tool.description} · 逐次审批</small></div>)}</div>
        <form className="dify-settings" onSubmit={(event) => { event.preventDefault(); void saveSettings(item); }}>
          <h3>插件凭据</h3>
          {Object.entries(item.credentials).length === 0 && <small>此插件未声明 API 凭据。</small>}
          {Object.entries(item.credentials).map(([name, field]) => <label key={name}>{field.label}{field.configured && <small>（已保存；留空不修改）</small>}<input type={field.type === 'secret-input' ? 'password' : 'text'} autoComplete="off" maxLength={4096} value={credentialDrafts[item.id]?.[name] ?? ''} onChange={(event) => setCredentialDrafts((all) => ({ ...all, [item.id]: { ...all[item.id], [name]: event.target.value } }))} required={field.required && !field.configured} placeholder={field.configured ? '密钥已安全保存；输入新值可替换' : field.required ? '必填' : '选填'} /></label>)}
          <label className="plugin-checkbox"><input type="checkbox" checked={item.network_enabled} onChange={(event) => setInstalled((all) => all.map((candidate) => candidate.id === item.id ? { ...candidate, network_enabled: event.target.checked } : candidate))} />允许插件容器访问外网（Docker 网络不是域名白名单）</label>
          <button type="submit" disabled={busy || item.status !== 'ready'}>保存设置</button>
        </form>
      </article>)}
    </section>}

    {preview && <div className="dify-confirm-backdrop" role="presentation"><section className="dify-confirm" role="dialog" aria-modal="true" aria-labelledby="dify-preview-title">
      <button type="button" className="dify-close" aria-label="关闭安装预览" onClick={() => setPreview(null)}>×</button>
      <h2 id="dify-preview-title">安装前预检</h2><p><strong>{preview.label}</strong> · {preview.plugin_id} · {preview.version}</p>
      <p>{preview.description || '市场未提供描述。'}</p><small>固定包 SHA-256：{preview.digest}</small>
      <h3>将安装的工具</h3><ul>{preview.tools.map((tool) => <li key={tool.name}><strong>{tool.label}</strong> — {tool.description}</li>)}</ul>
      <h3>所需凭据</h3><p>{Object.keys(preview.credentials).length ? Object.values(preview.credentials).map((field) => `${field.label}${field.required ? '（必填）' : ''}`).join('、') : '插件未声明凭据。'}</p>
      <p>插件依赖：{preview.dependencies.length ? preview.dependencies.join('、') : '无额外 requirements.txt 依赖'}。构建时会从 Python 包源获取 wheel；插件运行时默认禁网。</p>
      <div className="plugin-security-warning">{preview.warning}</div>
      <label className="plugin-checkbox"><input type="checkbox" checked={false} readOnly disabled /> 安装后联网默认关闭，可到「已安装」单独授权。</label>
      {message && <p role="status">{message}</p>}
      <div className="dify-confirm-actions"><button type="button" disabled={busy} onClick={() => setPreview(null)}>取消</button><button className="primary-button" type="button" disabled={busy} onClick={() => void install()}>{busy ? '安装中…' : '确认安装'}</button></div>
    </section></div>}
  </div>;
}

type DifyTool = { id: string; name: string; description: string; risk: string };

export function DifyToolChoices({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [tools, setTools] = useState<DifyTool[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { let active = true; api<DifyTool[]>('/dify-plugins/tools').then((items) => { if (active) setTools(items); }).catch((reason: Error) => { if (active) setError(reason.message); }); return () => { active = false; }; }, []);
  return <div className="skill-choice-list dify-tool-choices"><small>为此智能体单独启用 Dify 插件工具；工具调用遵循现有审批策略。</small>
    {tools.map((tool) => <label className="skill-choice" key={tool.id}><input type="checkbox" checked={selected.includes(tool.id)} onChange={(event) => onChange(event.target.checked ? [...selected, tool.id] : selected.filter((id) => id !== tool.id))}/><span><strong>{tool.name}</strong><small>{tool.description} · 可能修改数据需审批</small></span></label>)}
    {error && <small role="status">Dify 插件工具加载失败：{error}</small>}{!tools.length && !error && <small>暂无已安装且就绪的 Dify 工具。</small>}
  </div>;
}
