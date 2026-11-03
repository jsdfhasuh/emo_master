import { parseLosslessJSON } from './lossless';
let csrf = '';
let sessionGeneration = 0;
const sessionExpiredListeners = new Set<() => void>();
export class ApiError extends Error {
    constructor(message: string, public readonly status: number) { super(message); this.name = 'ApiError'; }
}
export function onSessionExpired(listener: () => void) {
    sessionExpiredListeners.add(listener);
    return () => { sessionExpiredListeners.delete(listener); };
}
export function setCsrf(value: string) { csrf = value; sessionGeneration++; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
    const requestSession = sessionGeneration;
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
        if (res.status === 401 && !['/api/session', '/api/preview/session'].includes(path)) {
            message = '会话已失效，请重新登录；未确认操作已保留，不会自动重发。';
            if (requestSession === sessionGeneration) sessionExpiredListeners.forEach(listener => listener());
        }
        throw new ApiError(message, res.status);
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
