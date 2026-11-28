import { useEffect, useMemo, useRef, useState } from 'react';
import { init, use } from 'echarts/core';
import type { ECharts, EChartsCoreOption } from 'echarts/core';
import {
  BarChart, BoxplotChart, CandlestickChart, EffectScatterChart, FunnelChart, GaugeChart,
  GraphChart, HeatmapChart, LineChart, ParallelChart, PieChart, PictorialBarChart,
  RadarChart, SankeyChart, ScatterChart, SunburstChart, ThemeRiverChart, TreemapChart,
} from 'echarts/charts';
import {
  DataZoomComponent, DatasetComponent, GridComponent, LegendComponent, PolarComponent,
  RadarComponent, TitleComponent, TooltipComponent, VisualMapComponent,
} from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import { parseChartBlocks, safeChartOption, type ChartBlock } from './chartBlocks';
import { parseChatLinks, type ChatLinkSegment } from './chatLinks';

use([
  BarChart, BoxplotChart, CandlestickChart, EffectScatterChart, FunnelChart, GaugeChart,
  GraphChart, HeatmapChart, LineChart, ParallelChart, PieChart, PictorialBarChart,
  RadarChart, SankeyChart, ScatterChart, SunburstChart, ThemeRiverChart, TreemapChart,
  DataZoomComponent, DatasetComponent, GridComponent, LegendComponent, PolarComponent,
  RadarComponent, TitleComponent, TooltipComponent, VisualMapComponent, SVGRenderer,
]);

function chartTitle(option: Record<string, unknown>): string {
  const title = Array.isArray(option.title) ? option.title[0] : option.title;
  if (!title || typeof title !== 'object') return '图表';
  const text = (title as Record<string, unknown>).text;
  return typeof text === 'string' && text.trim() ? text : '图表';
}

function ChartView({ block }: { block: Extract<ChartBlock, { type: 'chart' }> }) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<ECharts | null>(null);
  const [renderError, setRenderError] = useState(false);
  const title = block.option ? chartTitle(block.option) : '图表';

  useEffect(() => {
    const element = container.current;
    if (!element || !block.option) return;
    const chart = init(element, undefined, { renderer: 'svg' });
    instance.current = chart;
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => chart.resize());
    const resize = () => chart.resize();
    observer?.observe(element);
    if (!observer) window.addEventListener('resize', resize);
    return () => {
      observer?.disconnect();
      if (!observer) window.removeEventListener('resize', resize);
      chart.dispose();
      instance.current = null;
    };
  }, []);

  useEffect(() => {
    if (!block.option || !instance.current) return;
    try {
      setRenderError(false);
      instance.current.setOption(safeChartOption(block.option) as EChartsCoreOption, { notMerge: true });
    } catch {
      setRenderError(true);
    }
  }, [block.option]);

  if (block.error || !block.option) {
    return <div className="chat-chart-error" role="alert">
      <p>{block.error || '无法渲染此图表配置。'}</p>
      <details><summary>查看图表配置</summary><pre>{block.source}</pre></details>
    </div>;
  }

  return <figure className="chat-chart" aria-label={title}>
    <div ref={container} className="chat-chart-canvas" role="img" aria-label={title}/>
    {renderError && <div className="chat-chart-error" role="alert">
      <p>无法渲染此图表配置。</p>
      <details><summary>查看图表配置</summary><pre>{block.source}</pre></details>
    </div>}
    <figcaption>{title}</figcaption>
  </figure>;
}

type MessageContentProps = {
  content: string;
  onWorkspaceFile?: (path: string) => void;
  onWebLink?: (href: string) => void;
};

function TextBlock({
  content,
  onWorkspaceFile,
  onWebLink,
}: Omit<MessageContentProps, 'content'> & { content: string }) {
  const segments = parseChatLinks(content);
  return <span className="message-text">{segments.map((segment: ChatLinkSegment, index) => {
    if (segment.type === 'text') return <span key={`text-${index}`}>{segment.value}</span>;
    if (segment.type === 'workspace-file') {
      return onWorkspaceFile
        ? <button className="chat-workspace-link" type="button" key={`file-${index}`} onClick={() => onWorkspaceFile(segment.path)}>{segment.label}</button>
        : <span key={`file-${index}`}>{segment.label}</span>;
    }
    const handleClick = onWebLink ? (event: React.MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
      onWebLink(segment.href);
    } : undefined;
    return <a key={`web-${index}`} href={segment.href} target="_blank" rel="noopener noreferrer" onClick={handleClick}>{segment.label}</a>;
  })}</span>;
}

export function MessageContent({ content, onWorkspaceFile, onWebLink }: MessageContentProps) {
  const blocks = useMemo(() => parseChartBlocks(content), [content]);
  return <>
    {blocks.map((block, index) => block.type === 'text'
      ? block.content ? <TextBlock content={block.content} onWorkspaceFile={onWorkspaceFile} onWebLink={onWebLink} key={`text-${index}`}/> : null
      : <ChartView block={block} key={`chart-${index}-${block.source}`}/>
    )}
  </>;
}
