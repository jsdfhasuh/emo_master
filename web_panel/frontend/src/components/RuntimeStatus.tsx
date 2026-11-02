import { useEffect, useState } from 'react';
import { api } from '../data/api';
export interface JobEvent { sequence:string; at?:string; timestampMs?:string; kind:string; runId?:string; message:string; level?:string; code?:string; }
interface EventPage {events:JobEvent[];nextSequence:string;resetRequired:boolean;source:string;runtimeInstanceId?:string;projectId?:string}
interface Status { runtime?:{runtimeInstanceId?:string;projectId?:string}; counters:{source:string;total?:string;ok?:string;ng?:string;at?:string;counters?:{name:string;value:string;updatedAtMs:string}[]}; events:EventPage; }
export function mergeJobEvents(old:JobEvent[],page:EventPage):JobEvent[] {
 if(!/^\d+$/.test(page.nextSequence))throw Error('无效事件游标');
 const values=new Map((page.resetRequired?[]:old).map(event=>[event.sequence,event]));
 for(const event of page.events){if(!/^\d+$/.test(event.sequence))throw Error('无效事件序号');values.set(event.sequence,event)}
 return [...values.values()].sort((a,b)=>BigInt(a.sequence)<BigInt(b.sequence)?-1:BigInt(a.sequence)>BigInt(b.sequence)?1:0).slice(-100);
}
const timestamp=(value?:string)=>{if(!value)return '—';const date=new Date(Number(value));return Number.isNaN(date.valueOf())?value:date.toLocaleString()};
export function RuntimeStatus({runtimeId,runId='',runtimeInstanceId,projectId}:{runtimeId:string;runId?:string;runtimeInstanceId?:string;projectId?:string}) {
 const [status,setStatus]=useState<Status>(),[events,setEvents]=useState<JobEvent[]>([]),[error,setError]=useState(''),[eventError,setEventError]=useState(''),[eventPage,setEventPage]=useState<EventPage>();
 useEffect(()=>{setStatus(undefined);setError('');if(!runtimeId)return;
  let alive=true,timer:ReturnType<typeof setTimeout>;const abort=new AbortController();
  const poll=async()=>{try{const data=await api<Status>(`/api/runtimes/${encodeURIComponent(runtimeId)}/status`,{signal:abort.signal});if(!alive)return;setStatus(data);setError('');}catch(e){if(alive)setError((e as Error).message)}finally{if(alive)timer=setTimeout(poll,3000)}};
  void poll();return()=>{alive=false;abort.abort();clearTimeout(timer)};
 },[runtimeId,runtimeInstanceId,projectId]);
 const observedInstance=status?.runtime?.runtimeInstanceId || runtimeInstanceId;
 const observedProject=status?.runtime?.projectId || projectId;
 useEffect(()=>{setEvents([]);setEventPage(undefined);setEventError('');if(!runtimeId||!runId)return;
  if(runtimeInstanceId && observedInstance && runtimeInstanceId!==observedInstance || projectId && observedProject && projectId!==observedProject){setEventError('Runtime 身份改变，请重新选择运行');return;}
  let alive=true,cursor='0',timer:ReturnType<typeof setTimeout>;const abort=new AbortController();
  const poll=async()=>{try{const data=await api<EventPage>(`/api/runtimes/${encodeURIComponent(runtimeId)}/runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(cursor)}&runtimeInstanceId=${encodeURIComponent(runtimeInstanceId || observedInstance || '')}&projectId=${encodeURIComponent(projectId || observedProject || '')}`,{signal:abort.signal});if(!alive)return;
   if(data.runtimeInstanceId && observedInstance && data.runtimeInstanceId!==observedInstance || data.projectId && observedProject && data.projectId!==observedProject)throw Error('Runtime 身份改变，请重新选择运行');
   if(data.events.some(event=>event.runId && event.runId!==runId))throw Error('任务事件身份不匹配');
   if(!/^\d+$/.test(data.nextSequence))throw Error('无效事件游标');
   if(BigInt(data.nextSequence)<BigInt(cursor)){cursor='0';setEvents([]);setEventPage(undefined);return;}
   cursor=data.nextSequence;setEvents(old=>mergeJobEvents(old,data));setEventPage(data);setEventError('');
  }catch(e){if(alive)setEventError((e as Error).message)}finally{if(alive)timer=setTimeout(poll,3000)}};
  void poll();return()=>{alive=false;abort.abort();clearTimeout(timer)};
 },[runtimeId,runId,runtimeInstanceId,projectId,observedInstance,observedProject]);
 const counters=status?.counters;
 return <details className="runtime-events"><summary>权威计数与任务事件</summary>{error&&<p role="alert">读取失败：{error}；以下如有旧值不代表当前状态。</p>}{counters?<>
 {Array.isArray(counters.counters)?<><h3>Runtime 全局计数器</h3>{counters.counters.length?<table><thead><tr><th>名称</th><th>权威值</th><th>更新时间</th></tr></thead><tbody>{counters.counters.map(counter=><tr key={counter.name}><td>{counter.name}</td><td>{counter.value}</td><td>{timestamp(counter.updatedAtMs)}</td></tr>)}</tbody></table>:<p>当前工程未声明全局计数器。</p>}</>:<p>总数 {counters.total ?? '—'} · OK {counters.ok ?? '—'} · NG {counters.ng ?? '—'}</p>}
 <small>计数来源：{counters.source}；不按浏览器收到的结果帧累计。</small></>:!error&&<p>正在获取 Runtime 权威计数…</p>}
 <h3>所选运行事件：{runId || '尚未选择'}</h3>{eventError&&<p role="alert">日志读取失败：{eventError}；旧日志不代表当前状态。</p>}
 {!runId?<p>请明确选择查看运行；计数与日志不依赖页面结果帧。</p>:<><p>日志来源：{eventPage?.source || '等待 Runtime'} · 游标 {eventPage?.nextSequence || '0'} · 仅保留当前所选运行最近 100 条</p><div className="table-scroll"><table><thead><tr><th>序号 / 时间</th><th>类型 / 级别</th><th>内容</th></tr></thead><tbody>{events.map(event=><tr key={event.sequence}><td>{event.sequence} / {event.at || timestamp(event.timestampMs)}</td><td>{event.kind} / {event.level || '—'}</td><td>{event.code && `${event.code} · `}{event.message}</td></tr>)}</tbody></table></div></>}
 </details>;
}
