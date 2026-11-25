import { useEffect, useRef, useState } from 'react';
import { submitChatOnEnter } from '../chatKeyboard';
import { createTypewriter } from '../typewriter';
import { RunTracePanel } from '../components/RunTracePanel';
import { isTerminalTrace } from '../runTrace';
import type { RunTrace } from '../runTrace';

type DebugConfig = {
  model_config_id: string;
  mode: 'react' | 'plan_execute';
  context_window: number;
  system_prompt: string;
  prompt_variables: Record<string, string>;
  selected_skills: string[];
  selected_mcp_tools: string[];
  selected_local_tools: string[];
  selected_knowledge_bases: string[];
  selected_database_connections: string[];
  selected_agents: string[];
  sandbox_code_enabled: boolean;
};
type DebugMessage = { role: 'user' | 'assistant'; content: string; trace?: RunTrace[]; plan?: string };
type Props = { agentId: string; config: DebugConfig };

export function AgentDebugChat({ agentId, config }: Props) {
  const [messages, setMessages] = useState<DebugMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('调试对话只保存在当前页面；配置改动会自动保存。');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), [agentId]);

  const send = async () => {
    const text = draft.trim();
    if (!text || active.current || !agentId) return;
    const controller = new AbortController();
    active.current = controller;
    const { signal } = controller;
    const history = [...messages, { role: 'user' as const, content: text }];
    const updateReply = (update: (reply: DebugMessage) => DebugMessage) => setMessages((items) =>
      items.map((item, index) => index === history.length ? update(item) : item));
    const writer = createTypewriter((part) => updateReply((item) => ({ ...item, content: item.content + part })));
    const stopTyping = () => signal.reason === 'debug-stop' ? writer.stop() : writer.dispose();
    signal.addEventListener('abort', stopTyping, { once: true });
    setMessages([...history, { role: 'assistant', content: '', trace: [] }]);
    setDraft(''); setBusy(true); setStatus('正在调试…');
    let terminalReceived = false;
    const appendTrace = (event: RunTrace) => updateReply((item) => ({ ...item, trace: [...(item.trace ?? []), event].slice(-200) }));
    try {
      const response = await fetch(`/api/agents/${agentId}/debug`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config, messages: history.map(({ role, content }) => ({ role, content })) }), signal,
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? '调试请求失败');
      }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done || signal.aborted) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n'); buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const type = frame.match(/^event: (.+)$/m)?.[1];
          const data = frame.match(/^data: (.+)$/m)?.[1];
          if (!data) continue;
          const payload = JSON.parse(data) as { text?: string; plan?: string; summary?: string; status?: string; event_type?: string; message?: string; approval_id?: string; tool_name?: string; arguments?: Record<string, unknown>; description?: string };
          if (type === 'delta') {
            writer.push(payload.text ?? '');
          } else if (type === 'trace') {
            const item = { event_type: payload.event_type ?? 'debug_status', status: payload.status ?? 'running', summary: payload.summary ?? '调试运行中' };
            appendTrace(item);
            terminalReceived ||= isTerminalTrace(item);
          } else if (type === 'plan') {
            updateReply((item) => ({ ...item, plan: payload.plan ?? '' }));
          } else if (type === 'done') {
            terminalReceived = true;
          } else if (type === 'approval_required' && payload.approval_id) {
            const approved = window.confirm(`${payload.description ?? '工具需要确认'}\n\n${payload.tool_name}\n${JSON.stringify(payload.arguments ?? {}, null, 2)}\n\n是否批准？`);
            const decision = await fetch(`/api/approvals/${payload.approval_id}`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved }), signal,
            });
            if (!decision.ok) throw new Error('工具审批已失效，请重新发送调试消息。');
          } else if (type === 'error') {
            throw new Error(payload.message ?? '调试运行失败');
          }
        }
      }
      if (!signal.aborted && !terminalReceived) throw new Error('调试连接提前结束，请重试。');
      await writer.finish();
      if (!signal.aborted) setStatus('调试完成');
    } catch (reason) {
      if (signal.aborted) return;
      writer.stop();
      const message = (reason as Error).message;
      setMessages((items) => items.map((item, index) => index === items.length - 1 ? { ...item, content: item.content || `调试错误：${message}` } : item));
      setStatus(message);
      if (!terminalReceived) appendTrace({ event_type: 'run_failed', status: 'failed', summary: '未能完成本次调试' });
    } finally {
      writer.dispose();
      signal.removeEventListener('abort', stopTyping);
      active.current = null;
      if (signal.reason === 'debug-stop') {
        setStatus('已停止调试');
        if (!terminalReceived) appendTrace({ event_type: 'run_cancelled', status: 'cancelled', summary: '已停止接收调试回复' });
      }
      if (!signal.aborted || signal.reason === 'debug-stop') setBusy(false);
    }
  };

  return <section className="agent-debug-column">
    <header className="agent-debug-header"><h2>调试聊天</h2><p>使用当前编辑配置 · 发送时读取最新内容</p></header>
    <div className="agent-debug-messages" aria-live="polite">
      {!messages.length && <div className="agent-debug-empty">在此测试当前智能体配置。<br/>调试消息不会保存，离开页面后清空。</div>}
      {messages.map((message, index) => <article className={`agent-debug-message ${message.role}`} key={`${index}-${message.role}`}>
        {message.role === 'assistant' && <RunTracePanel events={message.trace ?? []}>
          {message.plan && <div className="debug-plan-card"><strong>执行计划</strong><pre>{message.plan}</pre></div>}
        </RunTracePanel>}
        <div className="debug-message-content">{message.content || (busy && index === messages.length - 1 ? '正在生成…' : '')}</div>
      </article>)}
    </div>
    <div className="agent-debug-status" role="status">{status}</div>
    <form className="agent-debug-form" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => submitChatOnEnter(event, () => void send())} placeholder="输入调试消息…" rows={3}/>
      {busy ? <button type="button" onClick={() => active.current?.abort('debug-stop')}>停止调试</button>
        : <button type="submit" disabled={!draft.trim() || !config.model_config_id}>发送调试消息</button>}
    </form>
  </section>;
}
