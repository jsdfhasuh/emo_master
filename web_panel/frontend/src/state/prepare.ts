import type { Result, Page, Component } from '../contracts/types';
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
/** Only prepare image bindings actually visible on this page for this scope. */
export function pageImageSources(page: Page | undefined, scope: string): Set<string> {
    const ids = new Set<string>();
    const visit = (component: Component) => {
        const binding = component.binding;
        if (component.type === 'image' && binding) {
            const sourceId = typeof binding === 'string' ? binding : binding.sourceId;
            const requestedScope = typeof binding === 'string' ? undefined : binding.scopeId;
            if (sourceId && (!requestedScope || requestedScope === scope)) ids.add(sourceId);
        }
        component.children?.forEach(visit);
    };
    page?.components.forEach(visit); return ids;
}
/** Fetch once, decode those exact bytes, then atomically publish the scope. */
export async function prepareResult(result: Result, signal: AbortSignal, requiredImages?: Set<string>): Promise<Result> {
    const sources = Object.fromEntries(Object.entries(result.sources).map(([id, s]) => [id, { ...s }]));
    await Promise.all(Object.entries(sources).map(async ([id, s]) => {
        if (!s.image) return;
        if (requiredImages && !requiredImages.has(id)) { sources[id] = { state: 'unavailable', reason: '当前页面无需此图片' }; return; }
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
            const limit = 8 * 1024 * 1024;
            const expectedSize = s.image.byteSize === undefined ? undefined : Number(s.image.byteSize);
            if (expectedSize !== undefined && (!Number.isSafeInteger(expectedSize) || expectedSize < 0 || expectedSize > limit)) throw new Error('invalid declared size');
            let blob: Blob;
            if (response.body) {
                const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = []; let size = 0;
                try { while (true) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength; if (size > limit) { await reader.cancel(); throw new Error('image exceeds limit'); } chunks.push(new Uint8Array(value)); } }
                finally { reader.releaseLock(); }
                blob = new Blob(chunks, { type: response.headers.get('Content-Type')?.split(';')[0] || '' });
            } else blob = await response.blob();
            if (blob.size > limit || (expectedSize !== undefined && blob.size !== expectedSize)) throw new Error('image size mismatch');
            if (s.image.mimeType && blob.type !== s.image.mimeType) throw new Error('image MIME mismatch');
            if (s.image.sha256) {
                if (!/^[0-9a-f]{64}$/i.test(s.image.sha256)) throw new Error('invalid digest');
                const bytes = new Uint8Array(await blob.arrayBuffer());
                // Pure SHA fallback preserves verification on non-secure LAN HTTP.
                if (sha256Hex(bytes) !== s.image.sha256.toLowerCase()) throw new Error('image hash mismatch');
            }
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
            sources[id] = { state: 'error', reason: '本轮图片加载、大小或摘要校验失败，或已取消' };
        } finally {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            clearTimeout(timer); signal.removeEventListener('abort', abort);
        }
    }));
    return { ...result, sources };
}
/** SHA-256 fallback for LAN HTTP where WebCrypto is unavailable. Bounded input only. */
export function sha256Hex(bytes: Uint8Array): string {
    const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const hash = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    padded.set(bytes); padded[bytes.length] = 0x80;
    const data = new DataView(padded.buffer); data.setUint32(padded.length - 8, Math.floor(bytes.length / 0x20000000)); data.setUint32(padded.length - 4, bytes.length * 8);
    const rotate = (x:number,n:number) => (x >>> n) | (x << (32 - n));
    const w = new Uint32Array(64);
    for (let offset=0; offset<padded.length; offset+=64) {
        for(let i=0;i<16;i++) w[i]=data.getUint32(offset+i*4);
        for(let i=16;i<64;i++){const a=w[i-15],b=w[i-2];w[i]=(rotate(a,7)^rotate(a,18)^(a>>>3))+w[i-16]+(rotate(b,17)^rotate(b,19)^(b>>>10))+w[i-7];}
        let [a,b,c,d,e,f,g,h]=hash;
        for(let i=0;i<64;i++) { const t1=(h+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[i]+w[i])|0;const t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))|0;h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0; }
        [a,b,c,d,e,f,g,h].forEach((value,i)=>{hash[i]=(hash[i]+value)|0;});
    }
    return hash.map(value=>(value>>>0).toString(16).padStart(8,'0')).join('');
}
