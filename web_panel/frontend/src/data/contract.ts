import type { Envelope, Result, Source } from '../contracts/types';
import type { BrowserResult, ClosedResult, ResultIdentity } from '../../../contracts/panel-contract-v1/typescript/panel-contract';
// Generated schema types describe JSON integers as number. Our boundary parser
// deliberately retains unsafe integer tokens as decimal strings for exact display.
type LosslessIdentity = Omit<ResultIdentity, 'resultOrdinal'> & {resultOrdinal:number|string};
type LosslessClosedResult = Omit<ClosedResult, 'identity'> & {identity:LosslessIdentity};
type LosslessBrowserResult = Omit<BrowserResult, 'result'> & {result:LosslessClosedResult};
/** Translate the official DTO only at the view boundary; never recompute revisions. */
export function normalizeMessage(raw: unknown, viewId: string, jobId: string, runtimeInstanceId?: string, allowedRunIds:readonly string[] = [jobId]): Envelope {
    if (!raw || typeof raw !== 'object') throw Error('Invalid message');
    const message = raw as Record<string,unknown>;
    if (typeof message.event !== 'string') {
        const internal=message.result as (Result & {gatewayTransformed?:boolean;runtimeInstanceId?:string;projectId?:string}) | undefined;
        if (['freeze_expired','package_updated','permission_changed','reset_required'].includes(String(message.type))) return {...message,sessionEpoch:undefined} as unknown as Envelope; // notices do not switch result identity; reset_required explicitly invalidates
        if (!internal?.gatewayTransformed) return raw as Envelope; // mock
        if (!internal.jobId || !allowedRunIds.includes(internal.jobId) || !internal.runtimeInstanceId || runtimeInstanceId && internal.runtimeInstanceId!==runtimeInstanceId || !internal.projectId || !internal.resultScopeId || !internal.resultKey) throw Error('Wrong transformed result identity');
        const sources=Object.fromEntries(Object.entries(internal.sources).map(([sourceId,source])=>[sourceId,{...source,...(source.image && ['available','ready','ok'].includes(source.state.toLowerCase())?{image:{...source.image,url:`/api/views/${encodeURIComponent(viewId)}/assets/${encodeURIComponent(sourceId)}?scope=${encodeURIComponent(internal.resultScopeId!)}&resultKey=${encodeURIComponent(internal.resultKey)}`}}:{})}]));
        return {...message,result:{...internal,sources},sessionEpoch:`${internal.runtimeInstanceId}/${internal.projectId}/${jobId}`} as Envelope;
    }
    if (typeof message.runtimeInstanceId !== 'string' || typeof message.projectId !== 'string') throw Error('Missing Runtime identity');
    if (runtimeInstanceId && message.runtimeInstanceId !== runtimeInstanceId) throw Error('Runtime identity changed; reselect the target');
    if (message.jobId && !allowedRunIds.includes(String(message.jobId)) && message.event !== 'job_changed') throw Error('Wrong Job identity');
    if (message.event !== 'result') return {...message,type:message.event} as unknown as Envelope;
    const closed = message.result as LosslessBrowserResult['result'];
    const identity = closed?.identity;
    if (!identity || !allowedRunIds.includes(identity.jobId) || identity.runtimeInstanceId !== message.runtimeInstanceId || typeof identity.resultScopeId !== 'string' || typeof identity.resultKey !== 'string') throw Error('Wrong result identity');
    if (!Array.isArray(closed.sources) || !Array.isArray(closed.expectedSourceIds)) throw Error('Invalid closed result');
    const sources:Record<string,Source>={};
    for(const source of closed.sources) {
        if(typeof source.sourceId!=='string' || Object.hasOwn(sources,source.sourceId)) throw Error('Duplicate source');
        sources[source.sourceId]={state:source.state.toLowerCase(),valueJson:source.valueJson ?? undefined,reason:source.reason ?? undefined,...(source.image?{image:{...source.image,url:`/api/views/${encodeURIComponent(viewId)}/assets/${encodeURIComponent(source.sourceId)}?scope=${encodeURIComponent(identity.resultScopeId)}&resultKey=${encodeURIComponent(identity.resultKey)}`}}:{})};
    }
    if (Object.keys(sources).length !== closed.expectedSourceIds.length || closed.expectedSourceIds.some(id=>!Object.hasOwn(sources,id))) throw Error('Incomplete source coverage');
    const result:Result={...identity,resultKey:identity.resultKey,resultScopeId:identity.resultScopeId,resultOrdinal:String(identity.resultOrdinal),sources};
    return {type:'result',event:'result',result,sessionEpoch:`${message.runtimeInstanceId}/${message.projectId}/${jobId}`};
}

/** Per-view SDK CaptureCoverage failures are presentation availability, not rewritten wire DTOs. */
export function applySourceProblems(result:Result,problems:Record<string,string>):Result {
    if (!Object.keys(problems).length) return result;
    const sources={...result.sources};
    for(const [sourceId,problem] of Object.entries(problems)) sources[sourceId]={state:'unavailable',reason:problem || 'SOURCE_NOT_CAPTURED'};
    return {...result,sources};
}
