type Options = {
  reducedMotion?: boolean;
  schedule?: (callback: () => void) => number;
  cancel?: (handle: number) => void;
};

// Animate only text already received from the provider. The reader never waits on a tick.
export function createTypewriter(append: (text: string) => void, options: Options = {}) {
  const schedule = options.schedule ?? ((callback) => window.setTimeout(callback, 25));
  const cancel = options.cancel ?? ((handle) => window.clearTimeout(handle));
  const reducedMotion = options.reducedMotion ?? globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : undefined;
  let queue = '';
  let timer: number | undefined;
  let closed = false;
  let settle: (() => void) | undefined;
  const drained = new Promise<void>((resolve) => { settle = resolve; });

  const tick = (initial = false) => {
    timer = undefined;
    // Re-segment the remaining text so graphemes split across pending deltas join up.
    const graphemes = segmenter ? segmenter.segment(queue) : queue;
    const count = initial ? 1 : Math.max(1, Math.ceil(queue.length / (closed ? 16 : 32)));
    let end = 0;
    let remaining = count;
    for (const item of graphemes) {
      end += (typeof item === 'string' ? item : item.segment).length;
      if (--remaining <= 0) break;
    }
    const text = queue.slice(0, end);
    queue = queue.slice(end);
    if (text) append(text);
    if (queue) timer = schedule(() => tick());
    else if (closed) settle?.();
  };

  const clear = (flush: boolean) => {
    closed = true;
    if (timer !== undefined) cancel(timer);
    timer = undefined;
    if (flush && queue) append(queue);
    queue = '';
    settle?.();
  };

  return {
    push(text: string) {
      if (closed || !text) return;
      if (reducedMotion) { append(text); return; }
      queue += text;
      // Keep a clock between small deltas too, so a burst doesn't render as one React update.
      if (timer === undefined) {
        tick(true);
        if (timer === undefined) timer = schedule(() => tick());
      }
    },
    finish() {
      closed = true;
      if (!queue) clear(false);
      return drained;
    },
    stop: () => clear(true),
    dispose: () => clear(false),
  };
}
