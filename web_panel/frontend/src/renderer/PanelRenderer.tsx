import { useEffect, useState } from 'react';
import type { Component, Panel, Result, History } from '../contracts/types';
import { preparedImageUrl } from '../state/prepare';
import { api, safeAssetUrl } from '../data/api';
const stringify = (v: unknown) => v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
function Clock({ format }: {
    format?: unknown;
}) { const [now, setNow] = useState(new Date()); useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []); return <time title="浏览器本地时区，非检测时间">{format === 'date' ? now.toLocaleDateString() : now.toLocaleString()}</time>; }
function HistoryTable({ component, viewId, aggregate }: {
    component: Component;
    viewId: string;
    aggregate: boolean;
}) {
    const [data, setData] = useState<History>();
    const [page, setPage] = useState(1);
    const [q, setQ] = useState('');
    const [sort, setSort] = useState('');
    const [order, setOrder] = useState('asc');
    const [refresh, setRefresh] = useState(0);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState<Record<string, unknown>>();
    useEffect(() => { let live = true; setSelected(undefined); setError(''); api<History>(`/api/views/${encodeURIComponent(viewId)}/history/${encodeURIComponent(component.id)}?page=${page}&q=${encodeURIComponent(q)}${sort ? `&sort=${encodeURIComponent(sort)}` : ''}&order=${order}`).then(d => { if (live)
        setData(d); }).catch(e => { if (live) {
        setData(undefined);
        setError(e.message);
    } }); return () => { live = false; }; }, [viewId, component.id, page, q, sort, order, refresh]);
    if (error)
        return <p role="alert">{error}</p>;
    if (!data)
        return <p>正在读取历史…</p>;
    if (aggregate)
        return <strong>{stringify((data as unknown as {
            value?: unknown;
        }).value ?? data.total)}</strong>;
    const columns = data.rows.length ? Object.keys(data.rows[0]).filter(k => !k.toLowerCase().includes('imageref')) : [];
    const image = selected && safeAssetUrl(selected.imageUrl ?? (typeof selected.imageRef === 'string' ? `/api/history-images/${encodeURIComponent(selected.imageRef)}` : undefined));
    return <><div className="toolbar"><label>筛选<input aria-label={`${component.id} 筛选`} value={q} onChange={e => { setPage(1); setQ(e.target.value); }}/></label><label>排序<select aria-label={`${component.id} 排序`} value={sort} onChange={e => { setPage(1); setSort(e.target.value); }}><option value="">默认</option>{columns.map(k => <option key={k}>{k}</option>)}</select></label><select aria-label={`${component.id} 顺序`} value={order} onChange={e => { setPage(1); setOrder(e.target.value); }}><option value="asc">升序</option><option value="desc">降序</option></select><button onClick={() => setRefresh(n => n + 1)}>刷新历史</button></div><div className="table-scroll"><table><thead><tr>{columns.map(k => <th key={k}>{k}</th>)}</tr></thead><tbody>{data.rows.map((row, i) => <tr key={i} tabIndex={0} aria-selected={selected === row} onClick={() => setSelected(row)} onKeyDown={e => { if (e.key === 'Enter')
        setSelected(row); }}>{columns.map(k => <td key={k}>{stringify(row[k])}</td>)}</tr>)}</tbody></table></div>{!data.rows.length && <p>暂无记录</p>}<nav><button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>第 {page} 页 · 共 {data.total} 条</span><button disabled={page * data.pageSize >= data.total} onClick={() => setPage(p => p + 1)}>下一页</button></nav>{selected && (image ? <img className="result-image" src={image} alt="选中历史记录"/> : <p>选中记录无可用图片</p>)}</>;
}
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
    const page = panel.pages.find(p => p.id === pageId) || panel.pages[0];
    if (!page)
        return <p>无可用页面</p>;
    function render(c: Component) {
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
        const failed = Boolean(binding) && (!source || !['ready', 'ok', 'available'].includes(source.state));
        let body;
        switch (c.type) {
            case 'image': {
                const ref = source?.image?.url ?? (typeof p.assetId === 'string' ? `/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.assetId)}` : undefined);
                const url = preparedImageUrl(ref);
                body = failed ? <p>{source?.reason || '本轮图片不可用'}</p> : url ? <img className="result-image" src={url} alt={c.label || '检测结果'}/> : <p>暂无图片</p>;
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
                    onAction(a); }}>{stringify(p.text ?? c.label ?? '打开')}</button>;
                break;
            case 'container':
                body = <div className="panel-grid">{c.children?.map(render)}</div>;
                break;
            case 'page_title':
                body = <h2>{page.title}</h2>;
                break;
            case 'page_navigation':
                body = <nav>{panel.pages.map(x => <button key={x.id} aria-current={x.id === page.id ? 'page' : undefined} onClick={() => onPage(x.id)}>{x.title}</button>)}</nav>;
                break;
            case 'clock':
                body = <Clock format={p.format}/>;
                break;
            case 'sqlite_table':
            case 'sqlite_value':
                body = viewId ? <HistoryTable key={`${viewId}:${c.id}`} component={c} viewId={viewId} aggregate={c.type === 'sqlite_value'}/> : <p>请先选择查看会话</p>;
                break;
            default: body = <p role="alert">不支持的组件：{c.type}</p>;
        }
        if (failed && ['image', 'text', 'number', 'indicator', 'table'].includes(c.type)) body = <p role="status">不可用：{source?.reason || '本轮数据未就绪'}</p>;
        const style = p.style && typeof p.style === 'object' ? p.style as Record<string, unknown> : {};
        const span = Math.min(12, Math.max(1, Number(c.grid?.columnSpan) || 6));
        return <section className="panel-card" key={c.id} style={{ fontWeight: ['normal', 'bold', 400, 700].includes(style.fontWeight as string | number) ? style.fontWeight as 'normal' | 'bold' | 400 | 700 : undefined, gridColumn: `${c.grid?.column || 'auto'} / span ${span}`, gridRow: `${c.grid?.row || 'auto'} / span ${Math.max(1, Number(c.grid?.rowSpan) || 1)}`, fontSize: typeof style.fontSize === 'number' ? Math.min(72, Math.max(10, style.fontSize)) : undefined, color: typeof style.color === 'string' && /^#[0-9a-f]{3,8}$/i.test(style.color) ? style.color : undefined, backgroundColor: typeof style.backgroundColor === 'string' && /^#[0-9a-f]{3,8}$/i.test(style.backgroundColor) ? style.backgroundColor : undefined, textAlign: ['left', 'center', 'right'].includes(String(style.textAlign)) ? style.textAlign as 'left' | 'center' | 'right' : undefined }}>{(c.label||typeof p.label==='string')&&<h3>{c.label||String(p.label)}</h3>}{body}</section>;
    }
    return <div className="panel-grid">{page.components.map(render)}</div>;
}
