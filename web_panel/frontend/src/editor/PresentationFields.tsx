import type { ReactNode } from 'react';
import type { Appearance, Component, ImageView, PanelShell, Surface, TextStyle, Typography } from '../contracts/types';
import type { Presentation, SourceComponent, SourcePage } from './model';
import { componentNames, walk } from './model';

const defaultOption = '继承默认';
type Option = string | { value: string; label: string };
function SelectField({label, value = '', options, onChange, empty}: {
    label:string; value?:string; options:readonly Option[]; empty?:string; onChange:(value:string)=>void;
}) {
    return <label>{label}<select aria-label={label} value={value} onChange={event=>onChange(event.target.value)}>
        {empty && <option value="">{empty}</option>}
        {options.map(option=>typeof option === 'string'
            ? <option key={option} value={option}>{option}</option>
            : <option key={option.value} value={option.value}>{option.label}</option>)}
    </select></label>;
}

function NumericField({label,value,min,max,onChange,step=1}: {
    label:string; value:number | undefined; min:number; max?:number; onChange:(value:number|undefined)=>void; step?:number | 'any';
}) {
    return <label>{label}<input type="number" aria-label={label} placeholder={defaultOption} step={step} min={min} max={max}
        value={value ?? ''} onChange={event=>{
            const raw=event.target.value;
            if (raw === '') onChange(undefined);
            else if (Number.isFinite(Number(raw))) onChange(Math.min(max ?? Infinity,Math.max(min, step === 1 ? Math.round(Number(raw)) : Number(raw))));
        }}/></label>;
}

function OverrideGroup({label,value,enable,reset,children}: {
    label:string; value:unknown; enable:()=>void; reset:()=>void; children?:ReactNode;
}) {
    return <fieldset><legend>{label}</legend>
        <SelectField label={`${label}配置`} value={value ? 'custom' : 'default'} onChange={choice=>choice === 'custom' ? enable() : reset()}
            options={[{value:'default',label:'使用当前默认'},{value:'custom',label:'自定义'}]}/>
        {Boolean(value) && children}
    </fieldset>;
}

export function PagePresentationFields({doc,edit,uploadLogo,busy}: {
    doc:Presentation; edit:(change:(next:Presentation)=>void)=>void; uploadLogo:(file:File)=>void; busy:boolean;
}) {
    const appearance = doc.appearance;
    const shell = doc.shell;
    const changeAppearance = (patch:Appearance) => edit(next=>{next.appearance={...next.appearance,...patch};});
    const changeShell = (patch:PanelShell) => edit(next=>{next.shell={...next.shell,...patch};});
    return <>
        <SelectField label="页签样式" value={doc.navigation.mode} options={[{value:'tabs',label:'页签'},{value:'buttons',label:'按钮'}]}
            onChange={mode=>edit(next=>{next.navigation.mode=mode as 'tabs'|'buttons';})}/>
        <OverrideGroup label="页面外观" value={appearance} enable={()=>changeAppearance({})} reset={()=>edit(next=>{delete next.appearance;})}>
            <NumericField label="内容最大宽度（0 为不限）" value={appearance?.contentMaxWidth ?? 0} min={0} max={4096}
                onChange={contentMaxWidth=>changeAppearance({contentMaxWidth})}/>
            <NumericField label="页面边距" value={appearance?.pagePadding ?? 24} min={0} max={64}
                onChange={pagePadding=>changeAppearance({pagePadding})}/>
        </OverrideGroup>
        <OverrideGroup label="运行页头" value={shell} enable={()=>changeShell({})} reset={()=>edit(next=>{delete next.shell;})}>
            <label>页头标题<input aria-label="页头标题" placeholder="使用工程名称" maxLength={1024} value={shell?.title || ''}
                onChange={event=>changeShell({title:event.target.value})}/></label>
            <label>页头副标题<input maxLength={1024} value={shell?.subtitle || ''} onChange={event=>changeShell({subtitle:event.target.value})}/></label>
            <label>上传页头图片<input type="file" accept="image/png,image/jpeg,image/bmp" disabled={busy}
                onChange={event=>{const file=event.target.files?.[0];if(file)uploadLogo(file);event.target.value='';}}/></label>
            {shell?.logoResourceId && <button onClick={()=>changeShell({logoResourceId:undefined})}>移除页头图片</button>}
            {([
                ['showConnection','显示连接状态',true],['showJobState','显示任务状态',true],
                ['showControls','显示宿主控制',true],['diagnosticsInitiallyExpanded','初始展开诊断',false],
            ] as const).map(([key,label,fallback])=><label key={key}>{label}<input type="checkbox" checked={shell?.[key] ?? fallback}
                onChange={event=>changeShell({[key]:event.target.checked})}/></label>)}
        </OverrideGroup>
    </>;
}

