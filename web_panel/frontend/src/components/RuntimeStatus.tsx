import { useEffect, useState } from 'react';
import { api } from '../data/api';
interface Event { sequence:string; at:string; kind:string; runId?:string; message:string; }
interface Status { counters:{source:string;total:string;ok:string;ng:string;at:string}; events:{events:Event[];nextSequence:string;resetRequired:boolean;source:string}; }
export function RuntimeStatus({runtimeId}:{runtimeId:string}) {
 const [status,setStatus]=useState<Status>(),[events,setEvents]=useState<Event[]>([]),[error,setError]=useState('');
 useEffect(()=>{setStatus(undefined);setEvents([]);setError('');if(!runtimeId)return;
  let alive=true,cursor='0',timer:ReturnType<typeof setTimeout>;const abort=new AbortController();
  const poll=async()=>{try{const data=await api<Status>(`/api/runtimes/${encodeURIComponent(runtimeId)}/status?after=${encodeURIComponent(cursor)}`,{signal:abort.signal});if(!alive)return;
   if(!/^\d{1,20}$/.test(data.events.nextSequence))throw Error('无效事件游标');
   cursor=data.events.nextSequence;setStatus(data);setError('');setEvents(old=>{const map=new Map((data.events.resetRequired?[]:old).map(e=>[e.sequence,e]));for(const event of data.events.events)map.set(event.sequence,event);return [...map.values()].slice(-200)});
  }catch(e){if(alive)setError((e as Error).message)}finally{if(alive)timer=setTimeout(poll,3000)}};
  void poll();return()=>{alive=false;abort.abort();clearTimeout(timer)};
 },[runtimeId]);
 return <details className="runtime-events"><summary>权威计数与任务事件</summary>{error&&<p role="alert">读取失败：{error}；以下如有旧值不代表当前状态。</p>}{status?<><p>总数 {status.counters.total} · OK {status.counters.ok} · NG {status.counters.ng}</p><small>计数来源：{status.counters.source} · {status.counters.at}；不按浏览器收到的结果帧累计。</small><p>日志来源：{status.events.source} · 游标 {status.events.nextSequence} · 仅保留本次查看最近 200 条</p><div className="table-scroll"><table><thead><tr><th>序号 / 时间</th><th>类型 / 运行</th><th>内容</th></tr></thead><tbody>{events.map(event=><tr key={event.sequence}><td>{event.sequence} / {event.at}</td><td>{event.kind} / {event.runId||'—'}</td><td>{event.message}</td></tr>)}</tbody></table></div></>:!error&&<p>正在获取 Runtime 权威状态…</p>}</details>;
}
