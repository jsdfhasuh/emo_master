import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { Component, Panel, Result, Layout, Typography } from '../contracts/types';
import { preparedImageUrl } from '../state/prepare';
import { parseLosslessJSON } from '../data/lossless';
import type { ComponentProps, CSSProperties, ReactNode } from 'react';
import { ImageView, IndicatorTitle, IndicatorValue, PageNavigation, indicatorState, indicatorSurface, pageAppearance, roleStyle, surfaceStyle } from './presentation';
import { HistoryImage, HistoryTable, historyAction, useHistoryReview } from './HistoryView';
import type { RecordReference } from './HistoryView';
import { AppearanceCss, appearanceBorder, appearancePart, buttonStateStyle } from './advancedAppearance';
import * as theme from '../../../contracts/panel-contract-v1/theme.json';
const gridOf = (c: Component) => ({ ...(c.props || {}), ...(typeof c.props?.grid === 'object' ? c.props.grid : {}), ...c.grid }) as NonNullable<Component['grid']>;
const ordered = (components: Component[]) => [...components].sort((a,b) => (gridOf(a).row || 1) - (gridOf(b).row || 1) || (gridOf(a).column || 1) - (gridOf(b).column || 1));
export const stringify = (v: unknown, emptyText='—') => v == null || v === '' ? emptyText : typeof v === 'object' ? JSON.stringify(v) : String(v);
function layoutStyle(layout?:Layout,columns=12):CSSProperties {
    if(layout?.type==='responsive') return {display:'flex',flexDirection:layout.direction==='vertical'?'column':'row',flexWrap:layout.direction==='vertical'?'nowrap':'wrap',gap:layout.spacing,alignItems:layout.alignment==='start'?'flex-start':layout.alignment==='end'?'flex-end':layout.alignment,justifyContent:layout.justify==='start'?'flex-start':layout.justify==='end'?'flex-end':layout.justify};
    return {gridTemplateColumns:`repeat(${layout?.columns || columns}, minmax(0, 1fr))`,gridAutoRows:'auto',gap:layout?.spacing ?? (layout as Component['grid'])?.gap};
}
export function responsiveStyle(c:Component,parent?:Layout,hasVerticalSpacer=false,equalWidthBasis=0):CSSProperties {
    if(parent?.type!=='responsive')return {};
    const r=c.responsive || {};
    const canvasPosition:CSSProperties=r.canvasOffsetY?{position:'relative',top:r.canvasOffsetY}:{};
    if(c.type==='spacer') {
        const width=Math.max(r.minWidth ?? 24,r.preferredWidth || 24);
        const height=Math.max(r.minHeight ?? 24,r.preferredHeight ?? 24);
        const flex=c.props?.spacerMode==='flex';
        const vertical=parent.direction==='vertical';
        const alignment=!r.alignSelf || r.alignSelf==='auto'?parent.alignment:r.alignSelf;
        return {...canvasPosition,width,maxWidth:'100%',height,minWidth:0,minHeight:r.minHeight ?? 24,
            flexGrow:flex?r.weight ?? 1:0,flexShrink:vertical?0:1,
            flexBasis:vertical?height:parent.direction==='horizontal' && flex?r.minWidth ?? 24:width,
            alignSelf:alignment==='center'?'center':alignment==='end'?'flex-end':'flex-start'};
    }
    // Preferred height is a lower bound; content still determines its natural height.
    const minHeight=r.preferredHeight?Math.max(r.minHeight ?? 0,r.preferredHeight):r.minHeight;
    const minWidth = r.minWidth ?? 0;
    const flexBasis = parent.direction === 'vertical' ? 'auto'
        : parent.equalWidth ? equalWidthBasis
        : r.width === 'content' ? r.preferredWidth || 'auto'
        : parent.direction === 'horizontal' ? minWidth
        : r.preferredWidth || minWidth;
    return {...canvasPosition,minWidth:`min(100%, ${minWidth}px)`,maxWidth:'100%',minHeight,
        flexGrow:hasVerticalSpacer?0:parent.equalWidth?1:r.width==='content'?0:r.weight ?? 1,flexShrink:hasVerticalSpacer?0:1,
        flexBasis,alignSelf:r.alignSelf==='start'?'flex-start':r.alignSelf==='end'?'flex-end':r.alignSelf as CSSProperties['alignSelf']};
}
export function fieldValue(row:unknown,path:string[]):unknown {
    let value=row;
    for(const field of path){if(value===null||typeof value!=='object'||!Object.prototype.hasOwnProperty.call(value,field))return undefined;value=(value as Record<string,unknown>)[field]}
    return value;
}
function Clock({ format, timezone, owner, typography }: {
    format?: unknown; timezone?: unknown; owner: string; typography?: Typography;
}) {
 const [now,setNow]=useState(new Date());useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(timer)},[]);
 const zone=typeof timezone==='string'&&timezone!=='browser'?timezone:undefined;
 const value=appearancePart(owner,'value',typography?.value && roleStyle(typography,'value'));
 if(format!==undefined&&!['date','time','datetime'].includes(String(format)))return <p {...value}>不支持的时钟格式</p>;
 try{const options:Intl.DateTimeFormatOptions={timeZone:zone,...(format==='date'?{dateStyle:'medium' as const}:format==='time'?{timeStyle:'medium' as const}:{dateStyle:'medium' as const,timeStyle:'medium' as const})};return <time {...value} title={`时区：${zone||'浏览器本地'}，非检测时间`}>{new Intl.DateTimeFormat(undefined,options).format(now)}<small {...appearancePart(owner,'helper',typography?.helper && roleStyle(typography,'helper'))}> · {zone||'浏览器本地时区'}</small></time>}catch{return <p {...value}>时区配置无效</p>}
}
/** Runtime's declared collection envelopes, not arbitrary JSON object guessing. */
export function resultRows(value:unknown):unknown[]|undefined {
 if(Array.isArray(value))return value;
 if(value==null || value==='')return [];
 if(typeof value!=='object')return;
 const payload=value as Record<string,unknown>;
 if(!['1.0','1.1','1.2'].includes(String(payload.schemaVersion)))return;
 const visionCollections=['blobCollection','detectionCollection','contourCollection','shapeMeasurementCollection','lineCollection','circleCollection','templateMatchCollection'];
 if(visionCollections.includes(String(payload.type)) && Array.isArray(payload.items))return payload.items;
 if(payload.type==='plcValueCollection' && Array.isArray(payload.values))return payload.values;
}
function ResultTable({value,columns,resultKey,pageSize=20,emptyText='本轮暂无表格数据',owner,typography,sample}:{
    value:unknown;columns:unknown;resultKey?:string;pageSize?:number;emptyText?:string;owner:string;typography?:Typography;sample?:boolean;
}) {
 const rows=resultRows(value);const[page,setPage]=useState(1);useEffect(()=>setPage(1),[resultKey]);
 const defined=Array.isArray(columns)?columns.filter((c):c is {key?:string;title?:string;fieldPath?:string[]}=>typeof c==='object'&&c!==null&&(typeof c.key==='string'||Array.isArray(c.fieldPath))):[];
 const keys=defined.length?defined:[];const size=Math.max(1,Math.min(100,pageSize));
 const helper=appearancePart(owner,'helper',typography?.helper && roleStyle(typography,'helper'));
 if(!keys.length)return <p {...helper}>未声明表格列</p>;
 if(!rows)return <p role="status" {...helper}>不支持此集合结果结构，请选择已声明的集合输出或字段投影</p>;
 const cell=appearancePart(owner,'cell',typography?.value && roleStyle(typography,'value'));
 const header=appearancePart(owner,'cell',typography?.title && roleStyle(typography,'title'));
 const displayedRows=rows.length || !sample ? rows : [{}];
 return <>
     <div className="table-scroll" {...appearancePart(owner,'viewport')}>
         <table {...appearancePart(owner,'table')}>
             <thead {...appearancePart(owner,'header')}><tr>{keys.map((c,i)=><th key={i} {...header}>{c.title||c.key}</th>)}</tr></thead>
             <tbody>{displayedRows.slice((page-1)*size,page*size).map((row,i)=><tr key={i}>
                 {keys.map((c,j)=><td key={j} {...cell}>{stringify(fieldValue(row,c.fieldPath || (c.key?[c.key]:[])),sample?'示例':emptyText)}</td>)}
             </tr>)}</tbody>
         </table>
     </div>
     {!displayedRows.length&&<p {...helper}>{emptyText}</p>}
     {rows.length>size&&<nav>
         <button {...appearancePart(owner,'action')} disabled={page<=1} onClick={()=>setPage(n=>n-1)}>上一页</button>
         <span>{page} / {Math.ceil(rows.length/size)}</span>
         <button {...appearancePart(owner,'action')} disabled={page*size>=rows.length} onClick={()=>setPage(n=>n+1)}>下一页</button>
     </nav>}
 </>;
}
export async function navigateDisplayedResult(target:string,scope:string|undefined,displayed:boolean,onAction:(a:string,scope?:string)=>Promise<boolean>,onPage:(page:string)=>void,isCurrent:()=>boolean):Promise<boolean> {
    if(displayed && (!scope || !await onAction('freeze',scope)))return false;
    if(!isCurrent())return false;
    onPage(target);return true;
}
interface AuthoringCanvas {
    card: (component: Component, props: ComponentProps<'section'>) => ReactNode;
    container: (component: Component, props: ComponentProps<'div'>) => ReactNode;
    page: Pick<ComponentProps<'div'>, 'onClick' | 'onDragOver' | 'onDrop'>;
    empty: ReactNode;
}

