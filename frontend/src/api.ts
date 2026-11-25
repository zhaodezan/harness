export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string | null) {
    super(message);
  }
}

export const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail)
      ? body.detail.map((item: { loc?: string[]; msg?: string }) => item.msg && `${item.loc?.at(-1) ?? '字段'}：${item.msg}`).filter(Boolean).join('；') : '';
    throw new ApiError(detail || `请求失败 (${response.status})`, response.status, response.headers.get('X-Harness-Error'));
  }
  return response.status === 204 ? (undefined as T) : response.json();
};
