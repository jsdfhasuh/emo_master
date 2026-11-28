import { useEffect, useRef, useState } from 'react';
import { api, apiBlob, ApiError, getSessionGeneration, onSessionExpired } from '../data/api';
import { localLogTimestamp, logFields, logText, runtimeLogMessage, runtimeLogTime } from '../data/runtimeLogDisplay';
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

const filterLabels = {
    projectId: '工程', jobId: 'Job', deviceId: '设备', component: '模块',
    levels: '级别（逗号分隔）', workflowId: '工作流', nodeId: '节点',
};
type LogFilter = keyof typeof filterLabels;
type LogDraft = Pick<RuntimeLogQuery, LogFilter> & {startLocal?: string; endLocal?: string};

function RuntimeLogLevel({level}: {level: string}) {
    return <span className="runtime-log-level" data-level={level}>{level}</span>;
}

function runtimeLogNames(event: RuntimeLogEvent) {
    const payload = logFields(event.payload);
    return {
        project: logText(payload.projectName) || event.projectId,
        workflow: logText(payload.workflowName) || event.workflowId,
        node: logText(payload.nodeName) || event.nodeId,
    };
}

function RuntimeLogDetail({event, onClose}: {event: RuntimeLogEvent; onClose: () => void}) {
    const [raw, setRaw] = useState(false);
    const names = runtimeLogNames(event);
    const payload = logFields(event.payload);
    const receipt = logFields(payload.receipt);
    const artifact = logFields(payload.artifact);
    const summary = logFields(payload.ioSummary);
    const iterations: unknown[] = Array.isArray(event.iterationPath) ? event.iterationPath : [];
    const fields: [string, unknown][] = [
        ['事件 ID', event.eventId],
        ['时间', runtimeLogTime(event.timestampMs)],
        ['记录代码', event.code || payload.code],
        ['事件类型', event.eventType],
        ['应用 / 模块', [event.originApp, event.component].filter(Boolean).join(' / ')],
        ['Job', event.jobId],
        ['工程', names.project],
        ['工作流', names.workflow],
        ['节点', names.node],
        ['轮次', iterations.filter(value => typeof value === 'number' && Number.isInteger(value) && value >= 0)
            .map(value => Number(value) + 1).join(' / ')],
        ['子工作流', logText(payload.targetWorkflowName) || payload.targetWorkflowId],
        ['设备', logText(payload.deviceName) || payload.deviceId],
        ['动作', payload.action],
        ['状态', payload.status],
        ['原因', logText(payload.reason) || logText(logFields(payload.error).message)
            || (event.eventType === 'node.skipped' ? payload.message : undefined)],
        ['结果摘要', payload.summary],
        ['回执状态', receipt.status],
        ['影响行数', receipt.rowsAffected],
        ['回执说明', logFields(receipt.error).message],
        ['产物', logText(artifact.path) || artifact.artifactId],
        ['输入项数', logFields(summary.inputs).portCount],
        ['输出项数', logFields(summary.outputs).portCount],
    ];
    return <details className="runtime-log-detail" open>
        <summary>日志详情（序号 {event.sequence}）</summary>
        <section aria-label="日志详情">
            <button onClick={() => setRaw(value => !value)}>{raw ? '查看易读详情' : '查看原始记录'}</button>
            <button onClick={onClose}>关闭详情</button>
            {raw ? <pre>{JSON.stringify(event, null, 2)}</pre> : <>
                <p><RuntimeLogLevel level={event.level} /> {runtimeLogMessage(event)}</p>
                <dl>{fields.filter(([, value]) => logText(value) !== '').map(([label, value]) =>
                    <div key={label}><dt>{label}</dt><dd>{logText(value)}</dd></div>)}</dl>
            </>}
        </section>
    </details>;
}

