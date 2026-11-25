import { FormEvent, useEffect, useState } from 'react';
import { api } from '../api';

type Task = {
  id: string; name: string; script_hash: string; confirmed_hash: string | null; confirmed: boolean;
  cron_expression: string; timezone: string; timeout_seconds: number; enabled: boolean;
  next_run_at: string | null; last_run_at: string | null; created_at: string; updated_at: string;
};
type TaskDetail = Task & { script: string };
type TaskRun = {
  id: string; trigger: 'manual' | 'scheduled'; scheduled_at: string | null; started_at: string;
  finished_at: string | null; duration_ms: number | null; status: string; exit_code: number | null;
  stdout: string; stderr: string; truncated: boolean; summary: string;
};
type TaskForm = { name: string; script: string; cron_expression: string; timezone: string; timeout_seconds: number };
const newForm: TaskForm = { name: '', script: '', cron_expression: '0 9 * * 1-5', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', timeout_seconds: 300 };

export function ScheduledTasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [form, setForm] = useState<TaskForm>(newForm);
  const [editingId, setEditingId] = useState('');
  const [nextRunPreview, setNextRunPreview] = useState('');
  const [runsByTask, setRunsByTask] = useState<Record<string, TaskRun[]>>({});
  const [expanded, setExpanded] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  const load = async () => setTasks(await api<Task[]>('/scheduled-tasks'));
  useEffect(() => { load().catch((reason: Error) => setError(reason.message)); }, []);
  useEffect(() => {
    if (!tasks.some((task) => expanded.includes(task.id) && runsByTask[task.id]?.[0]?.status === 'running')) return;
    const timer = window.setInterval(() => {
      for (const id of expanded) void loadRuns(id);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [tasks, expanded, runsByTask]);

  const patch = (values: Partial<TaskForm>) => setForm((current) => ({ ...current, ...values }));
  const loadRuns = async (taskId: string) => {
    const response = await api<{ items: TaskRun[] }>(`/scheduled-tasks/${taskId}/runs?limit=50`);
    setRunsByTask((current) => ({ ...current, [taskId]: response.items }));
  };
  const reset = () => { setEditingId(''); setForm(newForm); setNextRunPreview(''); };
  const validate = async () => {
    try {
      const result = await api<{ next_run_at: string }>('/scheduled-tasks/validate', {
        method: 'POST', body: JSON.stringify({ script: form.script, cron_expression: form.cron_expression, timezone: form.timezone }),
      });
      setNextRunPreview(result.next_run_at); setError(''); setMessage('脚本语法和 Cron 校验通过。');
    } catch (reason) { setError((reason as Error).message); }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setSaving(true); setError(''); setMessage('');
    try {
      const payload = { ...form, timeout_seconds: Number(form.timeout_seconds) };
      if (editingId) await api(`/scheduled-tasks/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      else await api('/scheduled-tasks', { method: 'POST', body: JSON.stringify(payload) });
      setMessage(editingId ? '任务已保存。若脚本变更，请重新确认后启用。' : '任务已创建，默认停用。');
      reset(); await load();
    } catch (reason) { setError((reason as Error).message); }
    finally { setSaving(false); }
  };
  const edit = async (task: Task) => {
    try {
      const detail = await api<TaskDetail>(`/scheduled-tasks/${task.id}`);
      setEditingId(task.id);
      setForm({ name: detail.name, script: detail.script, cron_expression: detail.cron_expression, timezone: detail.timezone, timeout_seconds: detail.timeout_seconds });
      setNextRunPreview(''); window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (reason) { setError((reason as Error).message); }
  };
  const confirmAction = async (task: Task, action: 'enable' | 'run') => {
    const explanation = action === 'enable'
      ? `确认当前脚本版本并启用「${task.name}」？当前哈希 ${task.script_hash.slice(0, 12)}… 将按 Cron 自动运行。脚本不是沙箱，会以本机后端账户权限执行。`
      : `确认当前脚本版本并立即运行「${task.name}」？哈希 ${task.script_hash.slice(0, 12)}… 脚本不是沙箱，会以本机后端账户权限执行。`;
    if (!window.confirm(explanation)) return;
    setError(''); setMessage('');
    try {
      if (action === 'enable') {
        await api(`/scheduled-tasks/${task.id}/enable`, { method: 'POST', body: JSON.stringify({ confirmed: true, script_hash: task.script_hash }) });
        setMessage('任务已确认并启用。');
      } else {
        await api(`/scheduled-tasks/${task.id}/run`, { method: 'POST', body: JSON.stringify({ confirmed: true, script_hash: task.script_hash }) });
        setMessage('已开始手动运行。');
        if (!expanded.includes(task.id)) setExpanded((items) => [...items, task.id]);
        await loadRuns(task.id);
      }
      await load();
    } catch (reason) { setError((reason as Error).message); }
  };
  const disable = async (task: Task) => {
    try { await api(`/scheduled-tasks/${task.id}/disable`, { method: 'POST' }); setMessage('已暂停新一轮调度；当前运行会继续。'); await load(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const remove = async (task: Task) => {
    if (!window.confirm(`删除「${task.name}」及其应用内运行历史？`)) return;
    try { await api(`/scheduled-tasks/${task.id}`, { method: 'DELETE' }); setMessage('任务和运行历史已删除。'); await load(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const toggleRuns = async (taskId: string) => {
    if (expanded.includes(taskId)) { setExpanded((items) => items.filter((id) => id !== taskId)); return; }
    setExpanded((items) => [...items, taskId]);
    try { await loadRuns(taskId); } catch (reason) { setError((reason as Error).message); }
  };

  return <section className="scheduled-tasks-page">
    <div className="scheduled-page-heading"><div className="eyebrow">LOCAL PYTHON SCHEDULER</div><h1>定时任务</h1><p>通用本地 Python 脚本任务，不绑定 Agent 或特定操作。</p></div>
    <div className="plugin-security-warning task-security-warning">安全提示：本机子进程不是安全沙箱（非沙箱）。脚本以本机后端账户权限运行，只执行你信任的代码；不要在脚本或输出中放入密钥。</div>
    {error && <div className="plugin-message error" role="alert">{error}<button type="button" onClick={() => setError('')}>×</button></div>}
    {message && <div className="plugin-message" role="status">{message}<button type="button" onClick={() => setMessage('')}>×</button></div>}
    <div className="scheduled-layout">
      <form className="plugin-list scheduled-form" onSubmit={submit}>
        <div className="plugin-list-head"><h2>{editingId ? '编辑任务' : '新建任务'}</h2></div>
        <label>任务名称<input required maxLength={100} value={form.name} onChange={(event) => patch({ name: event.target.value })}/></label>
        <label>Python 脚本<textarea required maxLength={100000} rows={12} spellCheck={false} value={form.script} onChange={(event) => patch({ script: event.target.value })} placeholder={'print("Hello from scheduled task")'}/></label>
        <div className="scheduled-form-row"><label>Cron（五段式）<input required value={form.cron_expression} onChange={(event) => patch({ cron_expression: event.target.value })} placeholder="0 9 * * 1-5"/></label><label>IANA 时区<input required value={form.timezone} onChange={(event) => patch({ timezone: event.target.value })} placeholder="Asia/Shanghai"/></label></div>
        <label>执行超时（秒）<input required type="number" min={1} max={3600} value={form.timeout_seconds} onChange={(event) => patch({ timeout_seconds: Number(event.target.value) })}/></label>
        <button className="secondary-button task-validate" type="button" onClick={() => void validate()}>校验脚本与时间</button>
        {nextRunPreview && <small className="plugin-helper">预览下次运行：{new Date(nextRunPreview).toLocaleString()}（{form.timezone}）</small>}
        <div className="scheduled-actions"><button className="primary-button" type="submit" disabled={saving}>{saving ? '保存中…' : editingId ? '保存任务' : '创建停用任务'}</button>{editingId && <button className="secondary-button" type="button" onClick={reset}>取消编辑</button>}</div>
      </form>
      <section className="plugin-list scheduled-task-list"><div className="plugin-list-head"><h2>任务列表</h2><small>{tasks.length}</small></div>
        {tasks.map((task) => <article className="scheduled-task-card" key={task.id}>
          <div className="scheduled-task-header"><div><strong>{task.name}</strong><small>{task.cron_expression} · {task.timezone} · 超时 {task.timeout_seconds}s</small></div><span className={`task-status ${task.enabled ? 'enabled' : ''}`}>{task.enabled ? '已启用' : '已暂停'}</span></div>
          <div className="task-next-run">下次：{task.enabled && task.next_run_at ? new Date(task.next_run_at).toLocaleString() : '无'} · 最近：{task.last_run_at ? new Date(task.last_run_at).toLocaleString() : '无'}</div>
          <div className="task-confirm-state">{task.confirmed ? `已确认脚本版本 ${task.script_hash.slice(0, 12)}…` : '脚本尚未确认；启用或手动运行前需要确认'}</div>
          <div className="scheduled-task-actions"><button type="button" onClick={() => void edit(task)}>编辑</button>{task.enabled ? <button type="button" onClick={() => void disable(task)}>暂停</button> : <button type="button" onClick={() => void confirmAction(task, 'enable')}>确认启用</button>}<button type="button" onClick={() => void confirmAction(task, 'run')}>立即运行</button><button type="button" onClick={() => void toggleRuns(task.id)}>运行记录</button><button type="button" onClick={() => void remove(task)}>删除</button></div>
          {expanded.includes(task.id) && <div className="scheduled-run-list">{(runsByTask[task.id] ?? []).map((run) => <article className="scheduled-run" key={run.id}><div className="scheduled-run-header"><strong>{run.trigger === 'manual' ? '手动' : '定时'} · {run.status}</strong><small>{new Date(run.started_at).toLocaleString()} · {run.duration_ms == null ? '运行中' : `${Math.round(run.duration_ms)} ms`} · 退出码 {run.exit_code ?? '—'}</small></div>{run.truncated && <small className="task-truncated">输出已截断</small>}{run.stdout && <details><summary>标准输出</summary><pre>{run.stdout}</pre></details>}{run.stderr && <details><summary>错误输出</summary><pre>{run.stderr}</pre></details>}{run.summary && <small>{run.summary}</small>}</article>)}{!(runsByTask[task.id] ?? []).length && <small className="plugin-helper">暂无运行记录</small>}</div>}
        </article>)}
        {!tasks.length && <div className="plugin-empty">创建通用 Python 脚本任务；新任务默认停用。</div>}
      </section>
    </div>
  </section>;
}
