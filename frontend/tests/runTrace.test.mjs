import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupRunTraces, mergeRunTraces, traceSummary } from '../src/runTrace.ts';

const event = (run_id, event_type, status = 'running') => ({ run_id, event_type, status, summary: event_type });

test('saved terminal runs replace live copies without mixing sessions', () => {
  const start = event('one', 'run_started');
  const tool = event('one', 'tool_started');
  const finished = event('one', 'run_completed', 'completed');
  const live = event('two', 'run_started');
  assert.deepEqual(mergeRunTraces('current', [start, tool, finished], { sessionId: 'current', events: [start, tool, live] }), [start, tool, finished, live]);
  assert.deepEqual(mergeRunTraces('other', [], { sessionId: 'current', events: [start, live] }), []);
});

test('saved summaries keep only the in-memory previews matching their event IDs', () => {
  const tool = { ...event('one', 'tool_completed', 'completed'), id: 3 };
  const otherTool = { ...tool, id: 4 };
  const finished = event('one', 'run_completed', 'completed');
  const details = { tool_name: 'example', input: { text: '{"value":2}', truncated: false } };
  const live = { sessionId: 'current', events: [{ ...otherTool, tool_details: details }] };
  assert.deepEqual(mergeRunTraces('current', [tool, otherTool, finished], live), [tool, { ...otherTool, tool_details: details }, finished]);
  assert.deepEqual(mergeRunTraces('current', [tool, otherTool, finished], null), [tool, otherTool, finished]);
});

test('a partial history read cannot replace a local stop or live events', () => {
  const start = event('one', 'run_started');
  const stopped = event('one', 'run_cancelled', 'cancelled');
  assert.deepEqual(mergeRunTraces('current', [start], { sessionId: 'current', events: [start, stopped] }), [start, stopped]);
  assert.deepEqual(mergeRunTraces('current', [], { sessionId: 'current', events: [start, stopped] }), [start, stopped]);
});

test('each reply can retrieve only its own ordered trace events', () => {
  const first = event('one', 'run_started');
  const second = event('two', 'run_started');
  const finished = event('one', 'run_completed', 'completed');
  const groups = groupRunTraces([first, second, finished, event(undefined, 'unlinked')]);
  assert.deepEqual(groups.get('one'), [first, finished]);
  assert.deepEqual(groups.get('two'), [second]);
  assert.equal(groups.size, 2);
});

test('legacy model errors and generated plan excerpts have safe display summaries', () => {
  assert.equal(traceSummary({ ...event('one', 'model_error', 'failed'), summary: 'provider supplied secret' }), '模型调用或运行失败');
  assert.equal(traceSummary({ ...event('one', 'plan_step_started'), summary: '第 1/2 步：private plan' }), '第 1/2 步开始执行');
});
