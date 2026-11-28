export type ChatLinkSegment =
  | { type: 'text'; value: string }
  | { type: 'workspace-file'; label: string; path: string }
  | { type: 'web'; label: string; href: string };

const DRIVE_PATH = /^[A-Za-z]:/;
const RAW_WEB_LINK = /^https?:\/\/[^\s<>"'`]+/i;
const TRAILING_PUNCTUATION = /[.,!?;:]+$/;

export function normalizeWorkspaceLinkPath(path: string): string | null {
  if (!path || path.includes('\u0000')) return null;
  const normalized = path.replaceAll('\\', '/');
  if (normalized.startsWith('/') || DRIVE_PATH.test(normalized)) return null;
  const parts = normalized.split('/');
  if (parts.some((part) => !part || part === '.' || part === '..')) return null;
  return parts.join('/');
}

function trimWebPunctuation(candidate: string): string {
  let value = candidate;
  while (TRAILING_PUNCTUATION.test(value)) value = value.replace(TRAILING_PUNCTUATION, '');
  while (value.endsWith(')')) {
    const opening = (value.match(/\(/g) ?? []).length;
    const closing = (value.match(/\)/g) ?? []).length;
    if (closing <= opening) break;
    value = value.slice(0, -1);
  }
  return value;
}

function normalizeWebLink(candidate: string): string | null {
  const value = trimWebPunctuation(candidate);
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return null;
    return value;
  } catch {
    return null;
  }
}

function pushText(segments: ChatLinkSegment[], value: string) {
  if (!value) return;
  const previous = segments.at(-1);
  if (previous?.type === 'text') previous.value += value;
  else segments.push({ type: 'text', value });
}

function markdownAt(text: string, index: number): ChatLinkSegment | null {
  if (text[index] !== '[') return null;
  const match = text.slice(index).match(/^\[([^\]\r\n]*)\]\(([^)\s]+)\)/);
  if (!match) return null;
  const [, label, target] = match;
  const filePath = normalizeWorkspaceLinkPath(target);
  if (filePath) return { type: 'workspace-file', label, path: filePath };
  const href = normalizeWebLink(target);
  return href ? { type: 'web', label, href } : null;
}

export function parseChatLinks(text: string): ChatLinkSegment[] {
  const segments: ChatLinkSegment[] = [];
  let index = 0;
  while (index < text.length) {
    const markdown = markdownAt(text, index);
    if (markdown) {
      segments.push(markdown);
      const match = text.slice(index).match(/^\[[^\]\r\n]*\]\(([^)\s]+)\)/);
      index += match?.[0].length ?? 1;
      continue;
    }

    const raw = text.slice(index).match(RAW_WEB_LINK)?.[0];
    if (raw && (index === 0 || !/[\w@]/.test(text[index - 1]))) {
      const href = normalizeWebLink(raw);
      if (href) {
        segments.push({ type: 'web', label: href, href });
        pushText(segments, raw.slice(href.length));
        index += raw.length;
        continue;
      }
    }

    pushText(segments, text[index]);
    index += 1;
  }
  return segments;
}
