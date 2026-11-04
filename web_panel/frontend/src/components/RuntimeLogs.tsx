import { useEffect, useRef, useState } from 'react';
import { api, apiBlob, ApiError, getSessionGeneration, onSessionExpired } from '../data/api';
import type { RuntimeLogEvent, RuntimeLogPage, RuntimeLogQuery } from '../contracts/types';

export function mergeRuntimeLogs(old:RuntimeLogEvent[], page:RuntimeLogPage):RuntimeLogEvent[] {
    if(!page.ok)throw new ApiError(page.message,409,page.code,page.runtimeInstanceId);
    if(!/^\d+$/.test(page.nextEventId)||!/^\d+$/.test(page.upperEventId))throw Error('无效日志游标');
    const values=new Map(old.map(item=>[item.eventId,item]));
    for(const item of page.events){if(!/^\d+$/.test(item.eventId))throw Error('无效日志 ID');values.set(item.eventId,item)}
    return [...values.values()].sort((a,b)=>BigInt(a.eventId)<BigInt(b.eventId)?-1:BigInt(a.eventId)>BigInt(b.eventId)?1:0);
}
export const RUNTIME_LIVE_LOG_LIMIT=1000;

type Cursor={instance:string;after:string;upper?:string;initialized:boolean;events:RuntimeLogEvent[]};
// Hidden/reopened views keep the consumed cursor for the same target and filter.
const liveCursors=new Map<string,Cursor>();
const unsupportedRuntimes=new Map<string,string>();
let logSessionGeneration=-1;
onSessionExpired(()=>{liveCursors.clear();unsupportedRuntimes.clear()});
const fresh=():Cursor=>({instance:'',after:'0',initialized:false,events:[]});
const queryString=(query:RuntimeLogQuery)=>new URLSearchParams(Object.entries(query).filter(([,v])=>v!==undefined&&v!=='') as [string,string][]).toString();

