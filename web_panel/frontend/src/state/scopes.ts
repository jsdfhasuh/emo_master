import type { Result } from '../contracts/types';
export interface ScopeStatus {jobId:string;scopeId:string;latestStartedOrdinal?:string;expiredOrdinal?:string}
export type ScopeStatuses=Record<string,ScopeStatus>;
export const scopeAddress=(jobId:string,scopeId:string)=>JSON.stringify([jobId,scopeId]);
export function mergeScopeStatus(old:ScopeStatuses,jobId:string,started:Record<string,string>,expired:Record<string,string>):ScopeStatuses {
 const next={...old};
 for(const [values,field] of [[started,'latestStartedOrdinal'],[expired,'expiredOrdinal']] as const)for(const [scopeId,ordinal] of Object.entries(values)){
  if(!/^\d{1,20}$/.test(ordinal))throw Error('Invalid scope ordinal');
  const key=scopeAddress(jobId,scopeId),prior=next[key] || {jobId,scopeId};
  next[key]={...prior,[field]:prior[field] && BigInt(prior[field]!)>BigInt(ordinal)?prior[field]:ordinal};
 }
 return next;
}
export function isScopeResultExpired(result:Result,statuses:ScopeStatuses,frozenKey?:string):boolean {
 if(frozenKey===result.resultKey)return false;
 if(!result.jobId || !result.resultScopeId || !result.resultOrdinal)return false;
 const expired=statuses[scopeAddress(result.jobId,result.resultScopeId)]?.expiredOrdinal;
 return expired!==undefined && BigInt(result.resultOrdinal)<=BigInt(expired);
}
export function scopeActivity(status:ScopeStatus,result?:Result,frozen=false):string {
 if(frozen)return `已冻结查看 #${result?.resultOrdinal || '—'} · 最近开始 #${status.latestStartedOrdinal || '—'}`;
 if(status.expiredOrdinal && (!result || BigInt(result.resultOrdinal || '0')<=BigInt(status.expiredOrdinal)))return `结果已过期 ≤ ${status.expiredOrdinal}`;
 if(status.latestStartedOrdinal && (!result || BigInt(status.latestStartedOrdinal)>BigInt(result.resultOrdinal || '0')))return `处理中 #${status.latestStartedOrdinal} · 保留最近完成结果`;
 return `最近开始 #${status.latestStartedOrdinal || '—'}`;
}
