import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Me, Panel, Run, Result, Envelope, RuntimeTarget, WorkflowTarget } from './contracts/types';
import { api, setCsrf } from './data/api';
import { LatestScopeQueue } from './state/latest';
import { prepareResult, releaseResult, pageImageSources } from './state/prepare';
import { PanelRenderer } from './renderer/PanelRenderer';
import './style.css';
import { WorkflowInputs } from './components/WorkflowInputs';
import { RuntimeStatus } from './components/RuntimeStatus';
import { Admin } from './components/Admin';
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
    function changePage(id: string) { pageRef.current = id; setPage(id); restagePage.current?.(); }
    const [runs, setRuns] = useState<Run[]>([]);
    const [runId, setRunId] = useState('');
    const [controlRunId, setControlRunId] = useState('');
    const [runtimeId, setRuntimeId] = useState('');
    const [runtimes, setRuntimes] = useState<RuntimeTarget[]>([]);
    const [workflows, setWorkflows] = useState<WorkflowTarget[]>([]);
    const [workflowId, setWorkflowId] = useState('');
    const [workflowInputs, setWorkflowInputs] = useState('{}');
    const [viewId, setViewId] = useState('');
    useEffect(() => { if (!viewId) return; return () => { void api(`/api/views/${encodeURIComponent(viewId)}`, { method: 'DELETE' }).catch(() => {}); }; }, [viewId]);
    const [result, setResult] = useState<Result>();
    const [scopes, setScopes] = useState<Record<string, Result>>({});
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
    }[]>([]);
    const [notice, setNotice] = useState('');
    const generation = useRef(0);
    const [pending,setPending]=useState<{id:string;kind:string;target:string}|undefined>();
    const controlId = useRef<string | undefined>(undefined);
    useEffect(()=>{if(!me){setPending(undefined);return}try{const saved=sessionStorage.getItem(`emo-pending:${me.userId || me.username}`);setPending(saved?JSON.parse(saved):undefined)}catch{setPending(undefined)}},[me]);
    function clearPending(){if(me)sessionStorage.removeItem(`emo-pending:${me.userId || me.username}`);setPending(undefined);controlId.current=undefined;}
    async function queryPending(){if(!pending)return;try{const r=await api<{state:string}>(`/api/operations/by-request/${encodeURIComponent(pending.id)}`);setNotice(`操作查询：${r.state}`);if(['succeeded','failed','rejected','cancelled'].includes(r.state))clearPending()}catch(e){setError(`查询未确认：${(e as Error).message}；未重新发送命令。`)}}
    async function refresh() {
        const [ps, targets] = await Promise.all([api<Panel[]>('/api/panels'), api<RuntimeTarget[]>('/api/runtimes')]);
        setPanels(ps); setRuntimes(targets);
        setRuntimeId(old => targets.some(t => t.id === old) ? old : targets[0]?.id || '');
        if (ps.length) setPanel(await api<Panel>(`/api/panels/${encodeURIComponent(ps[0].id)}`));
        if (me?.role === 'admin') setPackages(await api('/api/packages'));
    }
    useEffect(() => {
        let valid = true; setRuns([]); setWorkflows([]); setRunId(''); setControlRunId(''); setWorkflowId('');
        if (!runtimeId || !me) return;
        Promise.all([api<Run[]>(`/api/runtimes/${encodeURIComponent(runtimeId)}/runs`), api<WorkflowTarget[]>(`/api/runtimes/${encodeURIComponent(runtimeId)}/workflows`)])
            .then(([rs, ws]) => { if (valid) { setRuns(rs); setWorkflows(ws); setRunId(rs[0]?.id || ''); setWorkflowId(ws[0]?.id || ''); setWorkflowInputs(JSON.stringify(ws[0]?.inputs || {})); } })
            .catch(e => { if (valid) setError(e.message); });
        return () => { valid = false; };
    }, [runtimeId, me]);
    useEffect(() => { api<Me>('/api/me').then(m => { setCsrf(m.csrf); setMe(m); }).catch(() => { }); }, []);
    useEffect(() => { if (me)
        refresh().catch(e => setError(e.message)); }, [me]);
    useEffect(() => { pageRef.current = panel?.defaultPageId || panel?.pages[0]?.id || ''; setPage(pageRef.current); setResult(undefined); setScopes({}); }, [panel]);
    useEffect(() => { if (!me || !panel || !runtimeId) {
        setViewId('');
        return;
    } let valid = true; setViewId(''); setResult(undefined); setScopes({}); setFrozen(false); setRuntimeState(''); api<{
        id: string;
    }>('/api/views', { method: 'POST', body: JSON.stringify({ runtimeId, panelId: panel.id, runId: runId || undefined }) }).then(v => { if (valid)
        setViewId(v.id); else void api(`/api/views/${encodeURIComponent(v.id)}`, { method: 'DELETE' }).catch(() => {}); }).catch(e => { if (valid)
        setError(e.message); }); return () => { valid = false; }; }, [me, panel, runtimeId, runId]);
    useEffect(() => {
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
        const stage = async (raw: Result, connectionId: number) => {
            const scope = raw.resultScopeId || 'default';
            if (frozenKeys.current && frozenKeys.current[scope] !== raw.resultKey) return false;
            rawResults[scope] = raw;
            const id = (candidates[scope] || 0) + 1; candidates[scope] = id;
            aborts[scope]?.abort(); aborts[scope] = new AbortController();
            const visiblePage = panel?.pages.find(p => p.id === pageRef.current) || panel?.pages[0];
            const ready = await prepareResult(raw, aborts[scope].signal, pageImageSources(visiblePage, scope));
            if (!closed && connectionId === connection && gen === generation.current && id === candidates[scope] && (!frozenKeys.current || frozenKeys.current[scope] === ready.resultKey)) {
                displayedRaw[scope] = raw; ownedResults.current.add(ready); setScopes(old => ({ ...old, [scope]: ready })); setResult(ready); setStatus('已连接'); setLastUpdate(new Date().toLocaleTimeString()); return true;
            } else releaseResult(ready);
            return false;
        };
        const queue = new LatestScopeQueue<{raw:Result;connectionId:number}>(item => stage(item.raw,item.connectionId).catch(() => { if (!closed) setError('结果准备失败，未更新画面'); return false; }));
        const enqueue = (raw:Result,connectionId:number) => queue.push(raw.resultScopeId || 'default', {raw,connectionId}, JSON.stringify([pageRef.current,raw.resultKey,raw.sources]));
        invalidateCandidates.current = () => {
            queue.invalidate();
            if (frozenKeys.current) Object.assign(rawResults, displayedRaw);
            Object.keys(candidates).forEach(scope => candidates[scope]++);
            Object.values(aborts).forEach(a => a.abort());
        };
        restagePage.current = () => {
            invalidateCandidates.current?.();
            setResult(undefined); setScopes({});
            Object.values(rawResults).forEach(raw => { enqueue(raw, connection); });
        };
        const connect = () => { const connectionId = ++connection; setStatus('正在同步'); socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/views/${encodeURIComponent(viewId)}`); socket.onopen = () => setStatus('连接已建立，等待快照'); socket.onmessage = async (event) => { try {
            const message = JSON.parse(event.data) as Envelope;
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
            if (message.state) setRuntimeState(typeof message.state === 'string' ? message.state : JSON.stringify(message.state));
            if (typeof (message as Envelope & {
                frozen?: boolean;
            }).frozen === 'boolean')
                setFrozen(Boolean((message as Envelope & {
                    frozen?: boolean;
                }).frozen));
            if (message.result) enqueue(message.result, connectionId);
        }
        catch {
            setError('收到无效消息，未更新画面');
        } }; socket.onerror = () => setStatus('连接中断'); socket.onclose = () => { if (!closed) {
            connection++;
            Object.values(aborts).forEach(a => a.abort());
            setStatus('离线 · 显示最后结果');
            retry = setTimeout(() => { api<{
                id: string;
            }>('/api/views', { method: 'POST', body: JSON.stringify({ runtimeId, panelId: panel?.id, runId: runId || undefined }) }).then(v => { if (!closed)
                setViewId(v.id); else void api(`/api/views/${encodeURIComponent(v.id)}`, { method: 'DELETE' }).catch(() => {}); }).catch(e => { if (!closed) {
                setError(`重连失败：${e.message}`);
                setStatus('连接不可用，请重新登录或刷新状态');
            } }); }, 2000);
        } }; };
        connect();
        return () => { queue.invalidate(); invalidateCandidates.current = undefined; restagePage.current = undefined; frozenKeys.current = undefined; closed = true; generation.current++; abort.abort(); Object.values(aborts).forEach(a => a.abort()); clearTimeout(retry); socket?.close(); };
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
    async function action(name: string) { if (!viewId)
        return; setError(''); const actionGeneration = generation.current; const previousKeys = frozenKeys.current; if (name === 'freeze') { frozenKeys.current = Object.fromEntries(Object.entries(scopes).map(([scope, value]) => [scope, value.resultKey])); invalidateCandidates.current?.(); } else frozenKeys.current = undefined; try {
        const response = await api<{
            result?: Result; viewGeneration?: string;
        }>(`/api/views/${encodeURIComponent(viewId)}/${name === 'freeze' ? 'freeze' : 'resume'}`, { method: 'POST', body: JSON.stringify({ resultKey: result?.resultKey, resultKeys: frozenKeys.current }) });
        if (actionGeneration !== generation.current || !acceptViewGeneration(response.viewGeneration)) return;
        setFrozen(name === 'freeze');
        // Result snapshots arrive through the generation-checked WebSocket path.
        void response;
    }
    catch (e) {
        if (actionGeneration !== generation.current) return;
        if (name === 'freeze') { frozenKeys.current = undefined; restagePage.current?.(); } else frozenKeys.current = previousKeys;
        setError((e as Error).message);
    } }
    async function control(kind: 'start' | 'stop') { if(pending){setError('请先查询上次操作结果或明确结束跟踪，系统不会自动重发。');return}setBusy(true); setError(''); controlId.current ??= Array.from(crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join(''); try {
        const run = runs.find(r => r.id === controlRunId);
        const workflow = workflows.find(w => w.id === workflowId);
        const target = kind === 'start' ? workflow : run;
        if (!runtimeId || !target) throw new Error(kind === 'start' ? '请明确选择要启动的工作流' : '请明确选择停止目标');
        if (!target.runtimeInstanceId || !target.projectId || !target.configurationRevision) throw new Error('当前合同缺少控制目标身份，不允许操作');
        let inputs: unknown = {};
        if (kind === 'start') { inputs = JSON.parse(workflowInputs); if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error('工作流输入必须是 JSON 对象'); }
        const intent = { id: controlId.current!, kind, target: `${runtimeId}/${kind === 'start' ? workflowId : controlRunId}` };
        if (me) sessionStorage.setItem(`emo-pending:${me.userId || me.username}`, JSON.stringify(intent)); setPending(intent);
        const r = await api<{ operationId?: string; state?: string }>(kind === 'start' ? '/api/runs/start' : `/api/runs/${encodeURIComponent(controlRunId)}/stop`, {
            method: 'POST', body: JSON.stringify({ requestId: controlId.current, runtimeId, runtimeInstanceId: target.runtimeInstanceId, projectId: target.projectId, configurationRevision:target.configurationRevision, workflowId: kind === 'start' ? workflowId : run?.workflowId, inputs })
        });
        setNotice(`操作已受理：${r.operationId || r.state || kind}`);
        if(r.state&&['succeeded','failed','rejected','cancelled'].includes(r.state))clearPending();
        setRuns(await api(`/api/runtimes/${encodeURIComponent(runtimeId)}/runs`));
    }
    catch (e) {
        setError(`${(e as Error).message}。结果未确认时不自动重发，请先刷新运行状态。`);
    }
    finally {
        setBusy(false);
    } }
    async function upload(file: File) { setBusy(true); setError(''); try {
        await api('/api/packages', { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file });
        setPackages(await api('/api/packages'));
        setNotice('页面包已导入，尚未启用');
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } }
    if (!me)
        return <main className="login"><h1>Emo Web 操作员面板</h1><p>独立展示客户端，不执行工作流或控制设备驱动。</p><form onSubmit={login}><label>用户名<input name="username" autoComplete="username" required/></label><label>密码<input name="password" type="password" autoComplete="current-password" required/></label><button disabled={busy}>{busy ? '登录中…' : '登录'}</button></form>{error && <p role="alert">{error}</p>}</main>;
    return <main><header><div><h1>Emo Web 操作员面板</h1><p>{me.mode === 'mock' ? '模拟 Runtime · 草案合同 · 演示数据，不代表生产设备' : '真实 Runtime 模式 · 能力以合同为准'}</p></div><div>{me.username} · {me.role}<button onClick={async () => { await api('/api/logout', { method: 'POST' }); setMe(undefined); setResult(undefined); setScopes({}); setPanel(undefined); setCsrf(''); }}>退出</button></div></header>
 {error && <div className="alert" role="alert">{error}<button onClick={() => setError('')}>关闭</button></div>}{notice && <div className="notice" role="status">{notice}</div>}
 <section className="toolbar"><label>Runtime<select aria-label="Runtime 目标" value={runtimeId} onChange={e => setRuntimeId(e.target.value)}>{runtimes.map(r => <option key={r.id} value={r.id}>{r.alias || r.id} · {r.mode}</option>)}</select></label><label>页面包<select aria-label="页面包" value={panel?.id || ''} onChange={async (e) => { try {
        setPanel(await api(`/api/panels/${encodeURIComponent(e.target.value)}`));
    }
    catch (e) {
        setError((e as Error).message);
    } }}>{panels.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>查看运行<select aria-label="查看运行" value={runId} onChange={e => setRunId(e.target.value)}><option value="">无运行 · 页面 / 历史预览</option>{runs.map(r => <option key={r.id} value={r.id}>{r.name || r.id} · {r.state}</option>)}</select></label><button onClick={() => refresh().catch(e => setError(e.message))}>刷新页面包和状态</button><span className="status">{status} {lastUpdate && `· 最后更新 ${lastUpdate}`}</span></section>
 {pending&&<section className="notice"><strong>待确认操作：{pending.kind} / {pending.target}</strong><button onClick={queryPending}>查询操作结果</button><button onClick={()=>{if(confirm('结束本次跟踪不会停止或重新执行任何命令。请确认已核对运行状态，后续新操作将是新的意图。'))clearPending()}}>结束跟踪（不重发）</button></section>}
 <section className="toolbar"><button disabled={!result || frozen} onClick={() => action('freeze')}>冻结查看</button><button disabled={!frozen} onClick={() => action('resume_live')}>恢复实时</button><span>{frozen ? '已冻结查看，后台仍在运行' : '实时查看'}</span>{me.role !== 'viewer' && <><span className="control-target">控制目标：{controlRunId || '未选择'}（独立于查看运行）</span><label>停止目标<select aria-label="停止控制目标" value={controlRunId} onChange={e=>setControlRunId(e.target.value)}><option value="">明确选择停止对象</option>{runs.map(r=><option key={r.id} value={r.id}>{r.name||r.id} · {r.state}</option>)}</select></label><button className="danger" disabled={busy || !controlRunId} onClick={() => { if (confirm(`停止当前明确选中的运行 ${controlRunId}？`))
        control('stop'); }}>停止选中运行</button></>}</section>
 {me.role !== 'viewer' && <section className="toolbar"><label>启动工作流<select aria-label="启动工作流目标" value={workflowId} onChange={e => { setWorkflowId(e.target.value); setWorkflowInputs(JSON.stringify(workflows.find(w=>w.id===e.target.value)?.inputs || {})); }}>{!workflows.length && <option value="">无可用工作流 / 合同能力未就绪</option>}{workflows.map(w => <option key={w.id} value={w.id}>{w.name || w.id}</option>)}</select></label><WorkflowInputs workflow={workflows.find(w=>w.id===workflowId)} value={workflowInputs} onChange={setWorkflowInputs}/><button disabled={busy || !workflowId || !runtimeId || workflows.find(w=>w.id===workflowId)?.canStart === false} onClick={() => control('start')}>启动工作流</button><small>独立启动目标，不使用历史选行；输入由后端再次校验。</small>{workflows.find(w => w.id === workflowId)?.inputSchema && <details><summary>输入约束（草案）</summary><pre>{JSON.stringify(workflows.find(w => w.id === workflowId)?.inputSchema, null, 2)}</pre></details>}</section>}
 {panel ? <><nav className="page-nav">{panel.pages.map(p => <button key={p.id} aria-current={page === p.id ? 'page' : undefined} onClick={() => changePage(p.id)}>{p.title}</button>)}</nav><PanelRenderer panel={panel} pageId={page} onPage={changePage} result={result} results={scopes} viewId={viewId} onAction={action} state={status} runtimeState={runtimeState || runs.find(r=>r.id===runId)?.state}/></> : <p>暂无已发布页面，请联系管理员。</p>}
 {me.role === 'admin' && <details className="admin"><summary>页面包管理</summary><p>导入不会更改 Runtime 工程；启用 / 切回仅切换 Web 页面版本，并将该包授权关联设置为当前选中的 Runtime（替换此包旧关联，不累加）。</p><input aria-label="导入页面包" type="file" accept=".emoweb,.zip" disabled={busy} onChange={e => { if (e.target.files?.[0])
        upload(e.target.files[0]); }}/>{packages.map(p => <div key={p.id || p.packageId}>{p.name || p.id || p.packageId} · 关联：{p.runtimeIds?.join(', ') || '未关联'} {p.active && '已启用'} <button disabled={!runtimeId} onClick={async () => { if (!confirm(`将此版本启用 / 切回到 ${runtimeId}，并把该包授权关联替换为仅此目标？不会改动生产工程。`)) return; try {
        await api(`/api/packages/${encodeURIComponent(p.id || p.packageId || '')}/activate`, { method: 'POST', body: JSON.stringify({ runtimeIds: [runtimeId] }) });
        await refresh();
        setNotice('页面包已切换');
    }
    catch (e) {
        setError((e as Error).message);
    } }}>对当前目标启用 / 切回此版本</button></div>)}</details>}
 <RuntimeStatus runtimeId={runtimeId}/>
 {me.role === 'admin' && <Admin current={me.username}/>}<footer>页面合同适配草案 · {panel?.packageRevision || '—'} · {result?.resultKey || '等待结果'} · 不替代现场设备验收</footer></main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