export function RuntimeLogs({runtimeId,runtimeInstanceId=''}:{runtimeId:string;runtimeInstanceId?:string}) {
    if(logSessionGeneration!==getSessionGeneration()){liveCursors.clear();unsupportedRuntimes.clear();logSessionGeneration=getSessionGeneration()}
    const [mode,setMode]=useState<'live'|'history'>('live');
    const [draft,setDraft]=useState<RuntimeLogQuery>({}),[filter,setFilter]=useState<RuntimeLogQuery>({});
    const [events,setEvents]=useState<RuntimeLogEvent[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
    const [hasMore,setHasMore]=useState(false),[revision,setRevision]=useState(0),[pageRequest,setPageRequest]=useState(0);
    const [detail,setDetail]=useState<RuntimeLogEvent>(),[exporting,setExporting]=useState(false),[feedback,setFeedback]=useState('');
    const cursor=useRef<Cursor>(fresh()), download=useRef<AbortController | undefined>(undefined), readAbort=useRef<AbortController | undefined>(undefined);
    const filterKey=JSON.stringify(filter),key=runtimeId+'|'+filterKey;
    useEffect(()=>()=>download.current?.abort(),[key]);
    useEffect(()=>{cursor.current=mode==='live'?(liveCursors.get(key)||fresh()):fresh();setEvents(cursor.current.events);setError('');setHasMore(false);setFeedback('');setDetail(undefined);},[key,mode,revision]);
    function resetInstance(instance:string){
        const next=fresh();next.instance=instance;cursor.current=next;
        liveCursors.delete(key);
        if(mode==='live')liveCursors.set(key,next);
        readAbort.current?.abort();download.current?.abort();
        setEvents([]);setDetail(undefined);setHasMore(false);setFeedback('');setExporting(false);
        setPageRequest(value=>value+1);
    }
    useEffect(()=>{
        // /status and the first log query are independent reads. Confirming the
        // identity already learned by that query does not create a new range.
        if(!runtimeInstanceId)return;
        if(cursor.current.instance&&cursor.current.instance!==runtimeInstanceId){
            resetInstance(runtimeInstanceId);
        }else cursor.current.instance=runtimeInstanceId;
    },[runtimeInstanceId,key,mode]);
    useEffect(()=>{
        if(!runtimeId)return;
        if(unsupportedRuntimes.get(runtimeId)===runtimeInstanceId){setError('UNIMPLEMENTED · 此 Runtime 不支持历史日志查询');return;}
        let alive=true,timer:ReturnType<typeof setTimeout>;const abort=new AbortController();
        const current=cursor.current;readAbort.current=abort;
        const read=async()=>{
            setBusy(true);let retry=true;
            try {
                if(mode==='live'&&!current.initialized){
                    const baseline=await api<RuntimeLogPage>(`/api/runtimes/${encodeURIComponent(runtimeId)}/logs?${queryString({limit:'1',runtimeInstanceId:current.instance})}`,{signal:abort.signal});
                    if(!alive||cursor.current!==current)return;mergeRuntimeLogs([],baseline);
                    if(current.instance&&baseline.runtimeInstanceId!==current.instance)throw new ApiError('Runtime 实例改变',409,'RESET_REQUIRED',baseline.runtimeInstanceId);
                    current.instance=baseline.runtimeInstanceId;current.after=baseline.upperEventId;current.initialized=true;
                }
                do {
                    const query={...filter,runtimeInstanceId:current.instance,afterEventId:current.after,
                        ...(current.upper!==undefined?{upperEventId:current.upper}:{}),limit:'200'};
                    const page=await api<RuntimeLogPage>(`/api/runtimes/${encodeURIComponent(runtimeId)}/logs?${queryString(query)}`,{signal:abort.signal});
                    if(!alive||cursor.current!==current)return;
                    const merged=mergeRuntimeLogs(current.events,page);
                    if(current.instance&&page.runtimeInstanceId!==current.instance)throw new ApiError('Runtime 实例改变',409,'RESET_REQUIRED',page.runtimeInstanceId);
                    current.events=mode==='live'?merged.slice(-RUNTIME_LIVE_LOG_LIMIT):merged;current.instance=page.runtimeInstanceId;current.after=page.nextEventId;current.upper=page.upperEventId;current.initialized=true;
                    if(mode==='live')liveCursors.set(key,current);
                    setEvents(current.events);setHasMore(page.hasMore);setError('');
                    if(mode==='history'||!page.hasMore)break;
                } while(alive);
                if(mode==='live')current.upper=undefined;
            } catch(e) {
                if(!alive||cursor.current!==current)return;
                const failure=e as ApiError;
                if(failure.code==='RESET_REQUIRED'){
                    resetInstance(failure.runtimeInstanceId);retry=false;
                } else {
                    setError((failure.code?failure.code+' · ':'')+failure.message);
                    if(failure.code==='UNIMPLEMENTED')unsupportedRuntimes.set(runtimeId,runtimeInstanceId);
                    if(['UNIMPLEMENTED','EVENT_TOO_LARGE','INVALID_ARGUMENT'].includes(failure.code))retry=false;
                }
            } finally {if(alive&&cursor.current===current){setBusy(false);if(mode==='live'&&retry)timer=setTimeout(read,3000)}}
        };
        void read();return()=>{alive=false;abort.abort();clearTimeout(timer)};
    },[key,mode,revision,pageRequest]);
    async function exportHistory(){
        download.current?.abort();const abort=new AbortController();download.current=abort;setExporting(true);setFeedback('');
        try{
            const query={...filter,runtimeInstanceId:cursor.current.instance,...(cursor.current.upper!==undefined?{upperEventId:cursor.current.upper}:{})};
            const blob=await apiBlob(`/api/runtimes/${encodeURIComponent(runtimeId)}/logs/export?${queryString(query)}`,{signal:abort.signal});
            if(abort.signal.aborted)return;
            const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='runtime-logs.jsonl';link.click();URL.revokeObjectURL(url);
            setFeedback('JSONL 已下载，每行一条日志；范围为本次固定筛选和上界。');
        }catch(e){
            if(!abort.signal.aborted){
                const failure=e as ApiError;
                setError((failure.code?failure.code+' · ':'')+failure.message+(failure.runtimeInstanceId?' · 当前实例 '+failure.runtimeInstanceId:''));
                if(failure.code==='RESET_REQUIRED')resetInstance(failure.runtimeInstanceId)
                if(failure.code==='UNIMPLEMENTED')unsupportedRuntimes.set(runtimeId,runtimeInstanceId);
            }
        }finally{if(download.current===abort)setExporting(false)}
    }
    return <section aria-label="Runtime 日志"><h3>Runtime 运行日志</h3><p>查询 Runtime 持久化记录，可查看旧工程；设备收发明细另属设备缓存或本机文件。</p>
        <div className="toolbar"><label>范围<select aria-label="范围" value={mode} onChange={e=>setMode(e.target.value as 'live'|'history')}><option value="live">实时（接入后新增）</option><option value="history">历史</option></select></label>
        {(['projectId','jobId','deviceId','component','levels','workflowId','nodeId','startTimestampMs','endTimestampMs'] as const).map(name=><label key={name}>{({projectId:'工程',jobId:'Job',deviceId:'设备',component:'模块',levels:'级别（逗号分隔）',workflowId:'工作流',nodeId:'节点',startTimestampMs:'起始时间 ms（含）',endTimestampMs:'结束时间 ms（不含）'})[name]}<input value={draft[name]||''} onChange={e=>setDraft({...draft,[name]:e.target.value})}/></label>)}
        <button onClick={()=>{setFilter({...draft});setRevision(v=>v+1)}}>应用筛选</button><button onClick={()=>setRevision(v=>v+1)}>重新查询</button></div>
        {error&&<p role="alert">日志查询失败：{error}；已显示记录和已消费游标保留。</p>}
        {mode==='live'&&<p>实时显示保留最近 {RUNTIME_LIVE_LOG_LIMIT} 条；完整记录可按历史范围查询或导出。</p>}
        <p>已显示 {events.length} 条 · 当前连接实例 {cursor.current.instance||'等待确认'} · 全局游标 {cursor.current.after} · 历史上界 {cursor.current.upper??'按轮次确定'}</p>
        <button disabled={!runtimeId||exporting} onClick={()=>void exportHistory()}>导出筛选历史 JSONL</button>{exporting&&<button onClick={()=>{download.current?.abort();setExporting(false);setFeedback('导出已取消')}}>取消导出</button>}{feedback&&<p role="status">{feedback}</p>}
        <div className="table-scroll"><table><thead><tr><th>ID / 时间</th><th>应用 / 模块</th><th>关联</th><th>级别 / 内容</th></tr></thead><tbody>{events.map(event=><tr key={event.eventId} onClick={()=>setDetail(event)}><td>{event.eventId} / {new Date(Number(event.timestampMs)).toLocaleString()}</td><td>{event.originApp} / {event.component}</td><td>{event.projectId} / {event.jobId} / {event.nodeId}</td><td>{event.level} · {event.code} · {event.message}<button onClick={()=>setDetail(event)}>详情</button></td></tr>)}</tbody></table></div>
        {mode==='history'&&hasMore&&<button disabled={busy} onClick={()=>setPageRequest(v=>v+1)}>下一页</button>}{busy&&<p>正在读取日志…</p>}
        {detail&&<details open><summary>日志详情（Job sequence {detail.sequence}）</summary><button onClick={()=>setDetail(undefined)}>关闭详情</button><pre>{JSON.stringify(detail,null,2)}</pre></details>}
    </section>;
}
