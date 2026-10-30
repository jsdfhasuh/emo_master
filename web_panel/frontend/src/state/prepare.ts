import type { Result } from '../contracts/types';
import { safeAssetUrl } from '../data/api';
// Only URLs allocated here may bypass the remote-resource allowlist.
const preparedUrls = new Set<string>();
export const preparedImageUrl = (url: unknown) => typeof url === 'string' && preparedUrls.has(url) ? url : safeAssetUrl(url);
export function releaseResult(result?: Result) {
    for (const source of Object.values(result?.sources || {})) {
        const url = source.image?.url;
        if (url && preparedUrls.delete(url)) URL.revokeObjectURL(url);
    }
}
/** Fetch once, decode those exact bytes, then atomically publish the scope. */
export async function prepareResult(result: Result, signal: AbortSignal): Promise<Result> {
    const sources = Object.fromEntries(Object.entries(result.sources).map(([id, s]) => [id, { ...s }]));
    await Promise.all(Object.entries(sources).map(async ([id, s]) => {
        if (!s.image) return;
        const url = safeAssetUrl(s.image.url);
        if (!url || (s.image.ownerResultKey && s.image.ownerResultKey !== result.resultKey)) {
            sources[id] = { state: 'error', reason: '图片身份或地址无效' }; return;
        }
        const controller = new AbortController();
        const abort = () => controller.abort();
        signal.addEventListener('abort', abort, { once: true });
        const timer = setTimeout(abort, 10000);
        let objectUrl: string | undefined;
        try {
            if (signal.aborted) controller.abort();
            const response = await fetch(url, { credentials: 'same-origin', signal: controller.signal });
            if (!response.ok) throw new Error('image request failed');
            const blob = await response.blob();
            if (!/^image\/(png|jpeg|webp|gif|bmp)$/.test(blob.type)) throw new Error('unsupported image type');
            objectUrl = URL.createObjectURL(blob);
            await new Promise<void>((resolve, reject) => {
                const img = new Image();
                const finish = (ok: boolean) => { controller.signal.removeEventListener('abort', cancel); img.onload = null; img.onerror = null; if (!ok) img.src = ''; ok ? resolve() : reject(new Error('decode failed')); };
                const cancel = () => finish(false);
                img.onload = () => finish(true); img.onerror = () => finish(false);
                controller.signal.addEventListener('abort', cancel, { once: true });
                if (controller.signal.aborted) cancel(); else img.src = objectUrl!;
            });
            if (controller.signal.aborted) throw new Error('aborted');
            preparedUrls.add(objectUrl);
            sources[id] = { ...s, image: { ...s.image, url: objectUrl } };
            objectUrl = undefined;
        } catch {
            sources[id] = { state: 'error', reason: '本轮图片加载失败或已取消' };
        } finally {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            clearTimeout(timer); signal.removeEventListener('abort', abort);
        }
    }));
    return { ...result, sources };
}