export function RuntimeLogs({runtimeId,runtimeInstanceId=''}:{runtimeId:string;runtimeInstanceId?:string}) {
    if(logSessionGeneration!==getSessionGeneration()){liveCursors.clear();unsupportedRuntimes.clear();logSessionGeneration=getSessionGeneration()}
    const [mode,setMode]=useState<'live'|'history'>('live');
    const [draft,setDraft]=useState<LogDraft>({}),[filter,setFilter]=useState<RuntimeLogQuery>({});
    const [events,setEvents]=useState<RuntimeLogEvent[]>([]),[queryError,setQueryError]=useState(''),[busy,setBusy]=useState(false);
    const [rangeError,setRangeError]=useState('');
    const [hasMore,setHasMore]=useState(false),[revision,setRevision]=useState(0),[pageRequest,setPageRequest]=useState(0);
    const [detail,setDetail]=useState<RuntimeLogEvent>(),[exporting,setExporting]=useState(false),[feedback,setFeedback]=useState('');
    const [exportError,setExportError]=useState('');
    const cursor=useRef<Cursor>(fresh()), download=useRef<AbortController | undefined>(undefined), readAbort=useRef<AbortController | undefined>(undefined);
    const filterKey=JSON.stringify(filter),key=runtimeId+'|'+filterKey;
    useEffect(()=>{setExporting(false);setExportError('');return()=>download.current?.abort()},[key]);
    useEffect(()=>{cursor.current=mode==='live'?(liveCursors.get(key)||fresh()):fresh();setEvents(cursor.current.events);setQueryError('');setHasMore(false);setFeedback('');setDetail(undefined);},[key,mode,revision]);
    function resetInstance(instance:string){
        const next=fresh();next.instance=instance;cursor.current=next;
        liveCursors.delete(key);
        if(mode==='live')liveCursors.set(key,next);
        readAbort.current?.abort();download.current?.abort();
        setEvents([]);setDetail(undefined);setHasMore(false);setQueryError('');setExportError('');setFeedback('');setExporting(false);
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
        if(unsupportedRuntimes.get(runtimeId)===runtimeInstanceId){setQueryError('UNIMPLEMENTED · 此 Runtime 不支持历史日志查询');return;}
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
                    setEvents(current.events);setHasMore(page.hasMore);setQueryError('');
                    if(mode==='history'||!page.hasMore)break;
                } while(alive);
                if(mode==='live')current.upper=undefined;
            } catch(e) {
                if(!alive||cursor.current!==current)return;
                const failure=e as ApiError;
                if(failure.code==='RESET_REQUIRED'){
                    resetInstance(failure.runtimeInstanceId);retry=false;
                } else {
                    setQueryError((failure.code?failure.code+' · ':'')+failure.message);
                    if(failure.code==='UNIMPLEMENTED')unsupportedRuntimes.set(runtimeId,runtimeInstanceId);
                    if(['UNIMPLEMENTED','EVENT_TOO_LARGE','INVALID_ARGUMENT'].includes(failure.code))retry=false;
                }
            } finally {if(alive&&cursor.current===current){setBusy(false);if(mode==='live'&&retry)timer=setTimeout(read,3000)}}
        };
        void read();return()=>{alive=false;abort.abort();clearTimeout(timer)};
    },[key,mode,revision,pageRequest]);
    async function exportHistory(){
        download.current?.abort();const abort=new AbortController();download.current=abort;setExporting(true);setExportError('');setFeedback('');
        try{
            const query={...filter,runtimeInstanceId:cursor.current.instance,...(cursor.current.upper!==undefined?{upperEventId:cursor.current.upper}:{})};
            const blob=await apiBlob(`/api/runtimes/${encodeURIComponent(runtimeId)}/logs/export?${queryString(query)}`,{signal:abort.signal});
            if(abort.signal.aborted)return;
            const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='runtime-logs.jsonl';link.click();URL.revokeObjectURL(url);
            setFeedback('JSONL 已下载，每行一条日志；范围为本次固定筛选和上界。');
        }catch(e){
            if(!abort.signal.aborted){
                const failure=e as ApiError;
                if(failure.code==='RESET_REQUIRED')resetInstance(failure.runtimeInstanceId)
                setExportError((failure.code?failure.code+' · ':'')+failure.message+(failure.runtimeInstanceId?' · 当前实例 '+failure.runtimeInstanceId:''));
                if(failure.code==='UNIMPLEMENTED')unsupportedRuntimes.set(runtimeId,runtimeInstanceId);
            }
        }finally{if(download.current===abort)setExporting(false)}
    }
    function applyFilters() {
        const {startLocal = '', endLocal = '', ...query} = draft;
        try {
            const start = localLogTimestamp(startLocal);
            const end = localLogTimestamp(endLocal);
            if (start !== undefined && end !== undefined && start > end) {
                throw new RangeError('开始时间不能晚于结束时间');
            }
            setRangeError('');
            setFilter({...query, startTimestampMs: start?.toString(), endTimestampMs: end?.toString()});
            setRevision(value => value + 1);
        } catch (error) {
            setRangeError(error instanceof RangeError ? error.message : '请输入有效的本地日期时间');
        }
    }
    return <section aria-label="Runtime 日志"><h3>Runtime 运行日志</h3><p>查看 Runtime 持久化日志，包括以前加载的工程。</p>
        <form className="toolbar" onSubmit={event => {event.preventDefault(); applyFilters();}}>
            <label>范围<select aria-label="范围" value={mode} onChange={e=>setMode(e.target.value as 'live'|'history')}><option value="live">实时（接入后新增）</option><option value="history">历史</option></select></label>
            {(Object.keys(filterLabels) as LogFilter[]).map(name =>
                <label key={name}>{filterLabels[name]}<input value={draft[name] ?? ''} onChange={event =>
                    setDraft({...draft, [name]: event.target.value})}/></label>)}
            <label>本地开始时间（含）<input type="datetime-local" step="0.001" value={draft.startLocal ?? ''}
                onChange={event => setDraft({...draft, startLocal: event.target.value})}/></label>
            <label>本地结束时间（不含）<input type="datetime-local" step="0.001" value={draft.endLocal ?? ''}
                onChange={event => setDraft({...draft, endLocal: event.target.value})}/></label>
            <button type="submit">应用筛选</button><button type="button" onClick={()=>setRevision(v=>v+1)}>重新查询</button>
        </form>
        {rangeError&&<p role="alert">{rangeError}。当前查询结果已保留。</p>}
        {queryError&&<p role="alert">日志查询失败：{queryError}；已显示记录和已消费游标保留。</p>}
        {exportError&&<p role="alert">日志导出失败：{exportError}</p>}
        {mode==='live'&&<p>实时显示保留最近 {RUNTIME_LIVE_LOG_LIMIT} 条；完整记录可按历史范围查询或导出。</p>}
        <p>已显示 {events.length} 条 · 当前连接实例 {cursor.current.instance||'等待确认'} · 全局游标 {cursor.current.after} · 历史上界 {cursor.current.upper??'按轮次确定'}</p>
        <button disabled={!runtimeId||exporting} onClick={()=>void exportHistory()}>导出筛选历史 JSONL</button>{exporting&&<button onClick={()=>{download.current?.abort();setExporting(false);setFeedback('导出已取消')}}>取消导出</button>}{feedback&&<p role="status">{feedback}</p>}
        <div className="table-scroll"><table><thead><tr><th>ID / 时间</th><th>应用 / 模块</th><th>关联</th><th>级别 / 内容</th></tr></thead><tbody>{events.map(event => {
            const names = runtimeLogNames(event);
            return <tr key={event.eventId} onClick={() => setDetail(event)}>
                <td>{event.eventId} / {runtimeLogTime(event.timestampMs)}</td>
                <td>{[event.originApp, event.component].filter(Boolean).join(' / ')}</td>
                <td>{[names.project, event.jobId, names.workflow, names.node].filter(Boolean).join(' / ')}</td>
                <td><RuntimeLogLevel level={event.level} /> {event.code} {runtimeLogMessage(event)} <button>详情</button></td>
            </tr>;
        })}</tbody></table></div>
        {mode==='history'&&hasMore&&<button disabled={busy} onClick={()=>setPageRequest(v=>v+1)}>下一页</button>}{busy&&<p>正在读取日志…</p>}
        {detail&&<RuntimeLogDetail key={detail.eventId} event={detail} onClose={() => setDetail(undefined)}/>}
    </section>;
}
