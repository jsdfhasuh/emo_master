import { parseLosslessJSON } from './lossless';
let csrf = '';
let sessionGeneration = 0;
const sessionExpiredListeners = new Set<() => void>();
export class ApiError extends Error {
    constructor(message: string, public readonly status: number, public readonly code = '', public readonly runtimeInstanceId = '') { super(message); this.name = 'ApiError'; }
}
export function onSessionExpired(listener: () => void) {
    sessionExpiredListeners.add(listener);
    return () => { sessionExpiredListeners.delete(listener); };
}
export function getSessionGeneration() { return sessionGeneration; }
export function setCsrf(value: string) { csrf = value; sessionGeneration++; }
async function request(path: string, options: RequestInit = {}): Promise<Response> {
    const requestSession = sessionGeneration;
    const headers = new Headers(options.headers);
    if (options.body && !(options.body instanceof Blob))
        headers.set('Content-Type', 'application/json');
    if (options.method && !['GET', 'HEAD'].includes(options.method))
        headers.set('X-CSRF-Token', csrf);
    const res = await fetch(path, { ...options, headers, credentials: 'same-origin' });
    if (!res.ok) {
        let message = `请求失败 (${res.status})`, code = '', runtimeInstanceId = '';
        try {
            const body = await res.json();
            message = typeof body.detail === 'string' ? body.detail : body.message || body.error?.message || message;
            code = typeof body.code === 'string' ? body.code : '';
            runtimeInstanceId = typeof body.runtimeInstanceId === 'string' ? body.runtimeInstanceId : '';
        }
        catch { }
        if (res.status === 401 && !['/api/session', '/api/preview/session'].includes(path)) {
            message = '会话已失效，请重新登录；未确认操作已保留，不会自动重发。';
            if (requestSession === sessionGeneration) sessionExpiredListeners.forEach(listener => listener());
        }
        throw new ApiError(message, res.status, code, runtimeInstanceId);
    }
    return res;
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await request(path, options);
    return res.status === 204 ? undefined as T : parseLosslessJSON(await res.text()) as T;
}
export async function apiBlob(path: string, options: RequestInit = {}): Promise<Blob> {
    return (await request(path, options)).blob();
}
export function safeAssetUrl(value: unknown): string | undefined { if (typeof value !== 'string')
    return; try {
    const url = new URL(value, location.origin);
    if (url.origin === location.origin && (/^\/api\/(assets|history-images)\//.test(url.pathname) || /^\/api\/views\/[^/]+\/(images|history-images|assets)\/[^/]+$/.test(url.pathname) || /^\/api\/editor\/[^/]+\/assets\/[^/]+$/.test(url.pathname)))
        return url.href;
}
catch { } }