export function ComponentPresentationFields({component,update}: {
    component:SourceComponent; update:(change:(component:SourceComponent)=>void)=>void;
}) {
    const { props, type } = component;
    const surface=props.surface as Surface|undefined;
    const typography=props.typography as Typography|undefined;
    const image=props.imageView as ImageView|undefined;
    const set = (key:string,value:unknown) => update(item=>{if (value === undefined) delete item.props[key];else item.props[key]=value;});
    const setSurface = (patch:Surface) => set('surface',{...surface,...patch});
    const setRole = (role:keyof Typography,patch:TextStyle) => set('typography',{...typography,[role]:{...typography?.[role],...patch}});
    const setImage = (patch:ImageView) => set('imageView',{...image,...patch});
    return <>
        {type !== 'spacer' && <OverrideGroup label="表面" value={surface} enable={()=>setSurface({})} reset={()=>set('surface',undefined)}>
            <SelectField label="表面类型" value={surface?.kind} empty={defaultOption} options={['none','card']}
                onChange={kind=>setSurface({kind:kind as Surface['kind'] || undefined})}/>
            <SelectField label="背景" value={surface?.background} empty={defaultOption} options={['transparent','surface','subtle']}
                onChange={background=>setSurface({background:background as Surface['background'] || undefined})}/>
            <SelectField label="边框" value={surface?.border} empty={defaultOption} options={['none','subtle']}
                onChange={border=>setSurface({border:border as Surface['border'] || undefined})}/>
            <NumericField label="圆角" value={surface?.radius} min={0} max={32} onChange={radius=>setSurface({radius})}/>
            {(['top','right','bottom','left'] as const).map((side,index)=><NumericField key={side}
                label={`${['上','右','下','左'][index]}内边距`} value={surface?.padding?.[side]} min={0} max={64}
                onChange={value=>setSurface({padding:{...surface?.padding,[side]:value}})}/>)}
        </OverrideGroup>}
        {type !== 'spacer' && <OverrideGroup label="文字角色" value={typography} enable={()=>set('typography',{})} reset={()=>set('typography',undefined)}>
            {(['title','value','helper'] as const).map((role,index)=>{
                const label = ['标题','主值','说明'][index];
                return <details key={role}><summary>{label}</summary>
                    <NumericField label={label+'字号'} value={typography?.[role]?.size} min={8} max={96} onChange={size=>setRole(role,{size})}/>
                    <SelectField label={label+'字重'} value={typography?.[role]?.weight} empty={defaultOption} options={['normal','bold']}
                        onChange={weight=>setRole(role,{weight:weight as TextStyle['weight'] || undefined})}/>
                    <SelectField label={label+'颜色'} value={typography?.[role]?.color} empty={defaultOption}
                        options={['default','neutral','primary','green','red','amber']} onChange={color=>setRole(role,{color:color || undefined})}/>
                </details>;
            })}
        </OverrideGroup>}
        {['text','number','runtime_status'].includes(type) && <SelectField label="标题和值排列" value={String(props.valueLayout || 'vertical')}
            options={[{value:'vertical',label:'上下'},{value:'horizontal',label:'同行'}]} onChange={value=>set('valueLayout',value)}/>}
        {['text','number','indicator','runtime_status'].includes(type) && <label>辅助说明<textarea maxLength={1024} value={String(props.helperText ?? '')}
            onChange={event=>set('helperText',event.target.value)}/></label>}
        {type === 'indicator' && <>
            <SelectField label="判定显示" value={String(props.indicatorView || 'text')} options={['text','badge','row']} onChange={value=>set('indicatorView',value)}/>
            <SelectField label="圆点" value={props.showMarker == null ? '' : String(props.showMarker)} empty={defaultOption}
                options={[{value:'true',label:'显示'},{value:'false',label:'隐藏'}]} onChange={value=>set('showMarker',value ? value === 'true' : undefined)}/>
        </>}
        {type === 'runtime_status' && !Object.keys(component.bindings).length &&
            <SelectField label="宿主状态" value={String(props.statusField ?? 'connection')} options={[{value:'connection',label:'连接'},{value:'job_state',label:'任务'}]}
                onChange={value=>set('statusField',value)}/>}
        {type === 'image' && <OverrideGroup label="图像区域" value={image} enable={()=>setImage({})} reset={()=>set('imageView',undefined)}>
            <SelectField label="图像尺寸" value={image?.sizing ?? 'aspect'} options={['aspect','fixed']} onChange={value=>setImage({sizing:value as ImageView['sizing']})}/>
            <NumericField label="宽高比" value={image?.aspectRatio ?? undefined} min={0.001} step="any" onChange={aspectRatio=>setImage({aspectRatio})}/>
            <SelectField label="图像背景" value={image?.background ?? 'subtle'} options={['subtle','dark','transparent']}
                onChange={value=>setImage({background:value as ImageView['background']})}/>
            {(['horizontalAlign','verticalAlign'] as const).map((key,i)=><SelectField key={key} label={i===0?'图像水平对齐':'图像垂直对齐'}
                value={image?.[key] ?? 'center'} options={['start','center','end']} onChange={value=>setImage({[key]:value})}/>)}
        </OverrideGroup>}
        {type === 'navigation_button' && <SelectField label="按钮样式" value={String(props.buttonStyle ?? 'secondary')} options={['primary','secondary','link']}
            onChange={value=>set('buttonStyle',value)}/>}
        {type === 'container' && <OverrideGroup label="折叠区" value={props.collapse} enable={()=>set('collapse',{initiallyCollapsed:false})} reset={()=>set('collapse',undefined)}>
            <label>初始折叠<input type="checkbox" checked={Boolean((props.collapse as {initiallyCollapsed?:boolean}|undefined)?.initiallyCollapsed)}
                onChange={event=>set('collapse',{initiallyCollapsed:event.target.checked})}/></label>
        </OverrideGroup>}
    </>;
}

