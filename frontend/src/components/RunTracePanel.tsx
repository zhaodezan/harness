import type { ReactNode } from 'react';
import { traceSummary } from '../runTrace';
import type { RunTrace } from '../runTrace';
import './RunTracePanel.css';

const statusLabels: Record<string, string> = {
  running: '进行中', working: '进行中', completed: '已完成', failed: '失败',
  waiting: '等待确认', approved: '已批准', rejected: '已拒绝', cancelled: '已停止',
  canceled: '已停止', stopped: '已停止', interrupted: '已中断', warning: '有警告', empty: '无结果',
};

type Props = {
  events: RunTrace[];
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  children?: ReactNode;
};

export function RunTracePanel({ events, loading, error, onRetry, children }: Props) {
  const latest = events.at(-1);
  return <details className="trace-panel" aria-label="运行轨迹">
    <summary className="trace-toggle">
      <span className="trace-pulse" aria-hidden="true"/>运行轨迹
      <span className="trace-count">{events.length} 个事件{latest && ` · ${statusLabels[latest.status] ?? latest.status}`}</span>
      <span className="trace-chevron" aria-hidden="true"/>
    </summary>
    <div className="trace-body">
      {loading && <p className="trace-placeholder" role="status">正在加载历史轨迹…</p>}
      {error && <div className="trace-placeholder" role="status">无法加载历史轨迹。 <button type="button" onClick={onRetry}>重试</button></div>}
      {!loading && !error && !events.length && !children && <p className="trace-placeholder">暂无运行事件。</p>}
      {children}
      {events.length > 0 && <ol className="trace-list">{events.map((item, index) => <li key={index}>
        <i className={['failed', 'rejected', 'warning'].includes(item.status) ? 'failed' : ''} aria-hidden="true"/>
        <span>{traceSummary(item)}</span><small>{statusLabels[item.status] ?? item.status}</small>
      </li>)}</ol>}
    </div>
  </details>;
}
