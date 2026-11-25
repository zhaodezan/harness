import { useEffect, useState } from 'react';
import { api } from '../api';

type KnowledgeBase = { id: string; name: string; document_count: number; ready_count: number; failed_count: number; status: string; updated_at: string };
type KnowledgeDocument = { id: string; filename: string; extension: string; char_count: number; status: string; error: string | null; created_at: string };
type SearchResult = { knowledge_base_name: string; filename: string; score: number; matched_terms: string[]; snippet: string };

export function KnowledgeBasePage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');

  const loadKnowledgeBases = async () => {
    const items = await api<KnowledgeBase[]>('/knowledge-bases');
    setKnowledgeBases(items);
    if (!selectedId && items.length) setSelectedId(items[0].id);
    if (selectedId && !items.some((item) => item.id === selectedId)) setSelectedId(items[0]?.id ?? '');
  };
  const loadDocuments = async (id: string) => setDocuments(id ? await api<KnowledgeDocument[]>(`/knowledge-bases/${id}/documents`) : []);
  useEffect(() => { loadKnowledgeBases().catch((reason: Error) => setError(reason.message)); }, []);
  useEffect(() => { loadDocuments(selectedId).catch((reason: Error) => setError(reason.message)); }, [selectedId]);

  const createKnowledgeBase = async () => {
    if (!name.trim()) return;
    try { await api('/knowledge-bases', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }); setName(''); setStatus('知识库已创建'); await loadKnowledgeBases(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const renameKnowledgeBase = async () => {
    const current = knowledgeBases.find((item) => item.id === selectedId);
    if (!current) return;
    const next = window.prompt('输入新的知识库名称', current.name);
    if (!next?.trim()) return;
    try { await api(`/knowledge-bases/${selectedId}`, { method: 'PATCH', body: JSON.stringify({ name: next.trim() }) }); await loadKnowledgeBases(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const deleteKnowledgeBase = async () => {
    if (!selectedId || !window.confirm('删除此知识库及其文档？')) return;
    try { await api(`/knowledge-bases/${selectedId}`, { method: 'DELETE' }); setSelectedId(''); await loadKnowledgeBases(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const uploadFiles = async (files: FileList | null) => {
    if (!selectedId || !files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    try { await api(`/knowledge-bases/${selectedId}/documents`, { method: 'POST', body: form }); setStatus('文件已导入，正在显示解析状态'); await loadDocuments(selectedId); await loadKnowledgeBases(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const deleteDocument = async (id: string) => {
    try { await api(`/knowledge-bases/${selectedId}/documents/${id}`, { method: 'DELETE' }); await loadDocuments(selectedId); await loadKnowledgeBases(); }
    catch (reason) { setError((reason as Error).message); }
  };
  const search = async () => {
    if (!selectedId || !query.trim()) return;
    try { setResults(await api<SearchResult[]>(`/knowledge-bases/${selectedId}/search`, { method: 'POST', body: JSON.stringify({ query, limit: 5 }) })); }
    catch (reason) { setError((reason as Error).message); }
  };

  const selected = knowledgeBases.find((item) => item.id === selectedId);
  return <section className="knowledge-base-page">
    <div className="knowledge-page-heading"><div><div className="eyebrow">LOCAL DOCUMENTS</div><h1>知识库</h1><p>导入本地资料，供授权 Agent 自动检索。</p></div></div>
    {error && <div className="plugin-message error" role="status">{error}<button type="button" onClick={() => setError('')}>×</button></div>}
    {status && <div className="plugin-message" role="status">{status}<button type="button" onClick={() => setStatus('')}>×</button></div>}
    <div className="knowledge-layout">
      <section className="plugin-list knowledge-base-list"><div className="plugin-list-head"><h2>知识库</h2><button className="primary-button" type="button" onClick={createKnowledgeBase}>创建</button></div><div className="knowledge-create"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="新知识库名称" onKeyDown={(event) => { if (event.key === 'Enter') void createKnowledgeBase(); }}/></div>{knowledgeBases.map((item) => <button type="button" className={`knowledge-base-item ${item.id === selectedId ? 'active' : ''}`} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.name}</strong><small>{item.document_count} 个文档 · {item.ready_count} 个可检索</small></button>)}{!knowledgeBases.length && <div className="plugin-empty">还没有知识库</div>}</section>
      <section className="plugin-list knowledge-detail"><div className="plugin-list-head"><div><h2>{selected?.name ?? '选择一个知识库'}</h2>{selected && <small className="plugin-helper">{selected.document_count} 个文档 · {selected.status === 'has_errors' ? '包含解析失败文件' : '状态正常'}</small>}</div>{selected && <div className="knowledge-actions"><button type="button" onClick={renameKnowledgeBase}>重命名</button><button type="button" onClick={deleteKnowledgeBase}>删除</button></div>}</div>{selected && <><label className="knowledge-upload">导入文件（支持 .md、.markdown、.txt、.json、.csv、.pdf、.docx）<input type="file" multiple accept=".md,.markdown,.txt,.json,.csv,.pdf,.docx" onChange={(event) => { void uploadFiles(event.target.files); event.target.value = ''; }}/></label><div className="knowledge-documents">{documents.map((document) => <article className="knowledge-document" key={document.id}><div><strong>{document.filename}</strong><small>{document.extension} · {document.char_count.toLocaleString()} 字符 · {document.status === 'ready' ? '可检索' : '解析失败'}</small>{document.error && <small className="knowledge-error">{document.error}</small>}</div><button type="button" onClick={() => void deleteDocument(document.id)}>删除</button></article>)}{!documents.length && <div className="plugin-empty">导入文档后会显示在这里</div>}</div><div className="knowledge-search"><h3>调试搜索</h3><div><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入关键词验证检索效果" onKeyDown={(event) => { if (event.key === 'Enter') void search(); }}/><button type="button" onClick={() => void search()}>搜索</button></div>{results.map((result, index) => <article className="knowledge-result" key={`${result.filename}-${index}`}><strong>{result.knowledge_base_name} / {result.filename}</strong><small>BM25 {result.score.toFixed(3)} · 命中：{result.matched_terms.join('、')}</small><p>{result.snippet}</p></article>)}{query && !results.length && <small className="plugin-helper">没有找到相关内容</small>}</div></>}</section>
    </div>
  </section>;
}