export type HistorySource = NonNullable<Component['history']> & {
    columns?:string[]; title?:string; orderBy?:string; descending?:boolean;
};

const optionsOf = (components:SourceComponent[]) => components.map(component=>({
    value:component.componentId,label:String(component.props.title || component.componentId),
}));

export function ComponentActionFields({component,doc,page,update}: {
    component:SourceComponent; doc:Presentation; page:SourcePage; update:(change:(component:SourceComponent)=>void)=>void;
}) {
    const clicked = component.actions.clicked;
    const action=clicked?.type ?? '';
    const availableScopes=[...new Set([...page.resultScopeIds,...Object.keys(doc.resultScopes)])];
    const scopes = availableScopes.map(id=>({value:id,label:`${doc.resultScopes[id]?.scopeWorkflowId ?? ''} · ${id}`}));
    const tables = optionsOf(walk(page.components).filter(item=>item.type === 'sqlite_table'));
    const pages = doc.pageOrder.map(id=>({value:id,label:doc.pages[id].name}));
    const actionOptions = [
        {value:'navigate',label:'切换页面'},{value:'freeze',label:'冻结结果'},{value:'resume_live',label:'恢复实时'},
        {value:'history_previous',label:'历史上一条'},{value:'history_next',label:'历史下一条'},{value:'history_first',label:'刷新历史首页'},
    ].filter(option=>!option.value.startsWith('history_') || tables.length || option.value === action)
        .filter(option=>option.value !== 'freeze' || scopes.length || option.value === action);
    const change = (next:NonNullable<Component['actions']>['clicked']) => update(item=>{
        if(next)item.actions.clicked=next;
        else delete item.actions.clicked;
    });
    return <>
        <SelectField label="点击动作" value={action} options={actionOptions} empty="无动作" onChange={type=>{
            if (!type) change(undefined);
            else if (type === 'navigate') change({type,pageId:pages[0].value,context:'live'});
            else if (type.startsWith('history_')) change({type,componentId:tables[0]?.value});
            else change({type,resultScopeId:type === 'freeze' ? scopes[0]?.value : undefined});
        }}/>
        {clicked && action === 'navigate' && <>
            <SelectField label="导航目标页面" value={clicked.pageId} options={pages} onChange={pageId=>change({...clicked,pageId})}/>
            {scopes.length > 0 && <SelectField label="导航上下文" value={clicked.context ?? 'live'} options={[
                {value:'live',label:'实时'},{value:'displayed_result',label:'当前显示结果'},
            ]} onChange={context=>change({...clicked,context,resultScopeId:context === 'displayed_result' ? scopes[0]?.value : undefined})}/>}
        </>}
        {clicked && (action === 'freeze' || action === 'resume_live' || clicked.context === 'displayed_result') &&
            <SelectField label="结果作用域" value={clicked.resultScopeId} empty={action === 'resume_live'?'宿主当前范围':undefined}
                options={scopes} onChange={resultScopeId=>change({...clicked,resultScopeId:resultScopeId || undefined})}/>}
        {clicked && action.startsWith('history_') &&
            <SelectField label="历史动作来源" value={clicked.componentId} options={tables} onChange={componentId=>change({...clicked,componentId})}/>}
    </>;
}

