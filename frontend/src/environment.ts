export function parseEnvironmentLines(input: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const rawLine of input.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const separator = line.indexOf('=');
    if (separator <= 0) throw new Error('环境变量请按 KEY=VALUE 格式填写，每行一项。');
    const key = line.slice(0, separator).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) throw new Error(`环境变量名称无效：${key}`);
    result[key] = line.slice(separator + 1);
  }
  return result;
}
