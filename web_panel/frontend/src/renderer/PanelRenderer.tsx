import { useEffect, useState } from 'react';
import type { Component, Panel, Result, History } from '../contracts/types';
import { preparedImageUrl } from '../state/prepare';
import { api, safeAssetUrl } from '../data/api';
const gridOf = (c: Component) => ({ ...(c.props || {}), ...(typeof c.props?.grid === 'object' ? c.props.grid : {}), ...c.grid }) as NonNullable<Component['grid']>;
const ordered = (components: Component[]) => [...components].sort((a,b) => (gridOf(a).row || 1) - (gridOf(b).row || 1) || (gridOf(a).column || 1) - (gridOf(b).column || 1));
const stringify = (v: unknown) => v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
function Clock({ format, timezone }: { format?: unknown; timezone?: unknown; }) {
 const [now,setNow]=useState(new Date());useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
 const zone=typeof timezone==='string'&&timezone!=='browser'?timezone:undefined;
 if(format!==undefined&&!['date','time','datetime'].includes(String(format)))return <p>不支持的时钟格式</p>;
 try{const options:Intl.DateTimeFormatOptions={timeZone:zone,...(format==='date'?{dateStyle:'medium' as const}:format==='time'?{timeStyle:'medium' as const}:{dateStyle:'medium' as const,timeStyle:'medium' as const})};return <time title={`时区：${zone||'浏览器本地'}，非检测时间`}>{new Intl.DateTimeFormat(undefined,options).format(now)}<small> · {zone||'浏览器本地时区'}</small></time>}catch{return <p>时区配置无效</p>}
}
function HistoryTable({ component, viewId, aggregate, onSelection }: {
    component: Component;
    viewId: string;
    aggregate: boolean;
    onSelection: (componentId:string,row?:Record<string,unknown>)=>void;
}) {
    const [data, setData] = useState<History>();
    const [page, setPage] = useState(1);
    const [q, setQ] = useState('');
    const [sort, setSort] = useState('');
    const [order, setOrder] = useState('asc');
    const [refresh, setRefresh] = useState(0);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Record<string, unknown>>();
    useEffect(() => { let live = true; const abort = new AbortController(); setSelected(undefined); onSelection(component.id,undefined); setError(''); api<History>(`/api/views/${encodeURIComponent(viewId)}/history/${encodeURIComponent(component.id)}?page=${page}&q=${encodeURIComponent(q)}${sort ? `&sort=${encodeURIComponent(sort)}` : ''}&order=${order}`, { signal: abort.signal }).then(d => { if (live)
        setData(d); }).catch(e => { if (live) {
        setData(undefined);
        setError(e.message);
    } }); return () => { live = false; abort.abort(); }; }, [viewId, component.id, page, q, sort, order, refresh]);
    if (error)
        return <div><p role="alert">{error}</p><button onClick={()=>{setPage(1);setQ('');setSort('');setRefresh(n=>n+1)}}>重置查询并重试</button></div>;
    if (!data)
        return <p>正在读取历史…</p>;
    if (aggregate)
        return <strong>{stringify((data as unknown as {
            value?: unknown;
        }).value ?? data.total)}</strong>;
    const columns = data.rows.length ? Object.keys(data.rows[0]).filter(k => !k.toLowerCase().includes('imageref')) : [];
    const image = selected && safeAssetUrl(selected.imageUrl ?? (typeof selected.imageRef === 'string' ? `/api/history-images/${encodeURIComponent(selected.imageRef)}` : undefined));
    return <><div className="toolbar"><label>筛选<input aria-label={`${component.id} 筛选`} value={q} onChange={e => { setPage(1); setQ(e.target.value); }}/></label><label>排序<select aria-label={`${component.id} 排序`} value={sort} onChange={e => { setPage(1); setSort(e.target.value); }}><option value="">默认</option>{columns.map(k => <option key={k}>{k}</option>)}</select></label><select aria-label={`${component.id} 顺序`} value={order} onChange={e => { setPage(1); setOrder(e.target.value); }}><option value="asc">升序</option><option value="desc">降序</option></select><button onClick={() => setRefresh(n => n + 1)}>刷新历史</button></div><div className="table-scroll"><table><thead><tr>{columns.map(k => <th key={k}>{k}</th>)}</tr></thead><tbody>{data.rows.map((row, i) => <tr key={i} tabIndex={0} aria-selected={selected === row} onClick={() => {setSelected(row);onSelection(component.id,row)}} onKeyDown={e => { if (e.key === 'Enter')
        {setSelected(row);onSelection(component.id,row);} }}>{columns.map(k => <td key={k}>{stringify(row[k])}</td>)}</tr>)}</tbody></table></div>{!data.rows.length && <p>暂无记录</p>}<nav><button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>第 {page} 页 · 共 {data.total} 条</span><button disabled={page * data.pageSize >= data.total} onClick={() => setPage(p => p + 1)}>下一页</button></nav>{selected && (image ? <SafeImage key={image} url={image} label="选中历史记录"/> : <p>选中记录无可用图片</p>)}</>;
}
function SafeImage({url,label}:{url:string;label:string}) {const [failed,setFailed]=useState(false);return failed?<p>图片读取失败或无权访问</p>:<img className="result-image" src={url} alt={label} onError={()=>setFailed(true)}/>;}
function ResultTable({value,columns,resultKey}:{value:unknown;columns:unknown;resultKey?:string}) {
 const rows=Array.isArray(value)?value:[];const[page,setPage]=useState(1);useEffect(()=>setPage(1),[resultKey]);
 const defined=Array.isArray(columns)?columns.filter((c):c is {key:string;title?:string}=>typeof c==='object'&&c!==null&&typeof c.key==='string'):[];
 const keys=defined.length?defined:rows[0]&&typeof rows[0]==='object'?Object.keys(rows[0]).map(key=>({key,title:key})): [{key:'value',title:'值'}];
 return <><div className="table-scroll"><table><thead><tr>{keys.map(c=><th key={c.key}>{c.title||c.key}</th>)}</tr></thead><tbody>{rows.slice((page-1)*20,page*20).map((row,i)=><tr key={i}>{keys.map(c=><td key={c.key}>{stringify(typeof row==='object'&&row!==null?row[c.key]:row)}</td>)}</tr>)}</tbody></table></div>{!rows.length&&<p>本轮暂无表格数据</p>}{rows.length>20&&<nav><button disabled={page<=1} onClick={()=>setPage(n=>n-1)}>上一页</button><span>{page} / {Math.ceil(rows.length/20)}</span><button disabled={page*20>=rows.length} onClick={()=>setPage(n=>n+1)}>下一页</button></nav>}</>;
}
export function PanelRenderer({ panel, pageId, onPage, result, results, viewId, onAction, state }: {
    panel: Panel;
    pageId: string;
    onPage: (id: string) => void;
    result?: Result;
    results?: Record<string, Result>;
    viewId?: string;
    onAction: (a: string) => void;
    state: string;
}) {
    const [selections,setSelections]=useState<Record<string,Record<string,unknown>|undefined>>({});
    useEffect(()=>setSelections({}),[viewId,panel.packageRevision,pageId]);
    const selectHistory=(componentId:string,row?:Record<string,unknown>)=>setSelections(old=>({...old,[componentId]:row}));
    const page = panel.pages.find(p => p.id === pageId) || panel.pages[0];
    if (!page)
        return <p>无可用页面</p>;
    function render(c: Component, columns = page.grid?.columns || panel.grid?.columns || 12) {
        const p = c.props || {};
        const binding = typeof c.binding === 'string' ? c.binding : c.binding?.sourceId;
        const requestedScope = typeof c.binding === 'object' ? c.binding.scopeId : undefined;
        const boundResult = requestedScope ? results?.[requestedScope] : result;
        const source = binding ? boundResult?.sources[binding] : undefined;
        let value = binding ? source?.value : p.text ?? p.value;
        if (source?.valueJson !== undefined) {
            try {
                value = JSON.parse(source.valueJson);
            }
            catch {
                value = source.valueJson;
            }
        }
        const field = typeof c.binding === 'object' ? c.binding.field : undefined;
        if (field && value && typeof value === 'object') value = Object.prototype.hasOwnProperty.call(value, field) ? (value as Record<string, unknown>)[field] : undefined;
        const failed = !['history_fixed','history_selection'].includes(String(p.sourceMode)) && Boolean(binding) && (!source || !['ready', 'ok', 'available'].includes(source.state));
        let body;
        switch (c.type) {
            case 'image': {
                const selected = typeof p.sourceComponentId === 'string' ? selections[p.sourceComponentId] : undefined;
                const historyRef = p.sourceMode === 'history_fixed' && viewId ? `/api/views/${encodeURIComponent(viewId)}/images/${encodeURIComponent(c.id)}` : p.sourceMode === 'history_selection' && typeof selected?.imageRef === 'string' ? `/api/history-images/${encodeURIComponent(selected.imageRef)}` : undefined;
                const ref = historyRef ?? (p.sourceMode === 'history_selection' || p.sourceMode === 'history_fixed' ? undefined : source?.image?.url ?? (typeof p.assetId === 'string' ? `/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.assetId)}` : undefined));
                const url = preparedImageUrl(ref);
                body = failed ? <p>{source?.reason || '本轮图片不可用'}</p> : url ? <SafeImage key={url} url={url} label={c.label || '检测结果'}/> : <p>暂无图片</p>;
                break;
            }
            case 'text':
                body = <p>{stringify(value)}</p>;
                break;
            case 'number': {
                const digits = typeof p.decimals === 'number' ? Math.max(0, Math.min(8, p.decimals)) : 0;
                body = <strong className="number">{typeof value === 'number' ? value.toFixed(digits) : stringify(value)} <small>{stringify(p.unit ?? '')}</small></strong>;
                break;
            }
            case 'indicator': {
                const map = p.mapping && typeof p.mapping === 'object' ? p.mapping as Record<string, unknown> : {};
                const mapped = map[String(value)] ?? value;
                const ok = mapped === true || mapped === 'OK';const ng=mapped===false||mapped==='NG';
                body = <strong className={`indicator ${ok?'ok':ng?'ng':'unknown'}`}>{ok?'OK':ng?'NG':value==null?'未知':stringify(mapped)}</strong>;
                break;
            }
            case 'runtime_status':
                body = <p>{state}</p>;
                break;
            case 'table': {body=<ResultTable value={value} columns={p.columns} resultKey={boundResult?.resultKey}/>;break;}
            case 'navigation_button':
                body = <button onClick={() => { const a = String(p.action || 'navigate'); if (a === 'navigate') {
                    const target = String(p.targetPageId || '');
                    if (panel.pages.some(x => x.id === target))
                        onPage(target);
                }
                else if (['freeze', 'resume_live'].includes(a))
                    onAction(a); }}>{typeof p.icon === 'string' && <span aria-hidden="true">{p.icon} </span>}{stringify(p.text ?? c.label ?? '打开')}</button>;
                break;
            case 'container':
                body = <div className="panel-grid" style={{ gridTemplateColumns: `repeat(${gridOf(c).columns || columns}, minmax(0, 1fr))`, gap: gridOf(c).gap }}>{ordered(c.children || []).map(child => render(child, gridOf(c).columns || columns))}</div>;
                break;
            case 'page_title':
                body = <h2>{page.title}</h2>;
                break;
            case 'page_navigation':
                body = <nav>{panel.pages.map(x => <button key={x.id} aria-current={x.id === page.id ? 'page' : undefined} onClick={() => onPage(x.id)}>{x.title}</button>)}</nav>;
                break;
            case 'clock':
                body = <Clock format={p.format} timezone={p.timezone}/>;
                break;
            case 'sqlite_table':
            case 'sqlite_value':
                body = viewId ? <HistoryTable key={`${viewId}:${c.id}`} component={c} viewId={viewId} aggregate={c.type === 'sqlite_value'} onSelection={selectHistory}/> : <p>请先选择查看会话</p>;
                break;
            default: body = <p role="alert">不支持的组件：{c.type}</p>;
        }
        if (failed && ['image', 'text', 'number', 'indicator', 'table'].includes(c.type)) body = <p role="status">不可用：{source?.reason || '本轮数据未就绪'}</p>;
        const style = p.style && typeof p.style === 'object' ? p.style as Record<string, unknown> : {};
        const grid = gridOf(c);
        const span = Math.min(columns, Math.max(1, Number(grid.columnSpan) || Math.min(6, columns)));
        return <section className="panel-card" key={c.id} style={{ fontWeight: ['normal', 'bold', '400', '500', '600', '700', 400, 500, 600, 700].includes(style.fontWeight as string | number) ? style.fontWeight as 'normal' | 'bold' | 400 | 500 | 600 | 700 : undefined, gridColumn: `${grid.column || 'auto'} / span ${span}`, gridRow: `${grid.row || 'auto'} / span ${Math.max(1, Number(grid.rowSpan) || 1)}`, fontSize: typeof style.fontSize === 'number' ? Math.min(128, Math.max(8, style.fontSize)) : undefined, color: typeof style.color === 'string' && /^(#[0-9a-f]{3,8}|[a-z]{1,20})$/i.test(style.color) ? style.color : undefined, backgroundColor: typeof style.backgroundColor === 'string' && /^(#[0-9a-f]{3,8}|[a-z]{1,20})$/i.test(style.backgroundColor) ? style.backgroundColor : undefined, textAlign: ['left', 'center', 'right'].includes(String(style.textAlign)) ? style.textAlign as 'left' | 'center' | 'right' : undefined }}>{(c.label||typeof p.label==='string')&&<h3>{c.label||String(p.label)}</h3>}{body}</section>;
    }
    return <div className="panel-grid" style={{ gridTemplateColumns: `repeat(${page.grid?.columns || panel.grid?.columns || 12}, minmax(0, 1fr))`, gap: page.grid?.gap ?? panel.grid?.gap }}>{ordered(page.components).map(c => render(c))}</div>;
}
