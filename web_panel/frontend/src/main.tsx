import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Me, Panel, Run, Result, Envelope, RuntimeTarget, WorkflowTarget } from './contracts/types';
import { api, setCsrf } from './data/api';
import { parseLosslessJSON } from './data/lossless';
import { normalizeMessage, applySourceProblems, isInitialJobConfirmation } from './data/contract';
import { mergeScopeStatus, isScopeResultExpired, scopeActivity } from './state/scopes';
import type { ScopeStatuses } from './state/scopes';
import { reconcileCatalogSelection, selectedViewState, targetContext } from './state/catalog';
import type { RuntimeInfo, CatalogSelection } from './state/catalog';
import { commandResolution } from './state/commands';
import type { CommandReply } from './state/commands';
import { LatestScopeQueue, resumeExpiredScopes } from './state/latest';
import { prepareResult, releaseResult, pageImageSources } from './state/prepare';
import { PanelRenderer } from './renderer/PanelRenderer';
import './style.css';
import { WorkflowInputs } from './components/WorkflowInputs';
import { RuntimeStatus } from './components/RuntimeStatus';
import { Admin } from './components/Admin';
let bootstrapSession: Promise<Me> | undefined;
function loadSession() {
    if (bootstrapSession) return bootstrapSession;
    const url=new URL(location.href);const token=url.searchParams.get('token');
    if(token){url.searchParams.delete('token');history.replaceState(null,'',url.pathname+url.search+url.hash)}
    bootstrapSession=token?api<Me>('/api/preview/session',{method:'POST',body:JSON.stringify({token})}):api<Me>('/api/me');
    return bootstrapSession;
}
function App() {
    const [me, setMe] = useState<Me>();
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [panels, setPanels] = useState<Panel[]>([]);
    const [panel, setPanel] = useState<Panel>();
    const [page, setPage] = useState('');
    const pageRef = useRef('');
    const viewGeneration = useRef('0');
    function acceptViewGeneration(value?: string) {
        if (value === undefined) return true;
        if (!/^\d{1,20}$/.test(value)) return false;
        const next = BigInt(value), current = BigInt(viewGeneration.current);
        if (next < current) return false;
        if (next > current) { viewGeneration.current = value; invalidateCandidates.current?.(); }
        return true;
    }
    const frozenKeys = useRef<Record<string, string> | undefined>(undefined);
    const invalidateCandidates = useRef<(() => void) | undefined>(undefined);
    const restagePage = useRef<(() => void) | undefined>(undefined);
    const retireExpiredLive = useRef<(() => void) | undefined>(undefined);
    function changePage(id: string) { pageRef.current = id; setPage(id); restagePage.current?.(); }
    const [runs, setRuns] = useState<Run[]>([]);
    const [runId, setRunId] = useState('');
    const [controlRunId, setControlRunId] = useState('');
    const [runtimeId, setRuntimeId] = useState('');
    const [runtimes, setRuntimes] = useState<RuntimeTarget[]>([]);
    const [workflows, setWorkflows] = useState<WorkflowTarget[]>([]);
    const [workflowId, setWorkflowId] = useState('');
    const [workflowInputs, setWorkflowInputs] = useState('{}');
    const [runtimeInfo,setRuntimeInfo]=useState<RuntimeInfo>();
    const [catalogBusy,setCatalogBusy]=useState(false);
    const [viewRefresh,setViewRefresh]=useState(0);
    const connectionNeedsRefresh=useRef(false);
    const activeRuntime=useRef('');const catalogGeneration=useRef(0);const catalogAbort=useRef<AbortController | undefined>(undefined);
    const infoRef=useRef<RuntimeInfo | undefined>(undefined);const inputDirty=useRef(false);
    const selectionRef=useRef<CatalogSelection>({runId:'',controlRunId:'',workflowId:'',workflowInputs:'{}',dirty:false});
    selectionRef.current={runId,controlRunId,workflowId,workflowInputs,dirty:inputDirty.current};
    const panelRef=useRef<Panel | undefined>(undefined);panelRef.current=panel;
    const workflowRef=useRef<WorkflowTarget[]>([]);workflowRef.current=workflows;
    const refreshRef=useRef<((options?:{reconnect?:boolean;quiet?:boolean})=>Promise<void>) | undefined>(undefined);
    const [viewId, setViewId] = useState('');
    const sourceProblems = useRef<Record<string,string>>({});
    const viewRunIds = useRef<string[]>([]);
    const actionAttempt = useRef(0);
    const expiredScopes=useRef(new Set<string>());
    useEffect(() => { if (!viewId) return; return () => { void api(`/api/views/${encodeURIComponent(viewId)}`, { method: 'DELETE' }).catch(() => {}); }; }, [viewId]);
    const [result, setResult] = useState<Result>();
    const [scopes, setScopes] = useState<Record<string, Result>>({});
    const [scopeStatuses,setScopeStatuses]=useState<ScopeStatuses>({});
    const ownedResults = useRef(new Set<Result>());
    useEffect(() => {
        const visible = new Set(Object.values(scopes));
        for (const old of ownedResults.current) if (!visible.has(old)) { releaseResult(old); ownedResults.current.delete(old); }
    }, [scopes]);
    useEffect(() => () => { ownedResults.current.forEach(releaseResult); ownedResults.current.clear(); }, []);
    const [status, setStatus] = useState('尚未连接');
    const [runtimeState,setRuntimeState] = useState('');
    const [frozen, setFrozen] = useState(false);
    const [lastUpdate, setLastUpdate] = useState('');
    const [packages, setPackages] = useState<{
        id?: string;
        packageId?: string;
        name?: string;
        active?: boolean;
        runtimeIds?: string[];
        manifest?: {projectId:string;packageRevision:string};
    }[]>([]);
    const [notice, setNotice] = useState('');
    const generation = useRef(0);
    const [pending,setPending]=useState<{id:string;kind:'start'|'stop';target:string;runtimeId?:string;runtimeInstanceId?:string;projectId?:string;state?:'submitting'|'unknown'}|undefined>();
    const [lastCommand,setLastCommand]=useState<{kind:'start'|'stop';runtimeId:string;runtimeInstanceId?:string;projectId?:string;runId?:string;reply:CommandReply}>();
    const controlId = useRef<string | undefined>(undefined);
    useEffect(()=>{if(!me){setPending(undefined);return}try{const saved=sessionStorage.getItem(`emo-pending:${me.userId || me.username}`);setPending(saved?{...JSON.parse(saved),state:'unknown'}:undefined)}catch{setPending(undefined)}},[me]);
    function clearPending(){if(me)sessionStorage.removeItem(`emo-pending:${me.userId || me.username}`);setPending(undefined);controlId.current=undefined;}
    function clearIdentityView() {
        generation.current++;actionAttempt.current++;invalidateCandidates.current?.();
        ownedResults.current.forEach(releaseResult);ownedResults.current.clear();
        setViewId('');setResult(undefined);setScopes({});setScopeStatuses({});setFrozen(false);setRuntimeState('');setLastCommand(undefined);
    }
    function selectTarget(id:string) {
        catalogGeneration.current++;catalogAbort.current?.abort();connectionNeedsRefresh.current=false;activeRuntime.current=id;infoRef.current=undefined;setRuntimeInfo(undefined);
        clearIdentityView();setRunId('');setControlRunId('');setWorkflowId('');setRuns([]);setWorkflows([]);setPanel(undefined);setPanels([]);setPackages([]);
        inputDirty.current=false;setWorkflowInputs('{}');selectionRef.current={runId:'',controlRunId:'',workflowId:'',workflowInputs:'{}',dirty:false};
        setRuntimeId(id);
    }
    async function refresh(options:{reconnect?:boolean;quiet?:boolean}={}) {
        const ticket=++catalogGeneration.current;catalogAbort.current?.abort();const abort=new AbortController();catalogAbort.current=abort;
        const selected=activeRuntime.current;setCatalogBusy(true);
        const current=()=>ticket===catalogGeneration.current && !abort.signal.aborted;
        try {
            const targets=await api<RuntimeTarget[]>('/api/runtimes',{signal:abort.signal});if(!current())return;
            setRuntimes(targets);const rid=targets.some(target=>target.id===selected)?selected:targets[0]?.id || '';
            if(!rid){activeRuntime.current='';setRuntimeId('');infoRef.current=undefined;setRuntimeInfo(undefined);clearIdentityView();setRuns([]);setWorkflows([]);setRunId('');setControlRunId('');setWorkflowId('');setPanels([]);setPanel(undefined);setPackages([]);return;}
            if(rid!==selected){activeRuntime.current=rid;setRuntimeId(rid);infoRef.current=undefined;clearIdentityView();selectionRef.current={...selectionRef.current,runId:'',controlRunId:'',workflowId:''};setRunId('');setControlRunId('');setWorkflowId('');}
            const oldInfo=infoRef.current;
            const info=await api<RuntimeInfo>(`/api/runtimes/${encodeURIComponent(rid)}/info`,{signal:abort.signal});if(!current() || activeRuntime.current!==rid)return;
            if(!info.runtimeInstanceId || !info.projectId)throw Error('Runtime 未提供正式实例和工程身份');
            const changed=Boolean(oldInfo && (oldInfo.runtimeInstanceId!==info.runtimeInstanceId || oldInfo.projectId!==info.projectId));
            if(changed){clearIdentityView();setRuns([]);setWorkflows([]);setRunId('');setControlRunId('');setWorkflowId('');setPanel(undefined);setPanels([]);setNotice('Runtime 实例或工程已改变；旧查看和控制选择已清除。');}
            const context=targetContext(rid,info.projectId);
            const [rs,ws,ps,pk]=await Promise.all([
                api<Run[]>(`/api/runtimes/${encodeURIComponent(rid)}/runs`,{signal:abort.signal}),
                api<WorkflowTarget[]>(`/api/runtimes/${encodeURIComponent(rid)}/workflows`,{signal:abort.signal}),
                api<Panel[]>(`/api/panels?${context}`,{signal:abort.signal}),
                me?.role==='admin'?api<typeof packages>(`/api/packages?${context}`,{signal:abort.signal}):Promise.resolve([]),
            ]);if(!current() || activeRuntime.current!==rid)return;
            if(rs.some(run=>run.runtimeInstanceId!==info.runtimeInstanceId || run.projectId!==info.projectId) || ws.some(workflow=>workflow.runtimeInstanceId!==info.runtimeInstanceId || workflow.projectId!==info.projectId || workflow.configurationRevision!==info.configurationRevision))throw Error('目录读取期间 Runtime 身份或配置发生变化，请再次刷新。');
            const previous=selectionRef.current;
            const reconciled=reconcileCatalogSelection(previous,oldInfo,info,rs,ws,!oldInfo && targets.find(target=>target.id===rid)?.mode==='mock');
            const chosen=(!changed && ps.find(item=>item.id===panelRef.current?.id)) || ps[0];
            const nextPanel=chosen?await api<Panel>(`/api/panels/${encodeURIComponent(chosen.id)}?${context}`,{signal:abort.signal}):undefined;
            if(!current() || activeRuntime.current!==rid)return;
            const oldWorkflow=workflowRef.current.find(workflow=>workflow.id===previous.workflowId),newWorkflow=ws.find(workflow=>workflow.id===previous.workflowId);
            if(oldWorkflow && newWorkflow && JSON.stringify([oldWorkflow.inputSchema,oldWorkflow.inputDeclarations,oldWorkflow.inputsJson])!==JSON.stringify([newWorkflow.inputSchema,newWorkflow.inputDeclarations,newWorkflow.inputsJson]))reconciled.notices.push('工作流输入描述或默认值已更新；保留当前输入，请核对后再启动。');
            // Preserve edits made while the request was in flight.
            const next={...reconciled.selection,workflowInputs:inputDirty.current?selectionRef.current.workflowInputs:reconciled.selection.workflowInputs};
            infoRef.current=info;setRuntimeInfo(info);setLastCommand(old=>old && old.runtimeId===rid && (!old.runtimeInstanceId || old.runtimeInstanceId===info.runtimeInstanceId) && (!old.projectId || old.projectId===info.projectId)?{...old,reply:{...old.reply,run:rs.find(run=>run.id===old.runId) || old.reply.run}}:undefined);setRuns(rs);setWorkflows(ws);setPanels(ps);setPackages(pk);
            setRuntimeState(selectedViewState(next.runId,info,rs));
            selectionRef.current=next;setRunId(next.runId);setControlRunId(next.controlRunId);setWorkflowId(next.workflowId);setWorkflowInputs(next.workflowInputs);
            setPanel(old=>old?.id===nextPanel?.id && old?.packageRevision===nextPanel?.packageRevision && !changed?old:nextPanel);
            if(!ps.length)reconciled.notices.push('当前 Runtime／工程暂无可访问的启用页面包。');
            if(reconciled.notices.length)setNotice(reconciled.notices.join(' '));
            if(options.reconnect || connectionNeedsRefresh.current){connectionNeedsRefresh.current=false;setViewRefresh(value=>value+1);}
        } catch(e) {if(current()){setError(`目录刷新失败：${(e as Error).message}`);throw e;}}
        finally{if(current())setCatalogBusy(false)}
    }
    refreshRef.current=refresh;
    async function resolveCommand(kind:'start'|'stop',reply:CommandReply,intent:{runtimeId:string;runtimeInstanceId?:string;projectId?:string},requestId:string) {
        const resolution=commandResolution(kind,reply);
        // The original intent can settle while another target is displayed, but it
        // must not change that target's notice, identity or selected control Job.
        if(resolution.confirmed)clearPending();else setPending(old=>old?.id===requestId?{...old,state:'unknown'}:old);
        if(activeRuntime.current!==intent.runtimeId || intent.runtimeInstanceId && infoRef.current?.runtimeInstanceId!==intent.runtimeInstanceId || intent.projectId && infoRef.current?.projectId!==intent.projectId)return;
        setNotice(resolution.message);if(resolution.rejected)setError(resolution.message);
        setLastCommand({kind,...intent,runId:resolution.runId,reply});
        try{await refreshRef.current?.({quiet:true});}catch{/* A confirmed command stays confirmed even if catalog refresh fails. */}
    }
    async function queryPending(){
        if(!pending || busy)return;setBusy(true);const original=pending;
        try{const r=await api<CommandReply>(`/api/operations/by-request/${encodeURIComponent(original.id)}`);await resolveCommand(original.kind,r,{runtimeId:original.runtimeId || original.target.split('/')[0],runtimeInstanceId:original.runtimeInstanceId,projectId:original.projectId},original.id);}
        catch(e){if(activeRuntime.current===(original.runtimeId || original.target.split('/')[0]))setError(`查询未确认：${(e as Error).message}；未重新发送命令。`);setPending(old=>old?.id===original.id?{...old,state:'unknown'}:old)}finally{setBusy(false)}
    }
    useEffect(()=>{if(me && activeRuntime.current)void refreshRef.current?.().catch(()=>{});},[runtimeId]);
    useEffect(() => {
        loadSession().then(m=>{setCsrf(m.csrf);setMe(m)}).catch(()=>{});
    }, []);
    useEffect(() => { if (me)
        refresh().catch(()=>{}); }, [me]);
    useEffect(() => { pageRef.current = panel?.defaultPageId || panel?.pages[0]?.id || ''; setPage(pageRef.current); setResult(undefined); setScopes({}); }, [panel]);
    useEffect(() => { if (!me || !panel || !runtimeId || !runtimeInfo) {
        setViewId('');
        return;
    } let valid = true; sourceProblems.current={}; viewRunIds.current=[]; setViewId(''); setResult(undefined); setScopes({}); setFrozen(false); setRuntimeState(''); api<{
        id: string; sourceProblems?:Record<string,string>; runIds?:string[];
    }>('/api/views', { method: 'POST', body: JSON.stringify({ runtimeId, projectId:runtimeInfo?.projectId, panelId: panel.id, runId: runId || undefined }) }).then(v => { if (valid) {sourceProblems.current=v.sourceProblems || {};viewRunIds.current=v.runIds || (runId?[runId]:[]);setViewId(v.id);}  else void api(`/api/views/${encodeURIComponent(v.id)}`, { method: 'DELETE' }).catch(() => {}); }).catch(e => { if (valid)
        setError(e.message); }); return () => { valid = false; }; }, [me, panel, runtimeId, runId, runtimeInfo?.runtimeInstanceId, runtimeInfo?.projectId,viewRefresh]);
    useEffect(() => {
        setScopeStatuses({});
        if (!viewId || !runId) { setStatus('历史 / 页面预览 · 未订阅运行'); return; }
        viewGeneration.current = '0';
        let closed = false;
        let socket: WebSocket;
        let retry: ReturnType<typeof setTimeout>;
        let abort = new AbortController();
        const gen = ++generation.current;
        const candidates: Record<string, number> = {};
        const aborts: Record<string, AbortController> = {};
        let connection = 0;
        let epoch: string | undefined;
        const rawResults: Record<string, Result> = {};
        const displayedRaw: Record<string, Result> = {};
        let ordinalStatus:ScopeStatuses={};
        const stage = async (raw: Result, connectionId: number) => {
            const scope = raw.resultScopeId || 'default';
            if(isScopeResultExpired(raw,ordinalStatus,frozenKeys.current?.[scope]))return false;
            if(expiredScopes.current.has(scope)){rawResults[scope]=raw;return false;}
            if (frozenKeys.current?.[scope] && frozenKeys.current[scope] !== raw.resultKey) return false;
            rawResults[scope] = raw;
            const id = (candidates[scope] || 0) + 1; candidates[scope] = id;
            aborts[scope]?.abort(); aborts[scope] = new AbortController();
            const visiblePage = panel?.pages.find(p => p.id === pageRef.current) || panel?.pages[0];
            const ready = await prepareResult(raw, aborts[scope].signal, pageImageSources(visiblePage, scope));
            if (!closed && connectionId === connection && gen === generation.current && id === candidates[scope] && (!frozenKeys.current?.[scope] || frozenKeys.current[scope] === ready.resultKey)) {
                displayedRaw[scope] = raw; ownedResults.current.add(ready); setScopes(old => ({ ...old, [scope]: ready })); setResult(ready); setStatus('已连接'); setLastUpdate(new Date().toLocaleTimeString()); return true;
            } else releaseResult(ready);
            return false;
        };
        const queue = new LatestScopeQueue<{raw:Result;connectionId:number}>(item => stage(item.raw,item.connectionId).catch(() => { if (!closed) setError('结果准备失败，未更新画面'); return false; }));
        const enqueue = (raw:Result,connectionId:number) => queue.push(raw.resultScopeId || 'default', {raw,connectionId}, JSON.stringify([pageRef.current,raw.resultKey,raw.sources]));
        invalidateCandidates.current = () => {
            queue.invalidate();
            if (frozenKeys.current) for(const scope of Object.keys(frozenKeys.current)) if(displayedRaw[scope])rawResults[scope]=displayedRaw[scope];
            Object.keys(candidates).forEach(scope => candidates[scope]++);
            Object.values(aborts).forEach(a => a.abort());
        };
        restagePage.current = () => {
            invalidateCandidates.current?.();
            setResult(undefined); setScopes({});
            Object.values(rawResults).forEach(raw => { enqueue(raw, connection); });
        };
        retireExpiredLive.current = () => {
                const expired=(value:Result)=>isScopeResultExpired(value,ordinalStatus,frozenKeys.current?.[value.resultScopeId || 'default']);
                for(const [scope,value] of Object.entries(rawResults))if(expired(value)){queue.invalidateScope(scope);candidates[scope]=(candidates[scope] || 0)+1;aborts[scope]?.abort();delete rawResults[scope];}
                for(const [scope,value] of Object.entries(displayedRaw))if(expired(value))delete displayedRaw[scope];
                for(const value of ownedResults.current)if(expired(value)){releaseResult(value);ownedResults.current.delete(value)}
                setScopes(old=>Object.fromEntries(Object.entries(old).filter(([,value])=>!expired(value))));setResult(old=>old && !expired(old)?old:undefined);
        };
        const connect = () => { const connectionId = ++connection; setStatus('正在同步'); socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/views/${encodeURIComponent(viewId)}`); socket.onopen = () => {connectionNeedsRefresh.current=false;setStatus('连接已建立，等待快照')}; socket.onmessage = async (event) => { try {
            const message = normalizeMessage(parseLosslessJSON(event.data),viewId,runId,runs.find(r=>r.id===runId)?.runtimeInstanceId,viewRunIds.current);
            if (closed || gen !== generation.current || connectionId !== connection) return;
            message.type ||= message.event || '';
            if(message.type==='scope_status' && message.jobId){
                ordinalStatus=mergeScopeStatus(ordinalStatus,message.jobId,message.latestStartedOrdinals || {},message.expiredScopeOrdinals || {});setScopeStatuses(ordinalStatus);
                retireExpiredLive.current?.();return;
            }
            if (message.type === 'capture_unavailable') {invalidateCandidates.current?.();Object.keys(rawResults).forEach(scope=>{if(!message.jobId || rawResults[scope].jobId===message.jobId)delete rawResults[scope]});setResult(old=>old?.jobId===message.jobId?undefined:old);setScopes(old=>Object.fromEntries(Object.entries(old).filter(([,value])=>message.jobId && value.jobId!==message.jobId)));setNotice(message.reason || '当前运行未启用页面采集，不会自动重启');return;}
            if (message.type === 'error') {setError(message.message || 'Runtime 读取失败');return;}
            if (message.type === 'control_result') {setNotice(`${message.outcome || 'UNKNOWN'}：${message.message || ''}`);return;}
            if (message.type === 'job_state') {if(message.jobId===runId)setRuntimeState(message.status || 'unknown');return;}
            if (message.type === 'job_changed') {if(isInitialJobConfirmation(message,runId,infoRef.current?.runtimeInstanceId,infoRef.current?.projectId))return;invalidateCandidates.current?.();Object.keys(rawResults).forEach(scope=>delete rawResults[scope]);setResult(undefined);setScopes({});setNotice('查看运行已变化，请重新选择运行');return;}
            if (closed || gen !== generation.current || connectionId !== connection) return;
            if (!acceptViewGeneration(message.viewGeneration)) return;
            if (message.pagePackageRevision && message.pagePackageRevision !== panel?.packageRevision) {
                setNotice('页面包版本不匹配，请刷新页面包');
                return;
            }
            if (message.sessionEpoch && epoch && message.sessionEpoch !== epoch) {
                queue.invalidate();
                Object.keys(rawResults).forEach(scope => delete rawResults[scope]);
                Object.keys(candidates).forEach(scope => candidates[scope]++);
                Object.values(aborts).forEach(a => a.abort());
                setResult(undefined);
                setScopes({});
            }
            if (message.sessionEpoch)
                epoch = message.sessionEpoch;
            if (message.type === 'freeze_expired') {
                const expired=(message as Envelope & {frozenScopes?:string[]}).frozenScopes || Object.keys(frozenKeys.current || {});
                invalidateCandidates.current?.();
                for(const scope of expired){expiredScopes.current.add(scope);delete rawResults[scope];delete displayedRaw[scope];}
                for(const owned of ownedResults.current)if(expiredScopes.current.has(owned.resultScopeId || 'default')){releaseResult(owned);ownedResults.current.delete(owned)}
                setScopes(old=>Object.fromEntries(Object.entries(old).filter(([scope])=>!expiredScopes.current.has(scope))));
                setResult(old=>old && !expiredScopes.current.has(old.resultScopeId || 'default')?old:undefined);
                setFrozen(true);setNotice('冻结结果已过期，请明确恢复实时；生产未停止。');return;
            }
            if (message.type === 'package_updated') {
                setNotice('页面包已有更新，请点击刷新页面包');
                return;
            }
            if (message.type === 'reset_required') {
                queue.invalidate();
                Object.keys(rawResults).forEach(scope => delete rawResults[scope]);
                connection++;
                Object.values(aborts).forEach(a => a.abort());
                setResult(undefined);
                setScopes({});
                setStatus('目标变化，重新同步');
                socket.close();
                return;
            }
            if (message.state) {
                if (typeof message.state === 'string') setRuntimeState(message.state);
                else {
                    if (typeof message.state.runState === 'string') setRuntimeState(message.state.runState);
                    if (message.state.connected === false) setStatus('Runtime 未连接 · 显示最后结果');
                }
            }
            if (typeof (message as Envelope & {
                frozen?: boolean;
            }).frozen === 'boolean')
                setFrozen(Boolean((message as Envelope & {
                    frozen?: boolean;
                }).frozen));
            if (message.result) enqueue(applySourceProblems(message.result,sourceProblems.current), connectionId);
        }
        catch (e) {
            if((e as Error).message.includes('Runtime identity changed')){setStatus('Runtime 身份变化，重新获取目录');socket.close();}else setError('收到无效消息，未更新画面');
        } }; socket.onerror = () => setStatus('连接中断'); socket.onclose = () => { if (!closed) {
            connection++;
            Object.values(aborts).forEach(a => a.abort());
            setStatus('离线 · 显示最后结果');
            connectionNeedsRefresh.current=true;
            let delay=2000;
            const reconnect=()=>{retry=setTimeout(async()=>{if(closed || activeRuntime.current!==runtimeId)return;try{await refreshRef.current?.({reconnect:true});}catch{if(!closed && activeRuntime.current===runtimeId){setStatus('连接不可用，稍后重试；也可手动刷新目录');delay=Math.min(delay*2,15000);reconnect();}}},delay)};
            reconnect();
        } }; };
        connect();
        return () => { queue.invalidate(); invalidateCandidates.current = undefined; restagePage.current = undefined; retireExpiredLive.current=undefined; frozenKeys.current = undefined; expiredScopes.current.clear(); closed = true; generation.current++; abort.abort(); Object.values(aborts).forEach(a => a.abort()); clearTimeout(retry); socket?.close(); };
    }, [viewId]);
    async function login(event: React.FormEvent<HTMLFormElement>) { event.preventDefault(); const f = new FormData(event.currentTarget); setBusy(true); setError(''); try {
        const m = await api<Me>('/api/session', { method: 'POST', body: JSON.stringify({ username: f.get('username'), password: f.get('password') }) });
        setCsrf(m.csrf);
        setMe(m);
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } }
    async function action(name: string, scopeId?: string):Promise<boolean> {
        if (!viewId) return false;
        const attempt=++actionAttempt.current;
        setError(''); const actionGeneration=generation.current; const previousKeys=frozenKeys.current;
        if(name==='freeze' && scopeId && !scopes[scopeId]){setError('指定作用域尚无可冻结结果');return false;}
        if(name==='freeze'){
            frozenKeys.current={...previousKeys,...Object.fromEntries(Object.entries(scopes).filter(([scope])=>!scopeId || scope===scopeId).map(([scope,value])=>[scope,value.resultKey]))};
            invalidateCandidates.current?.();
        } else frozenKeys.current=undefined;
        try {
            const response=await api<{viewGeneration?:string}>(`/api/views/${encodeURIComponent(viewId)}/${name==='freeze'?'freeze':'resume'}`,{method:'POST',body:JSON.stringify({resultKey:result?.resultKey,resultKeys:frozenKeys.current})});
            if(attempt!==actionAttempt.current || actionGeneration!==generation.current || !acceptViewGeneration(response.viewGeneration))return false;
            setFrozen(name==='freeze');if(name!=='freeze'){retireExpiredLive.current?.();resumeExpiredScopes(expiredScopes.current,()=>restagePage.current?.());}return true;
        } catch(e) {
            if(attempt!==actionAttempt.current || actionGeneration!==generation.current)return false;
            if(name==='freeze'){frozenKeys.current=previousKeys;restagePage.current?.()}else frozenKeys.current=previousKeys;
            setError((e as Error).message);return false;
        }
    }
    async function control(kind: 'start' | 'stop') { const commandRuntime=runtimeId,commandInstance=infoRef.current?.runtimeInstanceId,commandProject=infoRef.current?.projectId;if(!canControl){setError('当前 Runtime 仅有查看权限');return}if(pending){setError('请先查询上次操作结果或明确结束跟踪，系统不会自动重发。');return}setBusy(true); setError(''); controlId.current ??= Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join(''); try {
        const run = runs.find(r => r.id === controlRunId);
        const workflow = workflows.find(w => w.id === workflowId);
        const target = kind === 'start' ? workflow : run;
        if (!runtimeId || !target) throw new Error(kind === 'start' ? '请明确选择要启动的工作流' : '请明确选择停止目标');
        if (!target.runtimeInstanceId || !target.projectId || (kind === 'start' && !target.configurationRevision)) throw new Error('当前合同缺少控制目标身份，不允许操作');
        let inputs: unknown = {};
        if (kind === 'start') { inputs = parseLosslessJSON(workflowInputs); if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('工作流输入必须是 JSON 对象'); }
        if (kind === 'stop' && run?.allowedActions && !run.allowedActions.some(a=>a.toLowerCase()==='stop')) throw new Error('Runtime 当前不允许停止此运行');
        const intent = { id: controlId.current!, kind, runtimeId,runtimeInstanceId:target.runtimeInstanceId,projectId:target.projectId, state:'submitting' as const, target: `${runtimeId}/${kind === 'start' ? workflowId : controlRunId}` };
        if (me) sessionStorage.setItem(`emo-pending:${me.userId || me.username}`, JSON.stringify(intent)); setPending(intent);
        const r = await api<CommandReply>(kind === 'start' ? '/api/runs/start' : `/api/runs/${encodeURIComponent(controlRunId)}/stop`, {
            method: 'POST', body: JSON.stringify({ requestId: controlId.current, runtimeId, runtimeInstanceId: target.runtimeInstanceId, projectId: target.projectId, runGroupId: run?.runGroupId, configurationRevision:target.configurationRevision, workflowId: kind === 'start' ? workflowId : run?.workflowId, inputsJson:kind === 'start'?workflowInputs:undefined, inputs:workflows.find(w=>w.id===workflowId)?.inputsJson !== undefined?undefined:inputs })
        });
        await resolveCommand(kind,r,intent,intent.id);
    }
    catch (e) {
        setPending(old=>old?{...old,state:'unknown'}:old);
        if(activeRuntime.current===commandRuntime && infoRef.current?.runtimeInstanceId===commandInstance && infoRef.current?.projectId===commandProject)setError(`${(e as Error).message}。结果未确认时不自动重发，请先刷新运行状态。`);
    }
    finally {
        setBusy(false);
    } }
    async function upload(file: File) { setBusy(true); setError(''); try {
        await api('/api/packages', { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file });
        await refreshRef.current?.({quiet:true});
        setNotice('页面包已导入，尚未启用');
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } }
    const canControl = me?.role !== 'viewer' && Boolean(runtimes.find(r=>r.id===runtimeId)?.permissions?.includes('control'));
    if (!me)
        return <main className="login"><h1>Emo Web 操作员面板</h1><p>独立展示客户端，不执行工作流或控制设备驱动。</p><form onSubmit={login}><label>用户名<input name="username" autoComplete="username" required/></label><label>密码<input name="password" type="password" autoComplete="current-password" required/></label><button disabled={busy}>{busy ? '登录中…' : '登录'}</button></form>{error && <p role="alert">{error}</p>}</main>;
    return <main><header><div><h1>Emo Web 操作员面板</h1><p>{me.preview ? '只读预览 · 不启动或停止生产' : me.mode === 'mock' ? '模拟 Runtime · 演示数据，不代表生产设备' : '真实 Runtime 模式 · 能力以合同为准'}</p></div><div>{me.username} · {me.role}<button onClick={async () => { await api('/api/logout', { method: 'POST' }); selectTarget('');setMe(undefined); setResult(undefined); setScopes({}); setPanel(undefined); setCsrf(''); }}>退出</button></div></header>
 {error && <div className="alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}{notice && <div className="notice" role="status">{notice}</div>}
 <section className="toolbar"><label>Runtime<select aria-label="Runtime 目标" value={runtimeId} onChange={e => selectTarget(e.target.value)}>{runtimes.map(r => <option key={r.id} value={r.id}>{r.alias || r.id} · {r.mode}</option>)}</select></label><label>页面包<select aria-label="页面包" value={panel?.id || ''} onChange={async (e) => { const rid=activeRuntime.current,ticket=catalogGeneration.current;try {
        const next=await api<Panel>(`/api/panels/${encodeURIComponent(e.target.value)}?${targetContext(rid,infoRef.current?.projectId)}`);if(rid===activeRuntime.current && ticket===catalogGeneration.current)setPanel(next);
    }
    catch (e) {
        if(rid===activeRuntime.current && ticket===catalogGeneration.current)setError((e as Error).message);
    } }}>{panels.map(p => <option key={p.id} value={p.id}>{p.name || p.title || p.id}</option>)}</select></label><label>查看运行<select aria-label="查看运行" disabled={catalogBusy} value={runId} onChange={e => setRunId(e.target.value)}><option value="">无运行 · 页面 / 历史预览</option>{runs.map(r => <option key={r.id} value={r.id}>{r.name || r.id} · {r.state}</option>)}</select></label><button disabled={catalogBusy} onClick={() => refresh().catch(()=>{})}>刷新页面包和状态</button><small aria-label="当前 Runtime 身份">{runtimeInfo?.runtimeInstanceId || '—'} / {runtimeInfo?.projectId || '—'} · 配置 {runtimeInfo?.configurationRevision || runtimeInfo?.documentRevision || '—'}</small><span className="status">{status} {lastUpdate && `· 最后更新 ${lastUpdate}`}</span></section>
 {pending&&<section className="notice"><strong>待确认操作：{pending.kind} / {pending.target}</strong><button disabled={busy} onClick={queryPending}>查询操作结果</button>{pending.state!=='submitting'&&<button onClick={()=>{if(confirm('结束本次跟踪不会停止或重新执行任何命令。请确认已核对运行状态，后续新操作将是新的意图。'))clearPending()}}>结束跟踪（不重发）</button>}</section>}
 {lastCommand && lastCommand.runtimeId===runtimeId && (!lastCommand.runtimeInstanceId || lastCommand.runtimeInstanceId===runtimeInfo?.runtimeInstanceId) && (!lastCommand.projectId || lastCommand.projectId===runtimeInfo?.projectId) && lastCommand.runId && (()=>{const authoritative=lastCommand.reply.run || runs.find(run=>run.id===lastCommand.runId);return <section className="notice" aria-label="原控制运行状态"><strong>原控制 Job：{lastCommand.runId}</strong><p>权威状态：{authoritative?.status || authoritative?.state || lastCommand.reply.status || '待刷新'} · 持有资源：{authoritative?.ownsResources===undefined?'未知':authoritative.ownsResources?'是':'否'} · 展示资源已释放：{authoritative?.displayResourcesReleased===undefined?'未知':authoritative.displayResourcesReleased?'是':'否'}</p><small>命令受理不等于 Job 结束；使用刷新获取后续权威状态。</small></section>})()}
 <section className="toolbar"><button disabled={!result || frozen} onClick={() => action('freeze')}>冻结查看</button><button disabled={!frozen} onClick={() => action('resume_live')}>恢复实时</button><span>{frozen ? '已冻结查看，后台仍在运行' : '实时查看'}</span>{canControl && <><span className="control-target">控制目标：{controlRunId || '未选择'}（独立于查看运行）</span><label>停止目标<select aria-label="停止控制目标" disabled={catalogBusy} value={controlRunId} onChange={e=>setControlRunId(e.target.value)}><option value="">明确选择停止对象</option>{runs.map(r=><option key={r.id} value={r.id}>{r.name||r.id} · {r.state}</option>)}</select></label><button className="danger" disabled={busy || catalogBusy || !controlRunId || Boolean(runs.find(r=>r.id===controlRunId)?.allowedActions && !runs.find(r=>r.id===controlRunId)?.allowedActions?.some(a=>a.toLowerCase()==='stop'))} onClick={() => { if (confirm(`停止当前明确选中的运行 ${controlRunId}？`))
        control('stop'); }}>停止选中运行</button></>}</section>
 {canControl && <section className="toolbar"><label>启动工作流<select aria-label="启动工作流目标" disabled={catalogBusy} value={workflowId} onChange={e => { inputDirty.current=false;setWorkflowId(e.target.value); setWorkflowInputs(workflows.find(w=>w.id===e.target.value)?.inputsJson || JSON.stringify(workflows.find(w=>w.id===e.target.value)?.inputs || {})); }}><option value="">{workflows.length?'明确选择工作流':'无可用工作流 / 合同能力未就绪'}</option>{workflows.map(w => <option key={w.id} value={w.id}>{w.name || w.id}</option>)}</select></label><WorkflowInputs workflow={workflows.find(w=>w.id===workflowId)} value={workflowInputs} onChange={value=>{inputDirty.current=true;selectionRef.current={...selectionRef.current,workflowInputs:value,dirty:true};setWorkflowInputs(value)}}/><button disabled={busy || catalogBusy || !workflowId || !runtimeId || workflows.find(w=>w.id===workflowId)?.canStart === false} onClick={() => control('start')}>启动工作流</button><small>独立启动目标，不使用历史选行；输入由后端再次校验。</small>{workflows.find(w => w.id === workflowId)?.inputSchema && <details><summary>输入约束</summary><pre>{JSON.stringify(workflows.find(w => w.id === workflowId)?.inputSchema, null, 2)}</pre></details>}</section>}
 {panel ? <><nav className="page-nav">{panel.pages.map(p => <button key={p.id} aria-current={page === p.id ? 'page' : undefined} onClick={() => changePage(p.id)}>{p.title}</button>)}</nav><PanelRenderer panel={panel} pageId={page} onPage={changePage} result={result} results={scopes} viewId={viewId} onAction={action} state={status} runtimeState={runtimeState || runs.find(r=>r.id===runId)?.state}/></> : <p>暂无已发布页面，请联系管理员。</p>}
 {me.role === 'admin' && <details className="admin"><summary>页面包管理</summary><p>导入不会更改 Runtime 工程；启用 / 切回仅切换 Web 页面版本，仅更新当前 Runtime／正式工程的启用版本，不改变其他目标的版本或访问。</p><input aria-label="导入页面包" type="file" accept=".emoweb,.zip" disabled={busy} onChange={e => { if (e.target.files?.[0])
        upload(e.target.files[0]); }}/>{packages.map(p => <div key={p.id || p.packageId} data-package-id={p.id || p.packageId}>{p.name || (p.manifest ? `${p.manifest.projectId} / ${p.manifest.packageRevision}` : p.id || p.packageId)} · 关联：{p.runtimeIds?.join(', ') || '未关联'} {p.active && '已启用'} <button disabled={!runtimeId} onClick={async () => { const activatedRuntime=runtimeId,activatedProject=runtimeInfo?.projectId;if (!confirm(`将此版本启用 / 切回到 ${runtimeId}，仅更新该目标当前正式工程的启用版本？其他 Runtime 不受影响。不会改动生产工程。`)) return; try {
        await api(`/api/packages/${encodeURIComponent(p.id || p.packageId || '')}/activate`, { method: 'POST', body: JSON.stringify({ runtimeId,projectId:runtimeInfo?.projectId }) });
        if(activeRuntime.current===activatedRuntime && infoRef.current?.projectId===activatedProject){await refresh();setNotice('当前目标页面包已切换；其他目标未改变');}
    }
    catch (e) {
        if(activeRuntime.current===activatedRuntime && infoRef.current?.projectId===activatedProject)setError((e as Error).message);
    } }}>对当前目标启用 / 切回此版本</button></div>)}</details>}
 {!canControl && runtimeId && <p>当前目标只读：未授予生产控制权限。</p>}
 <aside aria-label="作用域进度">{Object.entries(scopeStatuses).map(([key,value])=><p key={key}>{value.jobId} / {value.scopeId}：{scopeActivity(value,scopes[value.scopeId],frozenKeys.current?.[value.scopeId]===scopes[value.scopeId]?.resultKey && Boolean(scopes[value.scopeId]))}</p>)}</aside>
 <RuntimeStatus runtimeId={runtimeId} runId={runId} runtimeInstanceId={runs.find(r=>r.id===runId)?.runtimeInstanceId} projectId={runs.find(r=>r.id===runId)?.projectId}/>
 {me.role === 'admin' && <Admin current={me.username}/>}<footer>Panel Contract v1 · {panel?.packageRevision || '—'} · {result?.resultKey || '等待结果'} · 不替代现场设备验收</footer></main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
