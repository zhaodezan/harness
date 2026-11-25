export type RunTrace = { event_type: string; status: string; summary: string; run_id?: string; occurred_at?: string };
export type SessionTrace = { sessionId: string; events: RunTrace[] };

const terminal = new Set(['run_completed', 'run_cancelled', 'model_error', 'run_failed', 'run_interrupted']);

export function isTerminalTrace(event: RunTrace): boolean {
  return terminal.has(event.event_type);
}

export function mergeRunTraces(sessionId: string, saved: RunTrace[], live: SessionTrace | null): RunTrace[] {
  if (!live || sessionId !== live.sessionId || !live.events.length) return saved;
  const savedTerminal = new Set(saved.filter((item) => terminal.has(item.event_type)).map((item) => item.run_id));
  const pending = live.events.filter((item) => !savedTerminal.has(item.run_id));
  const pendingIds = new Set(pending.map((item) => item.run_id));
  return [...saved.filter((item) => !pendingIds.has(item.run_id)), ...pending].slice(-200);
}

export function groupRunTraces(events: RunTrace[]): Map<string, RunTrace[]> {
  const groups = new Map<string, RunTrace[]>();
  for (const event of events) {
    if (!event.run_id) continue;
    const group = groups.get(event.run_id);
    if (group) group.push(event);
    else groups.set(event.run_id, [event]);
  }
  return groups;
}

export function traceSummary(item: RunTrace): string {
  if (item.event_type === 'model_error') return '模型调用或运行失败';
  if (item.event_type === 'plan_step_started') {
    const step = item.summary.match(/第 \d+\/\d+ 步/)?.[0];
    return step ? `${step}开始执行` : '开始执行计划步骤';
  }
  return item.summary || item.event_type;
}
