import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api';

type DatabaseConnection = {
  id: string; name: string; db_type: 'sqlite' | 'mysql'; sqlite_path: string;
  host: string; port: number; database_name: string; username: string;
  ssl: boolean; readonly: boolean; password_configured: boolean;
  last_tested_at: string | null;
};
type SchemaResult = { tables: { name: string; columns: { name: string; type: string }[] }[] };

type Form = {
  name: string; db_type: 'sqlite' | 'mysql'; sqlite_path: string;
  host: string; port: string; database_name: string; username: string;
  password: string; readonly: boolean; ssl: boolean;
};
const emptyForm: Form = {
  name: '', db_type: 'sqlite', sqlite_path: '', host: '', port: '3306',
  database_name: '', username: '', password: '', readonly: true, ssl: true,
};

export function DatabasePage() {
  const [connections, setConnections] = useState<DatabaseConnection[]>([]);
  const [form, setForm] = useState<Form>(emptyForm);
  const [editingId, setEditingId] = useState('');
  const [schema, setSchema] = useState<Record<string, SchemaResult>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const load = async () => setConnections(await api<DatabaseConnection[]>('/databases'));
  useEffect(() => { load().catch((reason: Error) => setError(reason.message)); }, []);
  const patchForm = (patch: Partial<Form>) => setForm((current) => ({ ...current, ...patch }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(''); setMessage('');
    try {
      if (editingId) {
        const { password, db_type: _type, ...config } = form;
        await api(`/databases/${editingId}`, { method: 'PATCH', body: JSON.stringify({
          ...config, port: Number(config.port),
        }) });
        if (password.trim()) await api(`/databases/${editingId}/credentials`, {
          method: 'PUT', body: JSON.stringify({ password }),
        });
        setMessage('连接配置已保存。请重新测试连接。');
      } else {
        const payload = form.db_type === 'sqlite'
          ? { name: form.name, db_type: form.db_type, sqlite_path: form.sqlite_path, readonly: form.readonly }
          : { name: form.name, db_type: form.db_type, host: form.host, port: Number(form.port), database_name: form.database_name, username: form.username, password: form.password, readonly: form.readonly, ssl: form.ssl };
        await api('/databases', { method: 'POST', body: JSON.stringify(payload) });
        setMessage('数据库连接已添加。');
      }
      setForm(emptyForm); setEditingId(''); await load();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  };
  const edit = (item: DatabaseConnection) => {
    setEditingId(item.id);
    setForm({ name: item.name, db_type: item.db_type, sqlite_path: item.sqlite_path,
      host: item.host, port: String(item.port), database_name: item.database_name,
      username: item.username, password: '', readonly: item.readonly, ssl: item.ssl });
  };
  const test = async (item: DatabaseConnection) => {
    setError(''); setMessage('正在测试只读连接…');
    try { await api(`/databases/${item.id}/test-connection`, { method: 'POST' }); setMessage(`「${item.name}」连接测试成功。`); await load(); }
    catch (reason) { setMessage(''); setError((reason as Error).message); }
  };
  const showSchema = async (item: DatabaseConnection) => {
    setError('');
    try { const result = await api<SchemaResult>(`/databases/${item.id}/schema`); setSchema((current) => ({ ...current, [item.id]: result })); }
    catch (reason) { setError((reason as Error).message); }
  };
  const remove = async (item: DatabaseConnection) => {
    if (!window.confirm(`删除连接「${item.name}」？不会删除源数据库文件或外部数据库。`)) return;
    setError('');
    try { await api(`/databases/${item.id}`, { method: 'DELETE' }); setMessage('连接配置已删除。'); await load(); }
    catch (reason) { setError((reason as Error).message); }
  };

  return <section className="database-page">
    <div className="database-heading"><div className="eyebrow">CONNECTIONS</div><h1>数据库</h1><p>连接本地 SQLite 或 MySQL，Agent 仅能使用明确授权的连接。</p></div>
    {error && <div className="plugin-message error" role="alert">{error}<button type="button" onClick={() => setError('')}>×</button></div>}
    {message && <div className="plugin-message" role="status">{message}<button type="button" onClick={() => setMessage('')}>×</button></div>}
    <div className="database-layout">
      <form className="plugin-list database-form" onSubmit={submit}>
        <div className="plugin-list-head"><h2>{editingId ? '编辑连接' : '添加数据库'}</h2></div>
        <label>连接名称<input required maxLength={100} value={form.name} onChange={(event) => patchForm({ name: event.target.value })}/></label>
        {!editingId && <label>数据库类型<select value={form.db_type} onChange={(event) => patchForm({ db_type: event.target.value as Form['db_type'] })}><option value="sqlite">本地 SQLite 文件</option><option value="mysql">MySQL / MariaDB</option></select></label>}
        {form.db_type === 'sqlite' ? <label>已有 SQLite 文件的完整路径<input required value={form.sqlite_path} onChange={(event) => patchForm({ sqlite_path: event.target.value })} placeholder="D:\\data\\app.sqlite3"/><small>必须是已存在的数据库文件；不会删除或改写文件。</small></label> : <>
          <label>主机<input required value={form.host} onChange={(event) => patchForm({ host: event.target.value })} placeholder="db.example.com"/></label>
          <label>端口<input required type="number" min={1} max={65535} value={form.port} onChange={(event) => patchForm({ port: event.target.value })}/></label>
          <label>数据库名<input required value={form.database_name} onChange={(event) => patchForm({ database_name: event.target.value })}/></label>
          <label>用户名<input required value={form.username} onChange={(event) => patchForm({ username: event.target.value })}/></label>
          <label>密码<input type="password" required={!editingId} value={form.password} onChange={(event) => patchForm({ password: event.target.value })} placeholder={editingId ? '留空保持现有密码' : '密码只保存到系统凭据库'} autoComplete="new-password"/></label>
          <label className="database-checkbox"><input type="checkbox" checked={form.ssl} onChange={(event) => patchForm({ ssl: event.target.checked })}/>验证 TLS 证书</label>
        </>}
        <label className="database-checkbox"><input type="checkbox" checked={form.readonly} onChange={(event) => patchForm({ readonly: event.target.checked })}/>只读连接（推荐）</label>
        {!form.readonly && <div className="plugin-security-warning">该连接允许 Agent 请求数据变更；每次变更仍须逐次审批，且数据库账户权限仍然有效。</div>}
        <div className="database-form-actions"><button className="primary-button" type="submit" disabled={busy}>{busy ? '保存中…' : editingId ? '保存连接' : '添加连接'}</button>{editingId && <button className="secondary-button" type="button" onClick={() => { setEditingId(''); setForm(emptyForm); }}>取消</button>}</div>
      </form>
      <section className="plugin-list database-connection-list"><div className="plugin-list-head"><h2>已配置连接</h2><small>{connections.length}</small></div>
        {connections.map((item) => <article className="database-card" key={item.id}><div className="database-card-main"><strong>{item.name}</strong><small>{item.db_type === 'sqlite' ? item.sqlite_path : `${item.username}@${item.host}:${item.port}/${item.database_name}`}</small><div className="database-badges"><span>{item.db_type === 'sqlite' ? 'SQLite' : 'MySQL'}</span><span>{item.readonly ? '只读' : '变更需审批'}</span><span>{item.db_type === 'mysql' ? item.password_configured ? '密码已保存' : '缺少密码' : '本地文件'}</span><span>{item.last_tested_at ? `已测试 ${new Date(item.last_tested_at).toLocaleString()}` : '未测试'}</span></div></div><div className="database-actions"><button type="button" onClick={() => void test(item)}>测试</button><button type="button" onClick={() => void showSchema(item)}>查看结构</button><button type="button" onClick={() => edit(item)}>编辑</button><button type="button" onClick={() => void remove(item)}>删除</button></div>{schema[item.id] && <div className="database-schema">{schema[item.id].tables.map((table) => <div key={table.name}><strong>{table.name}</strong><small>{table.columns.map((column) => `${column.name} (${column.type || '无类型'})`).join(' · ')}</small></div>)}{!schema[item.id].tables.length && <small>没有可展示的表</small>}</div>}</article>)}
        {!connections.length && <div className="plugin-empty">添加本地 SQLite 或 MySQL 连接以供 Agent 按授权查询。</div>}
      </section>
    </div>
  </section>;
}
