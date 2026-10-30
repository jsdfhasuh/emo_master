import type { Result } from '../contracts/types';
import { safeAssetUrl } from '../data/api';
/** Prepare the entire scope, never pair a stale image with a new verdict. */
export async function prepareResult(result: Result, signal: AbortSignal): Promise<Result> {
    const sources = Object.fromEntries(Object.entries(result.sources).map(([id, s]) => [id, { ...s }]));
    await Promise.all(Object.entries(sources).map(async ([id, s]) => {
        if (!s.image)
            return;
        const url = safeAssetUrl(s.image.url);
        if (!url || (s.image.ownerResultKey && s.image.ownerResultKey !== result.resultKey)) {
            sources[id] = { state: 'error', reason: '图片身份或地址无效' };
            return;
        }
        await new Promise<void>(resolve => { const img = new Image(); let done = false; const finish = (ok: boolean) => { if (done)
            return; done = true; clearTimeout(timer); signal.removeEventListener('abort', abort); img.onload = null; img.onerror = null; if (!ok)
            sources[id] = { state: 'error', reason: '本轮图片加载失败' }; resolve(); }; const abort = () => { img.src = ''; finish(false); }; const timer = setTimeout(() => finish(false), 10000); img.onload = () => finish(true); img.onerror = () => finish(false); signal.addEventListener('abort', abort, { once: true }); if (signal.aborted)
            abort();
        else
            img.src = url; });
    }));
    return { ...result, sources };
}
