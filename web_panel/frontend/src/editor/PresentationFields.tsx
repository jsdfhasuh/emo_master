import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { Appearance, BorderAppearance, ButtonAppearance, ButtonStateAppearance, ButtonStates, Component, ControlAppearance,
    HexColor, HistoryView, HostControl, ImageView, Panel, PanelShell, Result, Surface, TextStyle, Typography } from '../contracts/types';
import type { Presentation, SourceComponent, SourcePage } from './model';
import { componentNames, walk } from './model';
import { AdvancedAppearanceDialog } from './AdvancedAppearanceDialog';
import { HostControlButton } from '../renderer/shell';
import { PanelRenderer } from '../renderer/PanelRenderer';

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

const isHex = (value:string|undefined): value is HexColor => /^#[0-9a-f]{6}$/i.test(value ?? '');

function HexField({label,value,onChange}: {label:string;value?:HexColor;onChange:(value:HexColor|undefined)=>void}) {
    const [text,setText]=useState(value ?? '');
    useEffect(()=>setText(value ?? ''),[value]);
    return <label>{label}<input aria-label={label} placeholder="#RRGGBB" maxLength={7}
        value={text} onChange={event=>{
            const raw=event.target.value;
            setText(raw);
            if (!raw || isHex(raw)) onChange(raw as HexColor || undefined);
        }} onBlur={()=>setText(value ?? '')}/>
        <input aria-label={label+'颜色选择器'} type="color" value={value ?? '#000000'} onChange={event=>onChange(event.target.value as HexColor)}/>
    </label>;
}

export function ColorField({label,value,options,empty=defaultOption,onChange}: {
    label:string; value?:string; options:readonly Option[]; empty?:string | false; onChange:(value:string|undefined)=>void;
}) {
    return <>
        <SelectField label={label} value={isHex(value) ? 'custom' : value} empty={empty || undefined}
            options={[...options,{value:'custom',label:'自定义颜色'}]}
            onChange={color=>onChange(color==='custom' ? '#000000' : color || undefined)}/>
        {isHex(value) && <HexField label={label+'色值'} value={value} onChange={color=>onChange(color || value)}/>}
    </>;
}

function BorderFields({value,onChange}: {value?:BorderAppearance;onChange:(patch:BorderAppearance)=>void}) {
    return <>
        <HexField label="边框颜色" value={value?.borderColor} onChange={borderColor=>onChange({borderColor})}/>
        <NumericField label="边框宽度" value={value?.borderWidth} min={0} max={16} onChange={borderWidth=>onChange({borderWidth})}/>
        <SelectField label="边框线型" value={value?.borderStyle} empty={defaultOption}
            options={[{value:'solid',label:'实线'},{value:'dashed',label:'虚线'},{value:'dotted',label:'点线'}]}
            onChange={style=>onChange({borderStyle:(style || undefined) as BorderAppearance['borderStyle']})}/>
    </>;
}

