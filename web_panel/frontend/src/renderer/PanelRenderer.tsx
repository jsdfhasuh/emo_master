import { useEffect, useRef, useState } from 'react';
import type { Component, Panel, Result, History, Layout } from '../contracts/types';
import { preparedImageUrl } from '../state/prepare';
import { api, safeAssetUrl } from '../data/api';
import { parseLosslessJSON } from '../data/lossless';
import type { CSSProperties } from 'react';
const gridOf = (c: Component) => ({ ...(c.props || {}), ...(typeof c.props?.grid === 'object' ? c.props.grid : {}), ...c.grid }) as NonNullable<Component['grid']>;
const ordered = (components: Component[]) => [...components].sort((a,b) => (gridOf(a).row || 1) - (gridOf(b).row || 1) || (gridOf(a).column || 1) - (gridOf(b).column || 1));
export const stringify = (v: unknown, emptyText='—') => v == null || v === '' ? emptyText : typeof v === 'object' ? JSON.stringify(v) : String(v);
export const historyAggregateText = (data: object, emptyText='—') => Object.prototype.hasOwnProperty.call(data, 'value') ? stringify((data as {value:unknown}).value,emptyText) : '统计能力未就绪';
const palette:Record<string,string>={neutral:'#64748b',green:'#15803d',red:'#b91c1c',amber:'#a16207'};
const fontFamilies:Record<string,string>={system:'system-ui',sans:'sans-serif',serif:'serif',monospace:'monospace'};
export function normalizeHistory(data:History):History {
    if (!data.columns || !Array.isArray(data.rows)) return data;
    return {...data,rows:data.rows.map(row => Array.isArray(row.values) ? {
        ...row,__displayValues:Object.fromEntries(data.columns!.map((column,i)=>[column.name,(row.values as unknown[])[i]])),
    } : row)};
}
export function restoreHistorySelection(data:History, prior?:Record<string,unknown>, revision?:string):Record<string,unknown>|undefined {
    if (!data.stableRowIdentity || !prior?.rowId || revision !== data.historyDefinitionRevision) return;
    return data.rows.find(row=>row.rowId===prior.rowId);
}
function layoutStyle(layout?:Layout,columns=12):CSSProperties {
    if(layout?.type==='responsive') return {display:'flex',flexDirection:layout.direction==='vertical'?'column':'row',flexWrap:layout.direction==='wrap'?'wrap':'nowrap',gap:layout.spacing,alignItems:layout.alignment==='start'?'flex-start':layout.alignment==='end'?'flex-end':layout.alignment,justifyContent:layout.justify==='start'?'flex-start':layout.justify==='end'?'flex-end':layout.justify};
    return {gridTemplateColumns:`repeat(${layout?.columns || columns}, minmax(0, 1fr))`,gap:layout?.spacing ?? (layout as Component['grid'])?.gap};
}
function responsiveStyle(c:Component,parent?:Layout):CSSProperties {
    if(parent?.type!=='responsive')return {};
    const r=c.responsive || {};
    return {minWidth:`min(100%, ${r.minWidth ?? 160}px)`,maxWidth:'100%',minHeight:r.minHeight,height:r.preferredHeight || undefined,flexGrow:parent.equalWidth?1:r.width==='content'?0:r.weight ?? 1,flexShrink:1,flexBasis:parent.direction==='vertical'?'auto':parent.equalWidth?0:r.preferredWidth ?? 240,alignSelf:r.alignSelf==='start'?'flex-start':r.alignSelf==='end'?'flex-end':r.alignSelf as CSSProperties['alignSelf']};
}
export function fieldValue(row:unknown,path:string[]):unknown {
    let value=row;
    for(const field of path){if(value===null||typeof value!=='object'||!Object.prototype.hasOwnProperty.call(value,field))return undefined;value=(value as Record<string,unknown>)[field]}
    return value;
}
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
    const [sort, setSort] = useState(component.history?.orderBy || '');
    const [filterColumn,setFilterColumn]=useState('');
    const [order, setOrder] = useState(component.history ? component.history.descending===false?'asc':'desc' : 'asc');
    const [refresh, setRefresh] = useState(0);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Record<string, unknown>>();
    const selectedRef = useRef<Record<string,unknown> | undefined>(undefined);
    const previousContext = useRef('');
    const previousRevision = useRef<string | undefined>(undefined);
    function select(row?:Record<string,unknown>) {selectedRef.current=row;setSelected(row);onSelection(component.id,row)}
    useEffect(() => {
        let live = true; const abort = new AbortController();
        const context = JSON.stringify([viewId,component.id,page,q,sort,order,filterColumn]);
        const prior = previousContext.current === context ? selectedRef.current : undefined;
        previousContext.current = context;
        select(undefined); setData(undefined); setError('');
        api<History>(`/api/views/${encodeURIComponent(viewId)}/history/${encodeURIComponent(component.id)}?page=${page}&pageSize=${Number(component.props?.pageSize)||10}&q=${encodeURIComponent(q)}${sort ? `&sort=${encodeURIComponent(sort)}` : ''}&order=${order}&filterColumn=${encodeURIComponent(filterColumn)}`, {signal:abort.signal}).then(d => {
            if (!live) return;
            const next = normalizeHistory(d); setData(next);
            const restored = restoreHistorySelection(next,prior,previousRevision.current);
            previousRevision.current=next.historyDefinitionRevision;
            select(restored);
        }).catch(e => {if(live){setData(undefined);setError(e.message);select(undefined)}});
        return () => {live=false;abort.abort()};
    }, [viewId,component.id,page,q,sort,order,filterColumn,refresh]);
    if (error)
        return <div><p role="alert">{error}</p><button onClick={()=>{setPage(1);setQ('');setFilterColumn('');setSort(component.history?.orderBy || '');setOrder(component.history ? component.history.descending===false?'asc':'desc':'asc');setRefresh(n=>n+1)}}>重置查询并重试</button></div>;
    if (aggregate)
        return !data ? <p>正在读取历史…</p> : <strong>{historyAggregateText(data,typeof component.props?.emptyText==='string'?component.props.emptyText:undefined)}</strong>;
    const rows = data?.rows || [];
    const configured = Array.isArray(component.props?.columns) ? component.props.columns : [];
    const declared = configured.map(c => typeof c === 'string' ? {key:c,title:c} : c && typeof c === 'object' && typeof c.key === 'string' ? {key:c.key,title:typeof c.title==='string'?c.title:c.key} : undefined).filter((c):c is {key:string;title:string}=>!!c);
    const columns = declared.length ? declared.map(c=>c.key) : data?.columns?.length ? data.columns.map(c=>c.name) : rows.length ? Object.keys(rows[0]).filter(k => !['rowId','imageRefs','imageRef','imageUrl'].includes(k)) : [];
    const title = (key:string) => declared.find(c=>c.key===key)?.title || key;
    const image = selected && safeAssetUrl(selected.imageUrl ?? (typeof selected.imageRef === 'string' ? `/api/history-images/${encodeURIComponent(selected.imageRef)}` : undefined));
    return <><div className="toolbar"><label>筛选<input aria-label={`${component.id} 筛选`} value={q} onChange={e => { setPage(1); setQ(e.target.value); }}/></label><label>筛选列<select aria-label={`${component.id} 筛选列`} value={filterColumn} onChange={e=>{setPage(1);setFilterColumn(e.target.value)}}><option value="">所有声明列</option>{columns.map(k=><option key={k} value={k}>{title(k)}</option>)}</select></label><label>排序<select aria-label={`${component.id} 排序`} value={sort} onChange={e => { setPage(1); setSort(e.target.value); }}><option value="">默认</option>{columns.map(k => <option key={k} value={k}>{title(k)}</option>)}</select></label><select aria-label={`${component.id} 顺序`} value={order} onChange={e => { setPage(1); setOrder(e.target.value); }}><option value="asc">升序</option><option value="desc">降序</option></select><button onClick={() => setRefresh(n => n + 1)}>刷新历史</button></div><div className="table-scroll"><table><thead><tr>{columns.map(k => <th key={k}>{title(k)}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i} tabIndex={0} aria-selected={selected === row} onClick={() => {select(row)}} onKeyDown={e => { if (e.key === 'Enter')
        {select(row);} }}>{columns.map(k => <td key={k}>{stringify(row.__displayValues ? (row.__displayValues as Record<string,unknown>)[k] : row[k])}</td>)}</tr>)}</tbody></table></div>{!data && <p>正在读取历史…</p>}{data && !rows.length && <p>{typeof component.props?.emptyText==='string'?component.props.emptyText:'暂无记录'}</p>}<nav><button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>第 {page} 页 · 共 {data?.total ?? '—'} 条</span><button disabled={!data || (data.hasNext !== undefined ? !data.hasNext : page * data.pageSize >= (data.total ?? 0))} onClick={() => setPage(p => p + 1)}>下一页</button></nav>{selected && (image ? <SafeImage key={image} url={image} label="选中历史记录"/> : <p>选中记录无可用图片</p>)}</>;
}
function SafeImage({url,label}:{url:string;label:string}) {const [failed,setFailed]=useState(false);return failed?<p>图片读取失败或无权访问</p>:<img className="result-image" src={url} alt={label} onError={()=>setFailed(true)}/>;}
function ResultTable({value,columns,resultKey,pageSize=20,emptyText='本轮暂无表格数据'}:{value:unknown;columns:unknown;resultKey?:string;pageSize?:number;emptyText?:string}) {
 const rows=Array.isArray(value)?value:[];const[page,setPage]=useState(1);useEffect(()=>setPage(1),[resultKey]);
 const defined=Array.isArray(columns)?columns.filter((c):c is {key?:string;title?:string;fieldPath?:string[]}=>typeof c==='object'&&c!==null&&(typeof c.key==='string'||Array.isArray(c.fieldPath))):[];
 const keys=defined.length?defined:[];const size=Math.max(1,Math.min(100,pageSize));
 if(!keys.length)return <p>未声明表格列</p>;
 return <><div className="table-scroll"><table><thead><tr>{keys.map((c,i)=><th key={i}>{c.title||c.key}</th>)}</tr></thead><tbody>{rows.slice((page-1)*size,page*size).map((row,i)=><tr key={i}>{keys.map((c,j)=><td key={j}>{stringify(fieldValue(row,c.fieldPath || (c.key?[c.key]:[])),emptyText)}</td>)}</tr>)}</tbody></table></div>{!rows.length&&<p>{emptyText}</p>}{rows.length>size&&<nav><button disabled={page<=1} onClick={()=>setPage(n=>n-1)}>上一页</button><span>{page} / {Math.ceil(rows.length/size)}</span><button disabled={page*size>=rows.length} onClick={()=>setPage(n=>n+1)}>下一页</button></nav>}</>;
}
export async function navigateDisplayedResult(target:string,scope:string|undefined,displayed:boolean,onAction:(a:string,scope?:string)=>Promise<boolean>,onPage:(page:string)=>void,isCurrent:()=>boolean):Promise<boolean> {
    if(displayed && (!scope || !await onAction('freeze',scope)))return false;
    if(!isCurrent())return false;
    onPage(target);return true;
}
export function PanelRenderer({ panel, pageId, onPage, result, results, viewId, onAction, state, runtimeState }: {
    panel: Panel;
    pageId: string;
    onPage: (id: string) => void;
    result?: Result;
    results?: Record<string, Result>;
    viewId?: string;
    onAction: (a: string, scopeId?:string) => Promise<boolean>;
    state: string;
    runtimeState?: string;
}) {
    const navigationAttempt=useRef(0);
    useEffect(()=>{navigationAttempt.current++;return()=>{navigationAttempt.current++}},[viewId,pageId,panel.packageRevision]);
    const [selections,setSelections]=useState<Record<string,Record<string,unknown>|undefined>>({});
    useEffect(()=>setSelections({}),[viewId,panel.packageRevision,pageId]);
    const selectHistory=(componentId:string,row?:Record<string,unknown>)=>setSelections(old=>({...old,[componentId]:row}));
    const page = panel.pages.find(p => p.id === pageId) || panel.pages[0];
    if (!page)
        return <p>无可用页面</p>;
    function render(c: Component, columns = page.layout?.columns || page.grid?.columns || panel.grid?.columns || 12, parentLayout?:Layout) {
        const p = c.props || {};
        const emptyText=typeof p.emptyText==='string'?p.emptyText:undefined;
        const style: Record<string, unknown> = {fontSize:p.fontSize || undefined,fontWeight:p.fontWeight,color:palette[String(p.textColor)],...(p.style && typeof p.style === 'object' ? p.style as Record<string, unknown> : {})};
        const binding = typeof c.binding === 'string' ? c.binding : c.binding?.sourceId;
        const requestedScope = typeof c.binding === 'object' ? c.binding.scopeId : undefined;
        const boundResult = requestedScope ? results?.[requestedScope] : result;
        const source = binding ? boundResult?.sources[binding] : undefined;
        let value = binding ? source?.value : p.text ?? p.value;
        if (source?.valueJson !== undefined) {
            try {
                value = parseLosslessJSON(source.valueJson);
            }
            catch {
                value = source.valueJson;
            }
        }
        const field = typeof c.binding === 'object' ? c.binding.field : undefined;
        if (field && value && typeof value === 'object') value = Object.prototype.hasOwnProperty.call(value, field) ? (value as Record<string, unknown>)[field] : undefined;
        const failed = !['history_fixed','history_selection'].includes(String(p.sourceMode)) && Boolean(binding) && (!source || !['ready', 'ok', 'available'].includes(source.state.toLowerCase()));
        let body;
        switch (c.type) {
            case 'image': {
                const selected = typeof p.sourceComponentId === 'string' ? selections[p.sourceComponentId] : undefined;
                const rowRef = (selected?.imageRefs as Record<string,{status:string;imageRef?:string}> | undefined)?.[c.id];
                const historyRef = p.sourceMode === 'history_fixed' && viewId ? `/api/views/${encodeURIComponent(viewId)}/${c.history?'history-images':'images'}/${encodeURIComponent(c.id)}` : p.sourceMode === 'history_selection' && viewId && rowRef?.status === 'AVAILABLE' && rowRef.imageRef ? `/api/views/${encodeURIComponent(viewId)}/history-images/${encodeURIComponent(c.id)}?imageRef=${encodeURIComponent(rowRef.imageRef)}` : p.sourceMode === 'history_selection' && typeof selected?.imageRef === 'string' ? `/api/history-images/${encodeURIComponent(selected.imageRef)}` : undefined;
                const ref = historyRef ?? (p.sourceMode === 'history_selection' || p.sourceMode === 'history_fixed' ? undefined : source?.image?.url ?? (typeof p.assetId === 'string' ? `/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.assetId)}` : undefined));
                const url = preparedImageUrl(ref);
                body = failed ? <p>{source?.reason || '本轮图片不可用'}</p> : url ? <SafeImage key={url} url={url} label={c.label || '检测结果'}/> : <p>{emptyText ?? '暂无图片'}</p>;
                break;
            }
            case 'text':
                body = <p>{stringify(value,emptyText)}</p>;
                break;
            case 'number': {
                const digits = typeof p.decimals === 'number' ? Math.max(0, Math.min(12, p.decimals)) : 0;
                body = <strong className="number" style={{fontSize:typeof style.fontSize==='number'?'inherit':undefined}}>{typeof value === 'number' ? value.toFixed(digits) : stringify(value,emptyText)} <small>{stringify(p.unit ?? '', '')}</small></strong>;
                break;
            }
            case 'indicator': {
                const map = p.mapping && typeof p.mapping === 'object' ? p.mapping as Record<string, unknown> : {};
                const official = p.indicatorStates && typeof p.indicatorStates === 'object' && ['boolean','string'].includes(typeof value) ? (p.indicatorStates as Record<string,{text:string;color:string}>)[JSON.stringify(value)] : undefined;
                if (official) {body=<strong className="indicator" style={{color:palette[official.color]}}>{official.text}</strong>;break;}
                const mapped = map[String(value)] ?? value;
                const ok = mapped === true || mapped === 'OK';const ng=mapped===false||mapped==='NG';
                body = <strong className={`indicator ${ok?'ok':ng?'ng':'unknown'}`} style={{fontSize:typeof style.fontSize==='number'?'inherit':undefined,color:typeof style.color==='string'?'inherit':undefined}}>{ok?'OK':ng?'NG':value==null?(emptyText ?? '未知'):stringify(mapped)}</strong>;
                break;
            }
            case 'runtime_status':
                body = <p>连接：{state}<br/>运行：{runtimeState || '无运行 / 状态未就绪'}<small>（非产品判定）</small></p>;
                break;
            case 'table': {body=<ResultTable value={value} columns={p.columns} emptyText={emptyText} pageSize={Number(p.pageSize)||20} resultKey={boundResult?.resultKey}/>;break;}
            case 'navigation_button':
                body = <button onClick={async () => { const attempt=++navigationAttempt.current;const clicked=c.actions?.clicked;const a = clicked?.type || String(p.action || 'navigate'); if (a === 'navigate') {
                    const target = clicked?.pageId || String(p.targetPageId || '');
                    if (panel.pages.some(x => x.id === target)) {
                        await navigateDisplayedResult(target,clicked?.resultScopeId,clicked?.context==='displayed_result',onAction,onPage,()=>attempt===navigationAttempt.current);
                    }
                }
                else if (['freeze', 'resume_live'].includes(a))
                    onAction(a,clicked?.resultScopeId); }}>{typeof p.iconResourceId==='string' && <img className="nav-icon" src={`/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.iconResourceId)}`} alt=""/>}{typeof p.icon === 'string' && <span aria-hidden="true">{p.icon} </span>}{stringify(p.text ?? c.label ?? '打开')}</button>;
                break;
            case 'container':
                body = <div className={c.grid?.type==='responsive'?'panel-responsive':'panel-grid'} style={layoutStyle(c.grid,columns)}>{(c.grid?.type==='responsive'?c.children || []:ordered(c.children || [])).map(child => render(child, c.grid?.columns || columns,c.grid))}</div>;
                break;
            case 'page_title':
                body = <h2 style={{fontSize:typeof style.fontSize==='number'?'inherit':undefined}}>{page.title}</h2>;
                break;
            case 'page_navigation':
                body = <nav role={panel.navigation?.mode==='tabs'?'tablist':undefined} aria-label="页面导航">{panel.pages.map(x => <button key={x.id} role={panel.navigation?.mode==='tabs'?'tab':undefined} aria-selected={panel.navigation?.mode==='tabs'?x.id===page.id:undefined} aria-current={x.id === page.id ? 'page' : undefined} onClick={() => onPage(x.id)}>{x.title}</button>)}</nav>;
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
        const grid = c.layout ? {...c.layout,row:(c.layout.row || 0)+1,column:(c.layout.column || 0)+1} : gridOf(c);
        const span = Math.min(columns, Math.max(1, Number(grid.columnSpan) || Math.min(6, columns)));
        return <section className="panel-card" key={c.id} style={{ ...responsiveStyle(c,parentLayout), fontFamily:fontFamilies[String(p.fontFamily)], border:p.cardStyle==='outlined'?'1px solid #64748b':undefined, boxShadow:p.cardStyle==='soft'?'0 2px 12px #0002':undefined, fontWeight: ['normal', 'bold', '400', '500', '600', '700', 400, 500, 600, 700].includes(style.fontWeight as string | number) ? style.fontWeight as 'normal' | 'bold' | 400 | 500 | 600 | 700 : undefined, gridColumn: parentLayout?.type==='responsive'?undefined:`${grid.column || 'auto'} / span ${span}`, gridRow: parentLayout?.type==='responsive'?undefined:`${grid.row || 'auto'} / span ${Math.max(1, Number(grid.rowSpan) || 1)}`, fontSize: typeof style.fontSize === 'number' ? Math.min(128, Math.max(8, style.fontSize)) : undefined, color: typeof style.color === 'string' && /^(#[0-9a-f]{3,8}|[a-z]{1,20})$/i.test(style.color) ? style.color : undefined, backgroundColor: typeof style.backgroundColor === 'string' && /^(#[0-9a-f]{3,8}|[a-z]{1,20})$/i.test(style.backgroundColor) ? style.backgroundColor : undefined, textAlign: ['left', 'center', 'right'].includes(String(style.textAlign)) ? style.textAlign as 'left' | 'center' | 'right' : undefined }}>{(c.label||typeof p.label==='string'||(c.type==='container'&&typeof p.title==='string'))&&<h3>{c.label||(typeof p.label==='string'?p.label:String(p.title))}</h3>}{body}</section>;
    }
    return <div className={page.layout?.type==='responsive'?'panel-responsive':'panel-grid'} style={layoutStyle(page.layout || page.grid || panel.grid,12)}>{(page.layout?.type==='responsive'?page.components:ordered(page.components)).map(c => render(c,undefined,page.layout))}</div>;
}