export function HistoryFields({component,page,update,sources}: {
    component:SourceComponent; page:SourcePage; update:(change:(component:SourceComponent)=>void)=>void;
    sources:Record<string,HistorySource>;
}) {
    const {type,props}=component;
    const tables = walk(page.components).filter(item=>item.type === 'sqlite_table');
    const tableOptions = optionsOf(tables);
    const columns = (id?:string) => id ? sources[id]?.columns ?? [] : [];
    const set = (key:string,value:unknown) => update(item=>{if(value == null)delete item.props[key];else item.props[key]=value;});
    if (type === 'sqlite_table') {
        const view = props.historyView as {mode?:string;titleField?:string;subtitleField?:string;trailingField?:string} | undefined;
        const source = sources[component.componentId];
        return <section><h2>历史记录</h2>
            {source ? <small>Runtime 来源 · {source.title || source.componentId}</small> : <p role="status">当前启用页面包缺少这个 Runtime 历史来源。请启用与 Runtime 工程匹配的页面包。</p>}
            <NumericField label="历史每页条数" value={Number(props.pageSize || 20)} min={1} max={100} onChange={value=>set('pageSize',value)}/>
            <SelectField label="历史显示方式" value={view?.mode ?? 'table'} options={[{value:'table',label:'表格'},{value:'list',label:'列表'}]}
                onChange={mode=>set('historyView',mode === 'list' ? {mode} : undefined)}/>
            {view?.mode === 'list' && (['titleField','subtitleField','trailingField'] as const).map((key,index)=>
                <SelectField key={key} label={['列表主字段','列表副字段','列表状态字段'][index]} value={view[key]} empty="未指定"
                    options={columns(component.componentId)} onChange={value=>set('historyView',{...view,[key]:value || undefined})}/>)}
        </section>;
    }
    if (!['text','number','indicator'].includes(type) || !tables.length) return null;
    const record = props.recordValue as {componentId:string;kind:'field'|'meta';field?:string;meta?:'position'|'count'} | undefined;
    return <section><h2>所选历史记录</h2>
        <SelectField label="历史记录来源" value={record?.componentId} options={tableOptions} empty="不关联"
            onChange={componentId=>update(item=>{
                if(!componentId)delete item.props.recordValue;
                else {
                    item.bindings={};
                    const field=columns(componentId)[0];
                    item.props.recordValue=field ? {componentId,kind:'field',field} : {componentId,kind:'meta',meta:'position'};
                }
            })}/>
        {record && <>
            <SelectField label="记录值类型" value={record.kind} options={[{value:'field',label:'声明字段'},{value:'meta',label:'查询信息'}]}
                onChange={kind=>set('recordValue',kind === 'field' && columns(record.componentId).length
                    ? {componentId:record.componentId,kind,field:columns(record.componentId)[0]}
                    : {componentId:record.componentId,kind:'meta',meta:'position'})}/>
            {record.kind === 'field'
                ? <SelectField label="记录字段" value={record.field} options={columns(record.componentId)} onChange={field=>set('recordValue',{...record,field})}/>
                : <SelectField label="查询信息" value={record.meta} options={[{value:'position',label:'位置'},{value:'count',label:'总量'}]}
                    onChange={meta=>set('recordValue',{...record,meta})}/>}
        </>}
    </section>;
}