export function PanelRenderer({ panel, pageId, onPage, result, results, viewId, onAction, state, runtimeState,
    viewportHeight, sample=false, sampleIndicatorKey, previewOnly=false, previewDisabled=false, authoring }: {
    panel: Panel;
    pageId: string;
    onPage: (id: string) => void;
    result?: Result;
    results?: Record<string, Result>;
    viewId?: string;
    onAction: (a: string, scopeId?:string) => Promise<boolean>;
    state: string;
    runtimeState?: string;
    /** Whole fixed preview height, including configured page padding. */
    viewportHeight?: number;
    sample?: boolean;
    sampleIndicatorKey?: string;
    previewOnly?: boolean;
    previewDisabled?: boolean;
    authoring?: AuthoringCanvas;
}) {
    const instance = useId();
    const navigationAttempt=useRef(0);
    const pageRoot=useRef<HTMLDivElement>(null);
    const [availableHeight,setAvailableHeight]=useState<number>();
    const page = panel.pages.find(p => p.id === pageId) || panel.pages[0];
    const hasVerticalSpacer=(components:Component[],layout?:Layout)=>layout?.type==='responsive' && layout.direction==='vertical' && components.some(c=>c.type==='spacer' && c.props?.spacerMode==='flex');
    const measureHeight=Boolean(page && (hasVerticalSpacer(page.components,page.layout) || page.canvasTop));
    const pagePadding=panel.appearance?.pagePadding ?? 24;
    const layoutHeight=viewportHeight===undefined?availableHeight:Math.max(0,viewportHeight-2*pagePadding);
    useLayoutEffect(()=>{
        const element=pageRoot.current;
        if(!element || !measureHeight || viewportHeight!==undefined)return;
        const measure=()=>setAvailableHeight(Math.max(0,window.innerHeight-element.getBoundingClientRect().top-2*pagePadding));
        measure();window.addEventListener('resize',measure);
        const observer=new ResizeObserver(measure);observer.observe(element.parentElement || element);
        return()=>{window.removeEventListener('resize',measure);observer.disconnect()};
    },[pageId,panel.packageRevision,viewportHeight,pagePadding,measureHeight]);
    useEffect(()=>{navigationAttempt.current++;return()=>{navigationAttempt.current++}},[viewId,pageId,panel.packageRevision]);
    const historyScope = JSON.stringify([panel.runtimeId, panel.runtimeInstanceId, panel.projectId,
        panel.historyDefinitionRevision || panel.packageRevision]);
    const [collapsedGroups,setCollapsedGroups]=useState<Record<string,boolean>>({});
    useEffect(()=>setCollapsedGroups({}),[historyScope]);
    const history = useHistoryReview(viewId, historyScope, panel.packageRevision, page);
    if (!page)
        return <p>无可用页面</p>;
    const equalWidthBasis=(components:Component[],layout?:Layout)=>layout?.equalWidth
        ? Math.max(0,...components.filter(c=>c.type!=='spacer').map(c=>layout.direction === 'horizontal' && c.responsive?.width !== 'content'
            ? c.responsive?.minWidth ?? 0 : Math.max(c.responsive?.minWidth ?? 0,c.responsive?.preferredWidth ?? 0)))
        : 0;
    function render(c: Component, columns = page.layout?.columns || page.grid?.columns || panel.grid?.columns || 12, parentLayout?:Layout,verticalSpacer=false,equalBasis=0) {
        const p = c.props || {};
        const owner = `${instance}:${JSON.stringify([page.id,c.id])}`;
        const card = (props: ComponentProps<'section'>) => authoring ? authoring.card(c, props) : <section {...props}/>;
        if(c.type==='spacer') {
            const grid=c.layout?{...c.layout,row:(c.layout.row || 0)+1,column:(c.layout.column || 0)+1}:gridOf(c);
            const style = parentLayout?.type==='responsive'?responsiveStyle(c,parentLayout):{minWidth:0,minHeight:c.responsive?.minHeight ?? 24,gridColumn:`${grid.column || 'auto'} / span ${grid.columnSpan || 1}`,gridRow:`${grid.row || 'auto'} / span ${grid.rowSpan || 1}`};
            return authoring ? <Fragment key={c.id}>{card({style})}</Fragment>
                : <div key={c.id} data-component-id={c.id} className="panel-spacer" aria-hidden="true" style={style}/>;
        }
        const emptyText=typeof p.emptyText==='string'?p.emptyText:undefined;
        const title = c.label || (typeof p.title === 'string' ? p.title : '');
        const binding = typeof c.binding === 'string' ? c.binding : c.binding?.sourceId;
        const requestedScope = typeof c.binding === 'object' ? c.binding.scopeId : undefined;
        const boundResult = requestedScope ? results?.[requestedScope] : result;
        const source = binding ? boundResult?.sources[binding] : undefined;
        let value = binding ? source?.value : p.text ?? p.value;
        if (source?.valueJson !== undefined) {
            try {
                value = parseLosslessJSON(source.valueJson, c.type === 'indicator');
            }
            catch {
                value = source.valueJson;
            }
        }
        const field = typeof c.binding === 'object' ? c.binding.field : undefined;
        if (field && value && typeof value === 'object') value = Object.prototype.hasOwnProperty.call(value, field) ? (value as Record<string, unknown>)[field] : undefined;
        const recordValue = p.recordValue as RecordReference | undefined;
        if (recordValue) value = history.value(recordValue);
        if (sample && value == null && c.type === 'number') value = 10;
        const failed = !['history_fixed','history_selection'].includes(String(p.sourceMode)) && Boolean(binding) && (!source || !['ready', 'ok', 'available'].includes(source.state.toLowerCase()));
        const stacked = p.valueLayout !== 'horizontal';
        const inlineTitle = ['text','number','indicator','runtime_status'].includes(c.type);
        const titlePart = appearancePart(owner,'title',roleStyle(p.typography,'title'));
        const valuePart = appearancePart(owner,'value',roleStyle(p.typography,'value'));
        const helperPart = appearancePart(owner,'helper',roleStyle(p.typography,'helper'));
        const label = <span className="panel-field-label" {...titlePart}>{title}</span>;
        function fieldBody(content:React.ReactNode,horizontal=!stacked,fieldLabel:React.ReactNode=label) {
            return <>
                <div className={`panel-field ${horizontal?'panel-field-horizontal':''}`}>
                    {title && fieldLabel}
                    {content}
                </div>
                {typeof p.helperText === 'string' && p.helperText && <p className="panel-field-helper" {...helperPart}>{p.helperText}</p>}
            </>;
        }
        let surface = surfaceStyle(c.type,p.surface);
        let body;
        switch (c.type) {
            case 'image': {
                if (p.sourceMode === 'history_selection' && viewId) {
                    body = <HistoryImage owner={owner} component={c} review={history} viewId={viewId} label={title || '历史图片'}/>;
                    break;
                }
                const ref = p.sourceMode === 'history_fixed' && viewId
                    ? `/api/views/${encodeURIComponent(viewId)}/${c.history ? 'history-images' : 'images'}/${encodeURIComponent(c.id)}`
                    : source?.image?.url ?? (typeof p.assetUrl === 'string'?p.assetUrl:typeof p.assetId === 'string' ? `/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.assetId)}` : undefined);
                const url = preparedImageUrl(ref);
                body = <ImageView key={url} url={failed ? undefined : url} label={title || '检测结果'} settings={p.imageView} owner={owner} typography={p.typography}
                    fixedHeight={c.responsive?.preferredHeight || c.responsive?.minHeight}
                    placeholder={failed ? source?.reason || '本轮图片不可用' : emptyText ?? '暂无图片'}/>;
                break;
            }
            case 'text':
                body = fieldBody(<p className="panel-field-value" {...valuePart}>{stringify(value,emptyText)}</p>);
                break;
            case 'number': {
                const digits = typeof p.decimals === 'number' ? Math.max(0, Math.min(12, p.decimals)) : 0;
                body = fieldBody(<span className="panel-field-value number panel-number" {...valuePart}>
                    {typeof value === 'number' ? value.toFixed(digits) : recordValue && typeof value !== 'bigint' ? stringify(undefined,emptyText) : stringify(value,emptyText)}
                    {value != null && (!recordValue || typeof value === 'number' || typeof value === 'bigint')
                        && typeof p.unit === 'string' && p.unit && <> <small {...helperPart}>{p.unit}</small></>}
                </span>);
                break;
            }
            case 'indicator': {
                const numericKey = typeof value === 'bigint' ? String(value) : undefined;
                const typedNumber = recordValue?.kind === 'field' || source?.valueJson !== undefined;
                const indicatorValue = failed || (typedNumber && typeof value === 'number') ? undefined : value;
                const state = indicatorState(p, indicatorValue, sampleIndicatorKey ?? (failed ? undefined : numericKey))
                    ?? (sample ? Object.values(p.indicatorStates ?? {})[0] : undefined);
                const row = p.indicatorView === 'row';
                if (row) surface = indicatorSurface(state,surface);
                body = fieldBody(<span title={failed ? source?.reason || '本轮数据未就绪' : undefined}>
                    <IndicatorValue owner={owner} props={p} state={state} emptyText={emptyText} hasTitle={Boolean(title)}/>
                </span>,row || !stacked,<IndicatorTitle owner={owner} props={p} state={state} title={title}/>);
                break;
            }
            case 'runtime_status': {
                const status = binding ? failed ? undefined : value : p.statusField === 'job_state' ? runtimeState || '无运行' : state || '连接不可用';
                body = fieldBody(<p className="panel-field-value" {...valuePart}>{stringify(status,emptyText)}</p>);
                break;
            }
            case 'table': {body=<ResultTable owner={owner} typography={p.typography} sample={sample} value={value} columns={p.columns} emptyText={emptyText} pageSize={Number(p.pageSize)||20} resultKey={boundResult?.resultKey}/>;break;}
            case 'navigation_button': {
                const clicked = c.actions?.clicked;
                const a = clicked?.type || String(p.action || 'navigate');
                const reviewAction = historyAction(a) ? a : undefined;
                const style = String(p.buttonStyle ?? 'secondary');
                const iconUrl = typeof p.iconUrl === 'string' ? p.iconUrl : typeof p.iconResourceId === 'string'
                    ? `/api/assets/${encodeURIComponent(panel.id)}/${encodeURIComponent(p.iconResourceId)}` : undefined;
                const icon = iconUrl ? <img className="nav-icon emo-advanced-icon" src={iconUrl} alt=""
                    style={{width:p.iconSize ?? 24,height:p.iconSize ?? 24}}/>
                    : typeof p.icon === 'string' ? <span className="emo-advanced-icon" aria-hidden="true" style={{fontSize:p.iconSize}}>{p.icon}</span> : null;
                const base = {
                    ...(p.typography?.value && roleStyle(p.typography,'value')),
                    ...(p.buttonStates && {
                        backgroundColor: style==='primary' ? theme.primary : style==='link' ? 'transparent' : theme.surface,
                        color: p.typography?.value?.color ? roleStyle(p.typography,'value').color : style==='primary' ? 'white' : style==='link' ? theme.primary : theme.text,
                        ...appearanceBorder(undefined, style==='link' ? 0 : 1, style==='primary' ? theme.primary : theme.border),
                    }),
                };
                body = <button className={`panel-action panel-action-${style}`}
                    {...appearancePart(owner,'action',base,{
                        ...buttonStateStyle(p.buttonStates), flexDirection:p.iconPosition==='right' ? 'row-reverse' : undefined, gap:p.iconGap,
                    })} data-emo-button-states={p.buttonStates ? '' : undefined}
                    disabled={previewOnly ? previewDisabled : !authoring && reviewAction ? !history.canReview(clicked?.componentId, reviewAction) : undefined}
                    onClick={previewOnly || authoring ? undefined : async () => {
                        if (reviewAction) {history.run(clicked?.componentId, reviewAction); return;}
                        const attempt=++navigationAttempt.current;
                        if (a === 'navigate') {
                    const target = clicked?.pageId || String(p.targetPageId || '');
                    if (panel.pages.some(x => x.id === target)) {
                        await navigateDisplayedResult(target,clicked?.resultScopeId,clicked?.context==='displayed_result',onAction,onPage,()=>attempt===navigationAttempt.current);
                    }
                }
                else if (['freeze', 'resume_live'].includes(a))
                    onAction(a,clicked?.resultScopeId); }}>{icon}{String(p.text || c.label || '打开')}</button>;
                break;
            }
            case 'container': {
                const props: ComponentProps<'div'> = {
                    className: c.grid?.type==='responsive'?'panel-responsive':'panel-grid',
                    style: {...layoutStyle(c.grid,columns),...(hasVerticalSpacer(c.children || [],c.grid)?{flex:1,minHeight:0}:{} )},
                    children: (c.grid?.type==='responsive'?c.children || []:ordered(c.children || [])).map(child =>
                        render(child, c.grid?.columns || columns,c.grid,hasVerticalSpacer(c.children || [],c.grid),equalWidthBasis(c.children || [],c.grid))),
                };
                body = authoring ? authoring.container(c, props) : <div {...props}/>;
                break;
            }
            case 'page_title':
                body = <h2 {...titlePart}>{page.title}</h2>;
                break;
            case 'page_navigation':
                body = <PageNavigation owner={owner} typography={p.typography} panel={panel} pageId={page.id} onPage={onPage}/>;
                break;
            case 'clock':
                body = <Clock owner={owner} typography={p.typography} format={p.format} timezone={p.timezone}/>;
                break;
            case 'sqlite_table':
            case 'sqlite_value':
                body = viewId || sample ? <HistoryTable owner={owner} sample={sample && !viewId} key={`${viewId}:${c.id}`} component={c} review={history}/>
                    : <p {...appearancePart(owner,c.type==='sqlite_value'?'value':'helper',roleStyle(p.typography,c.type==='sqlite_value'?'value':'helper'))}>请先选择查看会话</p>;
                break;
            default: body = <p role="alert">不支持的组件：{c.type}</p>;
        }
        if (failed && !sample && ['text', 'number', 'table'].includes(c.type)) {
            const unavailable = <p role="status" {...(inlineTitle ? valuePart : helperPart)}>不可用：{source?.reason || '本轮数据未就绪'}</p>;
            body = inlineTitle ? fieldBody(unavailable) : unavailable;
        }
        const grid = c.layout ? {...c.layout,row:(c.layout.row || 0)+1,column:(c.layout.column || 0)+1} : gridOf(c);
        const span = Math.min(columns, Math.max(1, Number(grid.columnSpan) || Math.min(6, columns)));
        const collapse = c.type === 'container' ? p.collapse : undefined;
        const collapseKey = JSON.stringify([historyScope,page.id,c.id]);
        const collapsed = collapsedGroups[collapseKey] ?? collapse?.initiallyCollapsed ?? false;
        const stretches = c.type === 'container' && hasVerticalSpacer(c.children || [],c.grid);
        const root = appearancePart(owner,'root',{
            ...surface, color:theme.text, fontFamily:'system-ui,-apple-system,"Segoe UI",sans-serif', fontSize:16, fontWeight:'normal', lineHeight:'normal',
        },{
            ...responsiveStyle(c,parentLayout,verticalSpacer,equalBasis),
            ...(stretches ? {display:'flex',flexDirection:'column'} : {}),
            ...(collapsed ? {minHeight:0} : {}),
            ...(parentLayout?.type !== 'responsive' ? {
                gridColumn:`${grid.column || 'auto'} / span ${span}`,
                gridRow:`${grid.row || 'auto'} / span ${Math.max(1, Number(grid.rowSpan) || 1)}`,
            } : {}),
        });
        return <Fragment key={c.id}>
            <AppearanceCss owner={owner} branch={p.customStyle?.webCss}/>
            {card({...root, className:'panel-card', children:<>
            {collapse ? <>
                <button className="panel-collapse" aria-expanded={!collapsed} {...appearancePart(owner,'action',roleStyle(p.typography,'title'))}
                    onClick={()=>setCollapsedGroups(old=>({...old,[collapseKey]:!collapsed}))}>
                    <span aria-hidden="true">{collapsed?'▸':'▾'}</span>{title || '详细内容'}
                </button>
                <div hidden={collapsed} className="panel-collapse-content">{body}</div>
            </> : <>
                {!inlineTitle && !['navigation_button','page_navigation','page_title'].includes(c.type) && title && <h3 {...titlePart}>{title}</h3>}
                {body}
            </>}
            </>, ...{'data-component-id':c.id}})}
        </Fragment>;
    }
    const extension=page.layout?.type==='responsive'?-(page.canvasTop || 0):0;
    const content=<div className={page.layout?.type==='responsive'?'panel-responsive':'panel-grid'} style={{...layoutStyle(page.layout || page.grid || panel.grid,12),...(hasVerticalSpacer(page.components,page.layout)?{height:layoutHeight}:{})}}>{(page.layout?.type==='responsive'?page.components:ordered(page.components)).map(c => render(c,undefined,page.layout,hasVerticalSpacer(page.components,page.layout),equalWidthBasis(page.components,page.layout)))}</div>;
    // Keep each responsive flow slot; only the moved root card is offset upward.
    // Measure the outer viewport so expansion never consumes flexible space twice.
    return <div ref={pageRoot} {...authoring?.page} className={authoring ? 'editor-canvas panel-page' : 'panel-page'}
        data-canvas-top={extension ? page.canvasTop : undefined} style={pageAppearance(panel.appearance)}>
        {!page.components.length && authoring ? authoring.empty
            : extension ? <div style={{paddingTop:extension,minHeight:(layoutHeight || 0)+extension}}>{content}</div> : content}
    </div>;
}
