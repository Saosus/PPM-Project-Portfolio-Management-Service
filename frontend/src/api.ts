export type ApiOptions = Omit<RequestInit, 'headers'> & { headers?: Record<string, string> };

function token(): string | null {
  return localStorage.getItem('ppm_token');
}

export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (options.body && typeof options.body === 'string') headers['Content-Type'] = 'application/json';
  const response = await fetch(path.startsWith('/api/') ? path : `/api/${path.replace(/^\//, '')}`, {
    ...options,
    headers,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body.error ?? body.detail ?? JSON.stringify(body);
    } catch { /* response might not be JSON */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('text/csv')) return await response.blob() as T;
  return await response.json() as T;
}

export function json(body: unknown): string {
  return JSON.stringify(body);
}
