import { describe, expect, it } from 'vitest';
import { parseChartBlocks, safeChartOption } from './chartBlocks';

describe('parseChartBlocks', () => {
  it('extracts a JSON chart block while preserving surrounding text', () => {
    const blocks = parseChartBlocks('开头\n\n```echarts\n{"xAxis":{"data":["A"]},"series":[{"type":"bar","data":[1]}]}\n```\n结尾');

    expect(blocks).toEqual([
      { type: 'text', content: '开头\n\n' },
      {
        type: 'chart',
        source: '{"xAxis":{"data":["A"]},"series":[{"type":"bar","data":[1]}]}',
        option: { xAxis: { data: ['A'] }, series: [{ type: 'bar', data: [1] }] },
      },
      { type: 'text', content: '\n结尾' },
    ]);
  });

  it('returns an error block instead of executing or dropping invalid JSON', () => {
    const blocks = parseChartBlocks('```echarts\n{"series": [not valid]}\n```');

    expect(blocks).toEqual([{
      type: 'chart',
      source: '{"series": [not valid]}',
      error: '图表配置不是有效的 JSON。',
    }]);
  });

  it('leaves non-echarts fences as ordinary text', () => {
    const source = '```json\n{"hello":"world"}\n```';

    expect(parseChartBlocks(source)).toEqual([{ type: 'text', content: source }]);
  });

  it('rejects non-object options and oversized chart payloads', () => {
    expect(parseChartBlocks('```echarts\n[]\n```')).toEqual([{
      type: 'chart', source: '[]', error: '图表配置必须是 JSON 对象。',
    }]);
    expect(parseChartBlocks('```echarts\n{"series":[{"type":"unknown"}]}\n```')[0]).toMatchObject({
      type: 'chart', error: '图表包含不支持的系列类型。',
    });
    const oversizedSource = `{"title":"${'x'.repeat(256 * 1024)}"}`;
    expect(parseChartBlocks('```echarts\n' + oversizedSource + '\n```')).toEqual([{
      type: 'chart', source: oversizedSource,
      error: '图表配置不能大于 256 KB。',
    }]);
  });

  it('forces safe rich-text tooltip rendering', () => {
    expect(safeChartOption({ tooltip: { trigger: 'axis', formatter: 'unsafe' } })).toEqual({
      tooltip: { trigger: 'axis', formatter: undefined, renderMode: 'richText' },
    });
  });

  it('removes clickable links before passing options to ECharts', () => {
    const option = safeChartOption({
      title: { text: 'safe', link: 'javascript:alert(1)', sublink: 'https://example.test' },
      series: [{ type: 'bar', data: [1] }],
    });
    expect(option).toEqual({ title: { text: 'safe' }, series: [{ type: 'bar', data: [1] }], tooltip: { renderMode: 'richText' } });
  });

  it('stops creating chart instances after aggregate count and byte limits', () => {
    const manyCharts = Array.from({ length: 21 }, (_, index) => 'before-' + index + '\n\n```echarts\n{"series":[{"type":"bar","data":[' + index + ']}]}\n```\n').join('');
    const blocks = parseChartBlocks(manyCharts);
    expect(blocks.filter((block) => block.type === 'chart')).toHaveLength(20);
    expect(blocks.at(-1)).toMatchObject({ type: 'text' });

    const largeOption = (text: string) => JSON.stringify({ title: { text }, series: [{ type: 'bar', data: [1] }] });
    const largeCharts = Array.from({ length: 5 }, (_, index) => '```echarts\n' + largeOption('x'.repeat(220 * 1024) + index) + '\n```').join('\n');
    const limited = parseChartBlocks(largeCharts);
    expect(limited.filter((block) => block.type === 'chart')).toHaveLength(4);
    expect(limited.at(-1)).toMatchObject({ type: 'text' });
  });
});
