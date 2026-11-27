import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import './chatComposerOptions.css';

const kinds = ['local_tools', 'mcp_tools', 'skills', 'agents'] as const;
type Kind = typeof kinds[number];
type Option = { id: string; name: string; group?: string; description?: string };
export type CapabilitySelection = Record<Kind, Option[]>;
export type ChatOptions = { options: CapabilitySelection; defaults: Record<Kind, string[]> };
export type ModelOption = { id: string; name: string; model: string; credential_configured: boolean; last_tested_at: string | null };
export type ApprovalMode = 'allow_all' | 'user_approval';
export const DEFAULT_APPROVAL_MODE: ApprovalMode = 'allow_all';
export type ChatPreference = {
  capabilities?: CapabilitySelection;
  model?: Pick<ModelOption, 'id' | 'name'>;
  approval_mode?: ApprovalMode;
};

export function normalizeApprovalMode(value: unknown): ApprovalMode {
  return value === 'user_approval' ? 'user_approval' : DEFAULT_APPROVAL_MODE;
}

const categories = {
  local_tools: { name: '工具', icon: '⚒', limit: 100 },
  mcp_tools: { name: 'MCP', icon: '⛓', limit: 100 },
  skills: { name: 'Skills', icon: '✦', limit: 50 },
  agents: { name: 'Agent', icon: '◎', limit: 20 },
};

export function defaultCapabilities(catalog: ChatOptions): CapabilitySelection {
  return Object.fromEntries(kinds.map((kind) => [kind, catalog.options[kind].filter(
    (item) => catalog.defaults[kind].includes(item.id),
  )])) as CapabilitySelection;
}

export function chatRequestOptions(
  selection: CapabilitySelection,
  model?: ChatPreference['model'],
  approvalMode: ApprovalMode = DEFAULT_APPROVAL_MODE,
) {
  return {
    capabilities: Object.fromEntries(kinds.map((kind) => [kind, selection[kind].map((item) => item.id)])),
    approval_mode: approvalMode,
    ...(model ? { model_config_id: model.id } : {}),
  };
}

function usePicker(onOpen: () => void) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = () => { setOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);
  return { open, root, trigger, panel, id, close, toggle: () => {
    if (!open) onOpen();
    setOpen(!open);
  } };
}

type PickerStatus = { loading: boolean; error: string; refresh: () => void; disabled: boolean };

export function ChatCapabilitiesPicker({ catalog, selection, onChange, onReset, canReset, loading, error, refresh, disabled }:
  PickerStatus & { catalog?: ChatOptions; selection?: CapabilitySelection; canReset: boolean;
    onChange: (selection: CapabilitySelection) => void; onReset: () => void }) {
  const picker = usePicker(refresh);
  const toggle = (kind: Kind, item: Option) => {
    if (!selection || disabled) return;
    const selected = selection[kind];
    const exists = selected.some((option) => option.id === item.id);
    if (!exists && selected.length >= categories[kind].limit) return;
    onChange({ ...selection, [kind]: exists ? selected.filter((option) => option.id !== item.id) : [...selected, item] });
  };
  const isUnavailable = (kind: Kind, id: string) => !!catalog && !catalog.options[kind].some((option) => option.id === id);
  return <div className="chat-capability-controls">
    <div className="chat-picker" ref={picker.root} onKeyDown={(event) => { if (event.key === 'Escape' && picker.open) picker.close(); }}>
      <button className="chat-add-button" type="button" ref={picker.trigger} aria-label="选择对话工具"
        aria-expanded={picker.open} aria-controls={picker.open ? picker.id : undefined} disabled={disabled} onClick={picker.toggle}>＋</button>
      {picker.open && <div className="chat-picker-panel" ref={picker.panel} id={picker.id} role="region" aria-label="选择对话工具" tabIndex={-1}>
        <header><strong>对话工具</strong><button type="button" onClick={picker.close} disabled={disabled}>完成</button></header>
        <p>可多选。模型会按需使用，选择不会修改 Agent 配置。</p>
        {loading && <p role="status">正在加载可选项…</p>}
        {error && <p role="alert">{error} <button type="button" onClick={refresh}>重试</button></p>}
        {catalog && kinds.map((kind) => <fieldset key={kind} disabled={disabled}>
          <legend><span aria-hidden="true">{categories[kind].icon}</span> {categories[kind].name}</legend>
          {!catalog.options[kind].length && <p>{kind === 'mcp_tools' ? '暂无已连接的 MCP 工具' : '暂无可选项'}</p>}
          {catalog.options[kind].map((item) => {
            const checked = selection?.[kind].some((option) => option.id === item.id) ?? false;
            return <label key={item.id} title={item.description}>
              <input type="checkbox" checked={checked} onChange={() => toggle(kind, item)}
                disabled={!checked && (selection?.[kind].length ?? 0) >= categories[kind].limit}/>
              <span>{item.group ? `${item.group} / ${item.name}` : item.name}</span>
            </label>;
          })}
        </fieldset>)}
        {canReset && <button type="button" className="chat-picker-reset" onClick={onReset} disabled={disabled}>恢复 Agent 默认工具</button>}
      </div>}
    </div>
    {selection && kinds.some((kind) => selection[kind].length > 0) && <div className="chat-capability-chips" aria-label="已选对话工具">
      {kinds.flatMap((kind) => selection[kind].map((item) => {
        const unavailable = isUnavailable(kind, item.id);
        return <span className={`chat-capability-chip${unavailable ? ' unavailable' : ''}`} key={`${kind}:${item.id}`}
          title={`${categories[kind].name} · ${item.group ? `${item.group} / ` : ''}${item.name}${unavailable ? '（已不可用）' : ''}`}>
          <span aria-hidden="true">{categories[kind].icon}</span><span className="chat-chip-name">{item.name}{unavailable ? '（已不可用）' : ''}</span>
          <button type="button" aria-label={`移除 ${item.name}`} disabled={disabled} onClick={() => toggle(kind, item)}>×</button>
        </span>;
      }))}
    </div>}
  </div>;
}