function PaddingFields({label='',value,onChange}: {label?:string;value?:Surface['padding'];onChange:(value:Surface['padding'])=>void}) {
    return (['top','right','bottom','left'] as const).map((side,index)=><NumericField key={side}
        label={`${label}${['上','右','下','左'][index]}内边距`} value={value?.[side]} min={0} max={64}
        onChange={size=>onChange({...value,[side]:size})}/>);
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

function TextStyleFields({label,value,change}: {label:string;value?:TextStyle;change:(patch:TextStyle)=>void}) {
    return <>
        <NumericField label={label+'字号'} value={value?.size} min={8} max={96} onChange={size=>change({size})}/>
        <SelectField label={label+'字重'} value={value?.weight} empty={defaultOption} options={['normal','bold']}
            onChange={weight=>change({weight:weight as TextStyle['weight'] || undefined})}/>
        <ColorField label={label+'颜色'} value={value?.color}
            options={['default','neutral','primary','green','red','amber']} onChange={color=>change({color})}/>
    </>;
}

function ShellSurfaceFields({value,change}: {value?:Surface;change:(value:Surface|undefined)=>void}) {
    const update=(patch:Surface)=>change({...value,...patch});
    return <OverrideGroup label="页头表面" value={value} enable={()=>change({})} reset={()=>change(undefined)}>
        <SelectField label="页头表面类型" value={value?.kind} empty={defaultOption} options={['none','card']}
            onChange={kind=>update({kind:kind as Surface['kind'] || undefined})}/>
        <ColorField label="页头背景" value={value?.background} options={['transparent','surface','subtle']}
            onChange={background=>update({background:background as Surface['background']})}/>
        <SelectField label="页头边框" value={value?.border} empty={defaultOption} options={['none','subtle']}
            onChange={border=>update({border:border as Surface['border'] || undefined})}/>
        <NumericField label="页头圆角" value={value?.radius} min={0} max={32} onChange={radius=>update({radius})}/>
        <PaddingFields label="页头" value={value?.padding} onChange={padding=>update({padding})}/>
    </OverrideGroup>;
}

function ButtonAdvancedFields({appearance,change,hasIcon}: {
    appearance:ButtonAppearance|undefined;change:(patch:ButtonAppearance)=>void;hasIcon:boolean;
}) {
    const states=appearance?.buttonStates;
    const changeState=(name:keyof ButtonStates,patch:ButtonStateAppearance)=>
        change({buttonStates:{...states,[name]:{...states?.[name],...patch}}});
    return <>
        {hasIcon && <fieldset><legend>图标</legend>
            <SelectField label="图标位置" value={appearance?.iconPosition} empty={defaultOption}
                options={[{value:'left',label:'左侧'},{value:'right',label:'右侧'}]}
                onChange={value=>change({iconPosition:(value || undefined) as ButtonAppearance['iconPosition']})}/>
            <NumericField label="图标尺寸" value={appearance?.iconSize} min={8} max={64} onChange={iconSize=>change({iconSize})}/>
            <NumericField label="图文间距" value={appearance?.iconGap} min={0} max={32} onChange={iconGap=>change({iconGap})}/>
            <button type="button" onClick={()=>change({iconPosition:undefined,iconSize:undefined,iconGap:undefined})}>恢复图标默认</button>
        </fieldset>}
        <OverrideGroup label="按钮状态" value={states} enable={()=>change({buttonStates:{}})} reset={()=>change({buttonStates:undefined})}>
            {([['default','正常'],['hover','悬停'],['pressed','按下'],['disabled','禁用']] as const).map(([name,label])=>
                <details key={name}><summary>{label}</summary>
                    <ColorField label={label+'背景'} value={states?.[name]?.background} options={['transparent']}
                        onChange={background=>changeState(name,{background:background as ButtonStateAppearance['background']})}/>
                    <HexField label={label+'文字颜色'} value={states?.[name]?.color} onChange={color=>changeState(name,{color})}/>
                    <BorderFields value={states?.[name]} onChange={patch=>changeState(name,patch)}/>
                </details>)}
        </OverrideGroup>
    </>;
}

function HostCommonFields({label,appearance,update}: {label:string;appearance?:ControlAppearance;update:(patch:ControlAppearance)=>void}) {
    return <>
        <SelectField label={label+'样式'} value={appearance?.style} empty={defaultOption} options={['primary','secondary','link']}
            onChange={style=>update({style:style as ControlAppearance['style'] || undefined})}/>
        <TextStyleFields label={label} value={appearance?.text} change={patch=>update({text:{...appearance?.text,...patch}})}/>
        <NumericField label={label+'圆角'} value={appearance?.radius} min={0} max={32} onChange={radius=>update({radius})}/>
        <NumericField label={label+'最小宽度'} value={appearance?.minWidth} min={0} max={512} onChange={minWidth=>update({minWidth})}/>
        <NumericField label={label+'最小高度'} value={appearance?.minHeight} min={0} max={128} onChange={minHeight=>update({minHeight})}/>
        <PaddingFields label={label} value={appearance?.padding} onChange={padding=>update({padding})}/>
        <label>{label}显示图标<input type="checkbox" checked={appearance?.showIcon ?? false}
            onChange={event=>update({showIcon:event.target.checked})}/></label>
    </>;
}

function HostControlFields({kind,label,value,change}: {kind:'start'|'stop';label:string;value?:HostControl;change:(value:HostControl)=>void}) {
    const appearance=value?.appearance;
    const update=(patch:ControlAppearance)=>change({...value,appearance:{...appearance,...patch}});
    const [advanced,setAdvanced]=useState(false);
    const [previewDisabled,setPreviewDisabled]=useState(false);
    const configured=Boolean(appearance && (!appearance.customStyle || Object.keys(appearance).some(key=>key!=='customStyle')));
    return <details><summary>{label}</summary>
        <label>{label}文字<input aria-label={label+'文字'} placeholder="使用宿主默认" maxLength={256}
            value={value?.label ?? ''} onChange={event=>change({...value,label:event.target.value || undefined})}/></label>
        <OverrideGroup label={label+'外观'} value={configured} enable={()=>update({style:appearance?.style})}
            reset={()=>change({...value,appearance:appearance?.customStyle ? {customStyle:appearance.customStyle} : undefined})}>
            <HostCommonFields label={label} appearance={appearance} update={update}/>
        </OverrideGroup>
        <button type="button" onClick={()=>{setPreviewDisabled(false);setAdvanced(true);}}>{label}高级外观…</button>
        {advanced && <AdvancedAppearanceDialog title={label} initial={appearance ?? {}}
            readStyle={draft=>draft.customStyle} updateStyle={(draft,customStyle)=>({...draft,customStyle})}
            fields={(draft,setDraft)=>{
                const patch=(updates:ControlAppearance)=>setDraft({...draft,...updates});
                return <>
                    <fieldset><legend>按钮</legend>
                        <HostCommonFields label={label} appearance={draft} update={patch}/>
                        <ColorField label="按钮背景" value={draft.background} options={['transparent']}
                            onChange={background=>patch({background:background as ControlAppearance['background']})}/>
                        <BorderFields value={draft} onChange={patch}/>
                    </fieldset>
                    <ButtonAdvancedFields appearance={draft} change={patch} hasIcon/>
                </>;
            }}
            preview={draft=><><label><input type="checkbox" checked={previewDisabled}
                onChange={event=>setPreviewDisabled(event.target.checked)}/>预览禁用状态</label>
                <div className="preview-shell"><HostControlButton control={{...value,appearance:draft}} kind={kind}
                    fallbackLabel={kind==='start'?'启动工作流':'停止选中运行'} disabled={previewDisabled}
                    title="仅预览按钮外观" onClick={()=>{}}/></div></>}
            onConfirm={draft=>{if (draft!==appearance && Object.keys(draft).length) change({...value,appearance:draft});setAdvanced(false);}}
            onClose={()=>setAdvanced(false)}/>}
    </details>;
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
            <NumericField label="Logo 显示宽度" value={shell?.logoWidth ?? 40} min={1} max={512}
                onChange={logoWidth=>changeShell({logoWidth})}/>
            <NumericField label="Logo 显示高度" value={shell?.logoHeight ?? 40} min={1} max={256}
                onChange={logoHeight=>changeShell({logoHeight})}/>
            <ShellSurfaceFields value={shell?.headerSurface} change={headerSurface=>changeShell({headerSurface})}/>
            <OverrideGroup label="页头文字" value={shell?.headerTypography} enable={()=>changeShell({headerTypography:{}})}
                reset={()=>changeShell({headerTypography:undefined})}>
                {(['title','helper'] as const).map((role,index)=><TextStyleFields key={role} label={index?'页头副标题':'页头标题'}
                    value={shell?.headerTypography?.[role]} change={patch=>changeShell({headerTypography:{...shell?.headerTypography,
                        [role]:{...shell?.headerTypography?.[role],...patch}}})}/>)}
            </OverrideGroup>
            <HostControlFields kind="start" label="开始按钮" value={shell?.startControl} change={startControl=>changeShell({startControl})}/>
            <HostControlFields kind="stop" label="停止按钮" value={shell?.stopControl} change={stopControl=>changeShell({stopControl})}/>
            {([
                ['showConnection','显示连接状态',true],['showJobState','显示任务状态',true],
                ['showControls','显示宿主控制',true],['diagnosticsInitiallyExpanded','初始展开诊断',false],
            ] as const).map(([key,label,fallback])=><label key={key}>{label}<input type="checkbox" checked={shell?.[key] ?? fallback}
                onChange={event=>changeShell({[key]:event.target.checked})}/></label>)}
        </OverrideGroup>
    </>;
}

function ComponentSurfaceFields({value,set,advanced=false}: {
    value?:Surface;set:(value:Surface|undefined)=>void;advanced?:boolean;
}) {
    const patch=(changes:Surface)=>set({...value,...changes});
    return <OverrideGroup label="表面" value={value} enable={()=>patch({})} reset={()=>set(undefined)}>
        <SelectField label="表面类型" value={value?.kind} empty={defaultOption} options={['none','card']}
            onChange={kind=>patch({kind:kind as Surface['kind'] || undefined})}/>
        <ColorField label="背景" value={value?.background} options={['transparent','surface','subtle']}
            onChange={background=>patch({background:background as Surface['background']})}/>
        <SelectField label="边框" value={value?.border} empty={defaultOption} options={['none','subtle']}
            onChange={border=>patch({border:border as Surface['border'] || undefined})}/>
        {advanced && <BorderFields value={value} onChange={patch}/>}
        <NumericField label="圆角" value={value?.radius} min={0} max={32} onChange={radius=>patch({radius})}/>
        <PaddingFields value={value?.padding} onChange={padding=>patch({padding})}/>
    </OverrideGroup>;
}

function ComponentTypographyFields({value,set,roles=['title','value','helper']}: {
    value?:Typography;set:(value:Typography|undefined)=>void;roles?:Array<keyof Typography>;
}) {
    const names={title:'标题',value:'主值',helper:'说明'};
    return roles.length > 0 && <OverrideGroup label="文字角色" value={value} enable={()=>set({})} reset={()=>set(undefined)}>
        {roles.map(role=><details key={role}><summary>{names[role]}</summary>
            <TextStyleFields label={names[role]} value={value?.[role]}
                change={patch=>set({...value,[role]:{...value?.[role],...patch}})}/>
        </details>)}
    </OverrideGroup>;
}

function appearanceRoles(component:SourceComponent):Array<keyof Typography> {
    const {type,props}=component;
    if(type==='page_title')return ['title'];
    if(['page_navigation','navigation_button'].includes(type))return ['value'];
    const title: Array<keyof Typography> = props.title || props.collapse ? ['title'] : [];
    if(type==='container')return title;
    if(type==='image')return [...title,'helper'];
    return [...title,'value',...(props.helperText || ['number','clock','table','sqlite_table','sqlite_value'].includes(type)
        ? ['helper' as const] : [])];
}

function ComponentAdvancedFields({component,onChange}: {
    component:SourceComponent;onChange:(props:SourceComponent['props'])=>void;
}) {
    const {props,type}=component;
    const patch=(changes:Partial<SourceComponent['props']>)=>onChange({...props,...changes});
    return <>
        <ComponentSurfaceFields value={props.surface} set={surface=>patch({surface})} advanced/>
        <ComponentTypographyFields value={props.typography} set={typography=>patch({typography})} roles={appearanceRoles(component)}/>
        {type==='image' && <OverrideGroup label="图像区域" value={props.imageView}
            enable={()=>patch({imageView:{}})} reset={()=>patch({imageView:undefined})}>
            <ColorField label="图像背景" value={props.imageView?.background ?? 'subtle'} empty={false}
                options={['subtle','dark','transparent']} onChange={background=>patch({imageView:{...props.imageView,background:background as ImageView['background']}})}/>
        </OverrideGroup>}
        {type==='navigation_button' && <ButtonAdvancedFields appearance={props} change={updates=>patch({...updates})} hasIcon={Boolean(props.iconResourceId || props.icon)}/>}
        {type==='indicator' && Object.entries(props.indicatorStates ?? {}).map(([key,state])=>{
            const update=(changes:Partial<typeof state>)=>patch({indicatorStates:{...props.indicatorStates,[key]:{...state,...changes}}});
            return <details key={key}><summary>判定外观 · {state.text}</summary>
                <ColorField label="状态文字和圆点颜色" value={state.color} empty={false} options={['neutral','green','red','amber','blue']}
                    onChange={color=>update({color:color || 'neutral'})}/>
                {(props.indicatorView ?? 'text')!=='text' && <>
                    <ColorField label="状态背景" value={state.background ?? 'none'} empty={false} options={['none','soft']}
                        onChange={background=>update({background:background as typeof state.background})}/>
                    <BorderFields value={state} onChange={update}/>
                </>}
            </details>;
        })}
    </>;
}

function findPreviewComponent(components:Component[],id:string):Component|undefined {
    for (const item of components) {
        if (item.id===id) return item;
        const nested=findPreviewComponent(item.children ?? [],id);
        if (nested) return nested;
    }
}

export function ComponentPresentationFields({component,update,panel,pageId,results}: {
    component:SourceComponent; update:(change:(component:SourceComponent)=>void)=>void;
    panel?:Panel;pageId?:string;results?:Record<string,Result>;
}) {
    const { props, type } = component;
    const [advanced,setAdvanced]=useState(false);
    const [previewDisabled,setPreviewDisabled]=useState(false);
    const [previewState,setPreviewState]=useState('');
    const surface=props.surface;
    const typography=props.typography;
    const image=props.imageView;
    const set = (key:string,value:unknown) => update(item=>{if (value === undefined) delete item.props[key];else item.props[key]=value;});
    const setImage = (patch:ImageView) => set('imageView',{...image,...patch});
    const previewPage=panel?.pages.find(page=>page.id===pageId);
    const previewItem=previewPage && findPreviewComponent(previewPage.components,component.componentId);
    return <>
        {type !== 'spacer' && <>
            {previewItem && <button type="button" onClick={()=>{setPreviewState('');setPreviewDisabled(false);setAdvanced(true);}}>高级外观…</button>}
            <ComponentSurfaceFields value={surface} set={value=>set('surface',value)}/>
            <ComponentTypographyFields value={typography} set={value=>set('typography',value)}/>
        </>}
        {advanced && panel && previewPage && previewItem && <AdvancedAppearanceDialog title={String(props.title || componentNames[type])}
            initial={props} readStyle={draft=>draft.customStyle} updateStyle={(draft,customStyle)=>({...draft,customStyle})}
            fields={(draft,setDraft)=><ComponentAdvancedFields component={{...component,props:draft}} onChange={setDraft}/>}
            preview={draft=>{
                const current={...previewItem,props:{...previewItem.props,...draft}};
                const previewPanel={...panel,appearance:{pagePadding:12},pages:panel.pages.map(page=>page.id===pageId
                    ? {...page,canvasTop:0,layout:{type:'responsive',direction:'vertical',alignment:'stretch'},components:[current]} : page)};
                return <>
                    {type==='navigation_button' && <label><input type="checkbox" checked={previewDisabled}
                        onChange={event=>setPreviewDisabled(event.target.checked)}/>预览禁用状态</label>}
                    {type==='indicator' && Object.keys(draft.indicatorStates ?? {}).length>0 &&
                        <SelectField label="预览判定值" value={previewState} empty="示例结果"
                            options={Object.entries(draft.indicatorStates ?? {}).map(([value,state])=>({value,label:state.text}))}
                            onChange={setPreviewState}/>}
                    <PanelRenderer panel={previewPanel} pageId={previewPage.id} onPage={()=>{}} onAction={async()=>false}
                        results={results} result={Object.values(results ?? {})[0]} state="示例已连接" runtimeState="RUNNING"
                        sample previewOnly previewDisabled={previewDisabled} sampleIndicatorKey={previewState || undefined}/>
                </>;
            }}
            onConfirm={draft=>{if(draft!==props)update(item=>{item.props=draft;});setAdvanced(false);}}
            onClose={()=>setAdvanced(false)}/>}
        {['text','number','runtime_status'].includes(type) && <SelectField label="标题和值排列" value={String(props.valueLayout || 'vertical')}
            options={[{value:'vertical',label:'上下'},{value:'horizontal',label:'同行'}]} onChange={value=>set('valueLayout',value)}/>}
        {['text','number','indicator','runtime_status'].includes(type) && <label>辅助说明<textarea maxLength={1024} value={String(props.helperText ?? '')}
            onChange={event=>set('helperText',event.target.value)}/></label>}
        {type === 'indicator' && <>
            <SelectField label="判定显示" value={String(props.indicatorView || 'text')} options={['text','badge','row']} onChange={value=>set('indicatorView',value)}/>
            <SelectField label="圆点" value={props.showMarker == null ? '' : String(props.showMarker)} empty={defaultOption}
                options={[{value:'true',label:'显示'},{value:'false',label:'隐藏'}]} onChange={value=>set('showMarker',value ? value === 'true' : undefined)}/>
            <SelectField label="圆点位置" value={String(props.markerPosition ?? 'value')}
                options={[{value:'value',label:'状态值前'},{value:'title',label:'标题前'}]} onChange={value=>set('markerPosition',value)}/>
            <label>标题跟随状态颜色<input type="checkbox" checked={Boolean(props.titleFollowsState)}
                onChange={event=>set('titleFollowsState',event.target.checked)}/></label>
        </>}
        {type === 'runtime_status' && !Object.keys(component.bindings).length &&
            <SelectField label="宿主状态" value={String(props.statusField ?? 'connection')} options={[{value:'connection',label:'连接'},{value:'job_state',label:'任务'}]}
                onChange={value=>set('statusField',value)}/>}
        {type === 'image' && <OverrideGroup label="图像区域" value={image} enable={()=>setImage({})} reset={()=>set('imageView',undefined)}>
            <SelectField label="图像尺寸" value={image?.sizing ?? 'aspect'} options={['aspect','fixed']} onChange={value=>setImage({sizing:value as ImageView['sizing']})}/>
            <NumericField label="宽高比" value={image?.aspectRatio ?? undefined} min={0.001} step="any" onChange={aspectRatio=>setImage({aspectRatio})}/>
            <ColorField label="图像背景" value={image?.background ?? 'subtle'} options={['subtle','dark','transparent']} empty={false}
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

export type HistoryCatalogEntry = NonNullable<Component['history']> & {
    columns?:string[]; title?:string; aggregate?:'count'|'sum'|'avg'|'min'|'max';
};

function optionsOf(components:SourceComponent[]) {
    const titles=components.map(component=>String(component.props.title || component.componentId));
    return components.map((component,index)=>({
        value:component.componentId,
        label:titles.filter(title=>title===titles[index]).length > 1
            ? `${titles[index]} · ${component.componentId}` : titles[index],
    }));
}

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
    sources:Record<string,HistoryCatalogEntry>;
}) {
    const {type,props}=component;
    const tables = walk(page.components).filter(item=>item.type === 'sqlite_table');
    const tableOptions = optionsOf(tables);
    const entry = (item?:SourceComponent) => item?.props.historySource
        ? sources[item.props.historySource.componentId] : undefined;
    const tableSource = (id?:string) => entry(tables.find(table=>table.componentId===id));
    const columns = (id?:string) => tableSource(id)?.columns ?? [];
    const set = (key:string,value:unknown) => update(item=>{if(value == null)delete item.props[key];else item.props[key]=value;});
    if (type === 'image' && props.historySource) {
        const reference=props.historySource;
        const source=entry(component);
        const choices=source?.kind === 'selected_row_image'
            ? optionsOf(tables.filter(table=>table.props.historySource?.componentId===source.sourceComponentId)) : [];
        return <section><h2>历史图片</h2>
            {source ? <small>Runtime 来源 · {source.title || source.componentId}</small>
                : <p role="status">当前 Runtime 目录缺少这个图片来源。</p>}
            {source?.kind === 'selected_row_image' && (choices.length
                ? <SelectField label="历史图片表格" value={reference.sourceComponentId ?? undefined}
                    options={choices} empty="请选择同页表格"
                    onChange={sourceComponentId=>set('historySource',{componentId:reference.componentId,
                        sourceComponentId:sourceComponentId || undefined})}/>
                : <p role="status">请先在当前页面添加与该图片匹配的历史表格。</p>)}
        </section>;
    }
    if (type === 'sqlite_table') {
        const view = props.historyView as HistoryView | undefined;
        const source = entry(component);
        return <section><h2>历史记录</h2>
            {source ? <small>Runtime 来源 · {source.title || source.componentId}</small> : <p role="status">当前启用页面包缺少这个 Runtime 历史来源。请启用与 Runtime 工程匹配的页面包。</p>}
            <NumericField label="历史每页条数" value={Number(props.pageSize || 20)} min={1} max={100} onChange={value=>set('pageSize',value)}/>
            <SelectField label="历史显示方式" value={view?.mode ?? 'table'} options={[{value:'table',label:'表格'},{value:'list',label:'列表'}]}
                onChange={mode=>set('historyView',{...view,mode})}/>
            {([['showFilter','显示筛选栏'],['showSort','显示排序控制'],['showPagination','显示分页栏']] as const)
                .map(([key,label])=><label key={key}>{label}<input type="checkbox" checked={view?.[key] ?? true}
                    onChange={event=>set('historyView',{...view,[key]:event.target.checked})}/></label>)}
            {view?.mode === 'list' && (['titleField','subtitleField','trailingField'] as const).map((key,index)=>
                <SelectField key={key} label={['列表主字段','列表副字段','列表状态字段'][index]} value={view[key]} empty="未指定"
                    options={columns(component.componentId)} onChange={value=>set('historyView',{...view,[key]:value || undefined})}/>)}
        </section>;
    }
    if (type === 'sqlite_value') {
        const source=entry(component);
        return <section><h2>历史统计</h2>
            {source ? <small>Runtime 来源 · {source.title || source.componentId}</small>
                : <p role="status">当前启用页面包缺少这个 Runtime 历史来源。</p>}
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
                : <SelectField label="查询信息" value={record.meta} options={[
                    {value:'position',label:'位置'},
                    ...(tableSource(record.componentId)?.aggregate === 'count' ? [{value:'count',label:'总量'}] : []),
                ]}
                    onChange={meta=>set('recordValue',{...record,meta})}/>}
        </>}
    </section>;
}
