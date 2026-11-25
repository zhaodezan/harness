import { Link } from 'react-router-dom';

export type AgentListItem = {
  id: string;
  name: string;
  model: string;
  mode: string;
};

type AgentListPageProps = {
  agents: AgentListItem[];
  onConfigure: (agentId: string) => void;
  onCreate: () => void;
  onDelete: (agent: AgentListItem) => void;
};

export function AgentListPage({
  agents,
  onConfigure,
  onCreate,
  onDelete,
}: AgentListPageProps) {
  return (
    <section className="agent-list-page">
      <header className="agent-page-heading">
        <div>
          <div className="eyebrow">工作区设置</div>
          <h1>智能体</h1>
          <p>管理配置，或选择一个智能体开始对话。</p>
        </div>
        <button className="primary-button agent-create-button" type="button" onClick={onCreate}>
          ＋ 新建智能体
        </button>
      </header>
      {agents.length ? (
        <div className="agent-cards">
          {agents.map((agent) => (
            <article className="agent-card" key={agent.id}>
              <div className="agent-card-mark" aria-hidden="true">{Array.from(agent.name)[0] || 'A'}</div>
              <div className="agent-card-info">
                <h2>{agent.name}</h2>
                <p>{agent.model || '尚未配置模型'} · {agent.mode === 'plan_execute' ? '计划执行' : '灵感对话'}</p>
              </div>
              <div className="agent-card-actions">
                <button type="button" className="secondary-button" onClick={() => onConfigure(agent.id)}>配置</button>
                <Link className="primary-button" to={`/agents/${encodeURIComponent(agent.id)}/chat`}
                  target="_blank" rel="noopener noreferrer" title="在新标签页打开 Agent 专属对话">发布</Link>
                <button type="button" className="danger-button" onClick={() => onDelete(agent)}>删除</button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="agent-list-empty">
          <span aria-hidden="true">✳</span>
          <h2>还没有智能体</h2>
          <p>创建并配置一个智能体后，就可以开始对话。</p>
          <button className="primary-button" type="button" onClick={onCreate}>创建第一个智能体</button>
        </div>
      )}
    </section>
  );
}
