export type ChartBlock =
  | { type: 'text'; content: string }
  | { type: 'chart'; source: string; option?: Record<string, unknown>; error?: string };

const MAX_CHART_BYTES = 256 * 1024;
const MAX_CHARTS_PER_MESSAGE = 20;
const MAX_TOTAL_CHART_BYTES = 1024 * 1024;
const MAX_NODES = 50_000;
const MAX_DEPTH = 40;
const ALLOWED_OPTION_KEYS = new Set([
  'title', 'tooltip', 'legend', 'grid', 'xAxis', 'yAxis', 'series', 'color', 'dataset',
  'dataZoom', 'visualMap', 'radar', 'polar', 'angleAxis', 'radiusAxis', 'textStyle',
  'animation', 'animationDuration', 'animationEasing', 'backgroundColor',
]);
const ALLOWED_SERIES_TYPES = new Set([
  'bar', 'line', 'pie', 'scatter', 'effectScatter', 'radar', 'funnel', 'gauge',
  'heatmap', 'treemap', 'sunburst', 'boxplot', 'candlestick', 'sankey', 'graph',
  'pictorialBar', 'themeRiver', 'parallel',
]);

function validateJsonTree(value: unknown, depth = 0, budget = { nodes: 0 }): boolean {
  if (++budget.nodes > MAX_NODES || depth > MAX_DEPTH) return false;
  if (Array.isArray(value)) return value.every((item) => validateJsonTree(item, depth + 1, budget));
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).every(([key, child]) =>
      !['__proto__', 'prototype', 'constructor'].includes(key)
      && validateJsonTree(child, depth + 1, budget));
  }
  return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

function parseOption(source: string): { option?: Record<string, unknown>; error?: string } {
  if (new TextEncoder().encode(source).byteLength > MAX_CHART_BYTES) {
    return { error: '图表配置不能大于 256 KB。' };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return { error: '图表配置不是有效的 JSON。' };
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    return { error: '图表配置必须是 JSON 对象。' };
  }
  if (!validateJsonTree(parsed)) {
    return { error: '图表配置结构过于复杂或包含不支持的字段。' };
  }

  const option = parsed as Record<string, unknown>;
  if (Object.keys(option).some((key) => !ALLOWED_OPTION_KEYS.has(key))) {
    return { error: '图表配置包含暂不支持的选项。' };
  }
  if (!Array.isArray(option.series) || option.series.length === 0 || option.series.length > 50) {
    return { error: '图表配置需要包含 1 到 50 个数据系列。' };
  }
  if (option.series.some((series) => !series || typeof series !== 'object' || Array.isArray(series)
      || typeof (series as Record<string, unknown>).type !== 'string'
      || !ALLOWED_SERIES_TYPES.has((series as Record<string, unknown>).type as string))) {
    return { error: '图表包含不支持的系列类型。' };
  }

  return { option };
}

export function parseChartBlocks(content: string): ChartBlock[] {
  const blocks: ChartBlock[] = [];
  const fence = /```echarts[ \t]*\r?\n([\s\S]*?)\r?\n```/g;
  let cursor = 0;
  let chartCount = 0;
  let chartBytes = 0;
  let match: RegExpExecArray | null;
  while ((match = fence.exec(content)) !== null) {
    if (chartCount >= MAX_CHARTS_PER_MESSAGE) break;
    const source = match[1].trim();
    if (source.length > MAX_TOTAL_CHART_BYTES) break;
    const size = new TextEncoder().encode(source).byteLength;
    if (chartBytes + size > MAX_TOTAL_CHART_BYTES) break;
    if (match.index > cursor) blocks.push({ type: 'text', content: content.slice(cursor, match.index) });
    blocks.push({ type: 'chart', source, ...parseOption(source) });
    chartCount += 1;
    chartBytes += size;
    cursor = fence.lastIndex;
  }
  if (cursor < content.length) blocks.push({ type: 'text', content: content.slice(cursor) });
  return blocks.length ? blocks : [{ type: 'text', content }];
}

/** Force non-HTML tooltip rendering; JSON configuration never supplies executable callbacks. */
export function safeChartOption(option: Record<string, unknown>): Record<string, unknown> {
  const tooltip = option.tooltip;
  const safeTooltip = Array.isArray(tooltip)
    ? tooltip.map((item) => ({ ...(item as Record<string, unknown>), renderMode: 'richText', formatter: undefined }))
    : tooltip && typeof tooltip === 'object'
      ? { ...(tooltip as Record<string, unknown>), renderMode: 'richText', formatter: undefined }
      : { renderMode: 'richText' };
  const title = option.title;
  const safeTitleItem = (item: unknown) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return item;
    const { link: _link, sublink: _sublink, ...safe } = item as Record<string, unknown>;
    return safe;
  };
  const safeTitle = Array.isArray(title) ? title.map(safeTitleItem) : safeTitleItem(title);
  return { ...option, title: safeTitle, tooltip: safeTooltip };
}
