import { parseLosslessJSON } from './lossless';
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body && !(options.body instanceof Blob))
        headers.set('Content-Type', 'application/json');
    if (options.method && !['GET', 'HEAD'].includes(options.method))
        headers.set('X-CSRF-Token', csrf);
    const res = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    if (!res.ok) {
        let message = `请求失败 (${res.status})`;
        try {
            const body = await res.json();
            message = typeof body.detail === 'string' ? body.detail : body.error?.message || message;
        }
        catch { }
        throw new Error(message);
    }
    return res.status === 204 ? undefined as T : parseLosslessJSON(await res.text()) as T;
}
export function safeAssetUrl(value: unknown): string | undefined { if (typeof value !== 'string')
    return; try {
    const url = new URL(value, location.origin);
    if (url.origin === location.origin && (/^\/api\/(assets|history-images)\//.test(url.pathname) || /^\/api\/views\/[^/]+\/(images|history-images|assets)\/[^/]+$/.test(url.pathname)))
        return url.href;
}
catch { } }
