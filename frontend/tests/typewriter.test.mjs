import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTypewriter } from '../src/typewriter.ts';

function harness(options = {}) {
  const ticks = new Map();
  const chunks = [];
  let id = 0;
  const writer = createTypewriter(text => chunks.push(text), {
    schedule: callback => { ticks.set(++id, callback); return id; },
    cancel: handle => ticks.delete(handle),
    ...options,
  });
  const step = () => {
    const [handle, callback] = ticks.entries().next().value;
    ticks.delete(handle);
    callback();
  };
  return { ticks, chunks, writer, step };
}

test('a large provider delta appears incrementally and finishes without losing Unicode', async () => {
  const { chunks, writer, ticks, step } = harness();
  const text = '你👩‍💻好！这是逐字显示。';
  writer.push(text);
  assert.equal(chunks.at(0), '你');
  const done = writer.finish();
  step();
  assert.equal(chunks[1], '👩‍💻');
  while (ticks.size) step();
  await done;
  assert.equal(chunks.join(''), text);
});

test('stop keeps received content immediately, dispose cannot update a different chat', async () => {
  const { writer, chunks, ticks } = harness();
  writer.push('一二三');
  const finished = writer.finish();
  writer.stop();
  await finished;
  assert.equal(chunks.join(''), '一二三');
  assert.equal(ticks.size, 0);
  const other = harness();
  other.writer.push('abc');
  const otherDone = other.writer.finish();
  other.writer.dispose();
  await otherDone;
  assert.deepEqual(other.chunks, ['a']);
  assert.equal(other.ticks.size, 0);
});

test('reduced motion displays immediately without scheduling animation', async () => {
  const { writer, chunks, ticks } = harness({ reducedMotion: true });
  writer.push('整段立即显示');
  await writer.finish();
  assert.deepEqual(chunks, ['整段立即显示']);
  assert.equal(ticks.size, 0);
});