export function ApprovalModePicker({ value, onChange, disabled }: {
  value: ApprovalMode;
  onChange: (value: ApprovalMode) => void;
  disabled: boolean;
}) {
  return <label className="approval-mode-picker">
    <span>执行权限</span>
    <select aria-label="工具执行权限" value={value} disabled={disabled}
      onChange={(event) => onChange(normalizeApprovalMode(event.target.value))}>
      <option value="allow_all">全部允许</option>
      <option value="user_approval">用户审批</option>
    </select>
  </label>;
}

export function ChatModelPicker({ models, model, defaultName, onChange, loading, error, refresh, disabled }:
  PickerStatus & { models: ModelOption[]; model?: ChatPreference['model']; defaultName?: string;
    onChange: (model?: ChatPreference['model']) => void }) {
  const picker = usePicker(refresh);
  const label = models.find((item) => item.id === model?.id)?.name || model?.name || defaultName || '选择模型';
  const readyModels = models.filter((item) => item.credential_configured && item.last_tested_at);
  return <div className="chat-picker chat-model-picker" ref={picker.root}
    onKeyDown={(event) => { if (event.key === 'Escape' && picker.open) picker.close(); }}>
    <button type="button" className="chat-model-button" ref={picker.trigger} disabled={disabled} title={label}
      aria-label={`选择模型：${label}`} aria-expanded={picker.open} aria-controls={picker.open ? picker.id : undefined}
      onClick={picker.toggle}><span>{label}</span><span aria-hidden="true">▾</span></button>
    {picker.open && <div className="chat-picker-panel" ref={picker.panel} id={picker.id} role="region" aria-label="对话模型" tabIndex={-1}>
      <header><strong>对话模型</strong></header>
      {defaultName && <button className="chat-model-option" type="button" aria-pressed={!model} disabled={disabled}
        onClick={() => { onChange(); picker.close(); }}>恢复 Agent 默认模型 · {defaultName}</button>}
      {loading && <p role="status">正在加载模型…</p>}
      {error && <p role="alert">{error} <button type="button" onClick={refresh}>重试</button></p>}
      {!loading && !error && !readyModels.length && <p>暂无已配置凭据并通过连接测试的模型。<Link to="/plugins?tab=Model">前往模型库</Link></p>}
      {model && !loading && !error && !readyModels.some((item) => item.id === model.id) && <p role="status">当前选中的模型已不可用，请重新选择。</p>}
      {readyModels.map((item) => <button type="button" className="chat-model-option" aria-pressed={model?.id === item.id}
        key={item.id} disabled={disabled} onClick={() => { onChange({ id: item.id, name: item.name }); picker.close(); }}>
        <strong>{item.name}</strong><small>{item.model}</small>
      </button>)}
    </div>}
  </div>;
}
