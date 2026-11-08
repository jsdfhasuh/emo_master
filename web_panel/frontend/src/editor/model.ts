import type { DataSource, ResultScope } from '../../../contracts/panel-contract-v1/typescript/panel-contract';
import type { Component, Layout, Panel, Result } from '../contracts/types';

/** The editable document is the complete Presentation source, never a renderer DTO. */
export interface SourceComponent {
    componentId:string; type:string; version?:'1.0'; layout:NonNullable<Component['layout']>;
    responsive:NonNullable<Component['responsive']>; props:Record<string,unknown>;
    bindings:Record<string,string>; actions:NonNullable<Component['actions']>; children:SourceComponent[]; grid:Layout;
}
export interface SourcePage { name:string; targets:['web']; layout:Layout; components:SourceComponent[]; resultScopeIds:string[]; canvasTop?:number; }
export interface Presentation {
    schemaVersion:'1.2'; defaultPageId:string|null; pageOrder:string[]; navigation:{mode:'tabs'|'buttons'};
    pages:Record<string,SourcePage>; dataSources:Record<string,DataSource>; resultScopes:Record<string,ResultScope>;
}
export interface Choice {
    label:string; source:DataSource; scope:ResultScope; sourceId?:string; scopeId?:string;
    enabled?:boolean; reason?:string; issues?:Array<{message:string;code?:string}>;
}
export const componentNames:Record<string,string> = {image:'图像',text:'文字',number:'数值',indicator:'判定指示',runtime_status:'连接状态',table:'集合表格',navigation_button:'导航按钮',container:'容器',page_title:'页面标题',page_navigation:'页面导航',spacer:'空白块'};
export const freshId=(prefix:string)=>`${prefix}-${Array.from(crypto.getRandomValues(new Uint8Array(10)),x=>x.toString(16).padStart(2,'0')).join('')}`;
export const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
export const newPage=(name='新页面'):SourcePage=>({name,targets:['web'],layout:{type:'responsive',direction:'wrap',spacing:12,alignment:'stretch',justify:'start',equalWidth:false},components:[],resultScopeIds:[]});
export function newPresentation():Presentation {
    const id=freshId('page');return {schemaVersion:'1.2',defaultPageId:id,pageOrder:[id],navigation:{mode:'tabs'},pages:{[id]:newPage('检测页')},dataSources:{},resultScopes:{}};
}
export function newComponent(type:string):SourceComponent {
    return {componentId:freshId('component'),type,version:'1.0',layout:{row:0,column:0,rowSpan:1,columnSpan:1},responsive:{width:type==='spacer'?'content':'fill',minWidth:type==='spacer'?24:160,preferredWidth:type==='image'?480:240,minHeight:type==='image'?240:type==='spacer'?24:64,preferredHeight:0,weight:1,alignSelf:'auto'},props:{title:componentNames[type] || type,...(type==='text'?{text:'文本内容'}:{}),...(type==='table'?{columns:[{title:'结果',fieldPath:[]}],pageSize:20}:{}),...(type==='navigation_button'?{text:'打开详情'}:{}),...(type==='indicator'?{indicatorStates:{'true':{text:'OK',color:'green'},'false':{text:'NG',color:'red'},'"OK"':{text:'OK',color:'green'},'"NG"':{text:'NG',color:'red'}}}:{})},bindings:{},actions:{},children:[],grid:{type:'responsive',direction:'wrap',spacing:12,alignment:'stretch',justify:'start',equalWidth:false}};
}
export function walk(components:SourceComponent[]):SourceComponent[] {return components.flatMap(c=>[c,...walk(c.children)]);}
export function findComponent(doc:Presentation,id:string):SourceComponent|undefined {return Object.values(doc.pages).flatMap(p=>walk(p.components)).find(c=>c.componentId===id);}
export function componentContext(page:SourcePage|undefined,id:string):{component:SourceComponent;parentLayout:Layout;siblings:SourceComponent[]}|undefined {
    function visit(children:SourceComponent[],layout:Layout):ReturnType<typeof componentContext>{for(const c of children){if(c.componentId===id)return {component:c,parentLayout:layout,siblings:children};const nested=visit(c.children,c.grid);if(nested)return nested;}}
    return page?visit(page.components,page.layout):undefined;
}
/** History restores source and then reconciles the current page-local selection. */
export function restoreSelection(doc:Presentation,pageId:string,componentId:string):{pageId:string;componentId:string} {
    const home=doc.defaultPageId;
    const validPage=doc.pages[pageId]?pageId:home && doc.pages[home]?home:doc.pageOrder.find(id=>doc.pages[id]) || '';
    return {pageId:validPage,componentId:validPage && walk(doc.pages[validPage].components).some(c=>c.componentId===componentId)?componentId:''};
}
export function removeComponent(children:SourceComponent[],id:string):SourceComponent|undefined {
    const at=children.findIndex(c=>c.componentId===id);if(at>=0)return children.splice(at,1)[0];
    for(const c of children){const found=removeComponent(c.children,id);if(found)return found;}
}
export function copyComponent(c:SourceComponent):SourceComponent {const copied=clone(c);for(const item of walk([copied]))item.componentId=freshId('component');return copied;}
export interface ComponentClipboard {component:SourceComponent;dataSources:Presentation['dataSources'];resultScopes:Presentation['resultScopes'];}
export function copyToClipboard(doc:Presentation,c:SourceComponent):ComponentClipboard {
    const saved:ComponentClipboard={component:clone(c),dataSources:{},resultScopes:{}};const scopes=new Set<string>();
    for(const item of walk([c])){
        for(const id of Object.values(item.bindings)){const source=doc.dataSources[id];if(!source)throw Error(`来源不存在 ${id}`);saved.dataSources[id]=clone(source);scopes.add(source.resultScopeId);}
        for(const action of Object.values(item.actions))if(action?.resultScopeId)scopes.add(action.resultScopeId);
    }
    for(const id of scopes){if(!doc.resultScopes[id])throw Error(`结果作用域不存在 ${id}`);saved.resultScopes[id]=clone(doc.resultScopes[id]);}
    return saved;
}
export function pasteFromClipboard(doc:Presentation,saved:ComponentClipboard):SourceComponent {
    for(const [id,scope] of Object.entries(saved.resultScopes))if(doc.resultScopes[id] && definitionKey({callPath:[],commitPolicy:'workflow_terminal',...doc.resultScopes[id]})!==definitionKey({callPath:[],commitPolicy:'workflow_terminal',...scope}))throw Error(`结果作用域 ${id} 已有不同定义，请重新复制后粘贴`);
    for(const [id,source] of Object.entries(saved.dataSources))if(doc.dataSources[id] && !sameSource(doc.dataSources[id],source))throw Error(`来源 ${id} 已有不同定义，请重新复制后粘贴`);
    Object.assign(doc.resultScopes,clone(saved.resultScopes));Object.assign(doc.dataSources,clone(saved.dataSources));return copyComponent(saved.component);
}
export function setGridPlacement(doc:Presentation,pageId:string,id:string,placement:SourceComponent['layout']):void {
    const context=componentContext(doc.pages[pageId],id);if(!context || context.parentLayout.type!=='grid')throw Error('所选组件不属于当前网格');
    const columns=context.parentLayout.columns || 1,{row=0,column=0,rowSpan=1,columnSpan=1}=placement;
    if(![row,column,rowSpan,columnSpan].every(Number.isInteger) || row<0 || row>4095 || column<0 || column+columnSpan>columns || rowSpan<1 || rowSpan>128 || columnSpan<1)throw Error('网格位置或跨度超出可用范围');
    const overlap=context.siblings.find(c=>c.componentId!==id && row<(c.layout.row || 0)+(c.layout.rowSpan || 1) && row+rowSpan>(c.layout.row || 0) && column<(c.layout.column || 0)+(c.layout.columnSpan || 1) && column+columnSpan>(c.layout.column || 0));
    if(overlap)throw Error(`网格位置与“${overlap.props.title || componentNames[overlap.type]}”重叠，请调整行列或跨度`);
    context.component.layout={row,column,rowSpan,columnSpan};
}
export function resizedGridPlacement(initial:SourceComponent['layout'],columns:number,dx:number,dy:number,columnStep:number):SourceComponent['layout'] {
    return {...initial,columnSpan:Math.max(1,Math.min(columns-(initial.column || 0),(initial.columnSpan || 1)+Math.round(dx/columnStep))),rowSpan:Math.max(1,Math.min(128,(initial.rowSpan || 1)+Math.round(dy/64)))};
}
export function copyPage(page:SourcePage,originalId:string,newId:string):SourcePage {
    const copied={...clone(page),name:`${page.name} 副本`,components:page.components.map(copyComponent)};
    for(const c of walk(copied.components))if(c.actions.clicked?.type==='navigate' && c.actions.clicked.pageId===originalId)c.actions.clicked.pageId=newId;
    return copied;
}
/** Reorder/duplicate without discarding component sizes or creating overlapping cells. */
export function arrangeGrid(children:SourceComponent[],layout:Layout):void {
    if(layout.type!=='grid')return;
    const columns=Math.max(1,Math.min(24,layout.columns || 1)),bottoms=Array<number>(columns).fill(0);
    for(const c of children){
        // A grid spacer occupies cells; elastic growth only applies to responsive parents.
        if(c.type==='spacer' && c.props.spacerMode==='flex')c.props.spacerMode='fixed';
        const width=Math.max(1,Math.min(columns,c.layout.columnSpan || 1)),height=Math.max(1,Math.min(128,c.layout.rowSpan || 1));
        let row=Infinity,column=0;
        for(let start=0;start<=columns-width;start++){const top=Math.max(...bottoms.slice(start,start+width));if(top<row){row=top;column=start;}}
        if(row>4095)throw Error('当前网格行数已达上限，请减少组件或调整跨度');
        c.layout={row,column,rowSpan:height,columnSpan:width};
        for(let i=column;i<column+width;i++)bottoms[i]=row+height;
    }
}
/** A canonical comparison for definitions only; revisions always come from the server. */
export function definitionKey(value:unknown):string {if(Array.isArray(value))return `[${value.map(definitionKey).join(',')}]`;if(value && typeof value==='object')return `{${Object.entries(value).filter(([,v])=>v!==undefined && v!==null).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${definitionKey(v)}`).join(',')}}`;return JSON.stringify(value);}
export function sameSource(a:DataSource|undefined,b:DataSource):boolean {return Boolean(a) && definitionKey({callPath:[],fieldPath:[],ruleVersion:'1.0',...a})===definitionKey({callPath:[],fieldPath:[],ruleVersion:'1.0',...b});}
export function compatible(type:string,source:DataSource):string {
    const accepted:Record<string,string[]>={image:['image'],number:['integer','number'],indicator:['boolean','string'],table:['collection'],text:['integer','number','string','boolean','json','geometry']};
    if(!accepted[type])return '此组件不绑定检测输出';
    return accepted[type].includes(source.expectedType)?'':`${componentNames[type]}不支持 ${source.expectedType} 输出`;
}
export function bindChoice(doc:Presentation,pageId:string,componentId:string,choice:Choice):void {
    const component=componentContext(doc.pages[pageId],componentId)?.component;if(!component)throw Error('请先选择当前页面组件');
    const reason=compatible(component.type,choice.source) || choice.reason || choice.issues?.map(x=>x.message).join('; ');
    if(reason || choice.enabled===false)throw Error(reason || '此输出不可绑定');
    // Strip default omissions so a refreshed catalogue keeps existing stable identities.
    const normalizedScope={callPath:[],commitPolicy:'workflow_terminal',...choice.scope};
    const scopeId=Object.entries(doc.resultScopes).find(([,s])=>definitionKey({callPath:[],commitPolicy:'workflow_terminal',...s})===definitionKey(normalizedScope))?.[0] || choice.scopeId || freshId('scope');
    doc.resultScopes[scopeId]=clone(choice.scope);
    const source={callPath:[],fieldPath:[],ruleVersion:'1.0',...clone(choice.source),resultScopeId:scopeId} as DataSource;
    const sourceId=Object.entries(doc.dataSources).find(([,s])=>definitionKey({callPath:[],fieldPath:[],ruleVersion:'1.0',...s})===definitionKey(source))?.[0] || choice.sourceId || freshId('source');
    doc.dataSources[sourceId]=source;
    component.bindings={[component.type==='image'?'image':component.type==='table'?'rows':'value']:sourceId};
    delete component.props.imageResourceId;
    if(!doc.pages[pageId].resultScopeIds.includes(scopeId))doc.pages[pageId].resultScopeIds.push(scopeId);
}
export function tidyBindings(doc:Presentation):void {
    const used=new Set<string>();const scopes=new Set<string>();
    for(const page of Object.values(doc.pages)){
        const pageScopes=new Set<string>();
        for(const c of walk(page.components)){Object.values(c.bindings).forEach(id=>{used.add(id);const scope=doc.dataSources[id]?.resultScopeId;if(scope){pageScopes.add(scope);scopes.add(scope);}});for(const action of Object.values(c.actions))if(action?.resultScopeId){pageScopes.add(action.resultScopeId);scopes.add(action.resultScopeId);}}
        page.resultScopeIds=[...pageScopes];
    }
    for(const id of Object.keys(doc.dataSources))if(!used.has(id))delete doc.dataSources[id];
    for(const id of Object.keys(doc.resultScopes))if(!scopes.has(id))delete doc.resultScopes[id];
}
/** Pure one-way projection, used while arranging unsaved source. */
export function renderModel(doc:Presentation,assetUrl?:(id:string)=>string):Panel {
    const component=(c:SourceComponent):Component=>{const sourceId=Object.values(c.bindings)[0];return {id:c.componentId,type:c.type,label:String(c.props.title || ''),props:{...clone(c.props),...(c.props.imageResourceId && assetUrl?{assetUrl:assetUrl(String(c.props.imageResourceId))}:{}),...(c.props.iconResourceId && assetUrl?{iconUrl:assetUrl(String(c.props.iconResourceId))}:{})},layout:clone(c.layout),responsive:clone(c.responsive),grid:clone(c.grid),actions:clone(c.actions),children:c.children.map(component),...(sourceId?{binding:{sourceId,scopeId:doc.dataSources[sourceId]?.resultScopeId}}:{})};};
    return {id:'editor-draft',name:'Web 草稿',packageRevision:'unsaved-draft',defaultPageId:doc.defaultPageId || undefined,navigation:clone(doc.navigation),pages:doc.pageOrder.map(id=>({id,title:doc.pages[id].name,layout:clone(doc.pages[id].layout),canvasTop:doc.pages[id].canvasTop,components:doc.pages[id].components.map(component)}))};
}
export function exampleResults(doc:Presentation):Record<string,Result> {
    return Object.fromEntries(Object.keys(doc.resultScopes).map(scopeId=>[scopeId,{resultKey:`sample-${scopeId}`,resultScopeId:scopeId,sources:Object.fromEntries(Object.entries(doc.dataSources).filter(([,source])=>source.resultScopeId===scopeId).map(([id,source])=>[id,{state:source.expectedType==='image'?'unavailable':'available',reason:source.expectedType==='image'?'示例图片占位 · 真实图片从 DisplayService 获取':undefined,value:source.expectedType==='boolean'?true:source.expectedType==='integer'?12:source.expectedType==='number'?98.5:source.expectedType==='collection'?[{label:'示例产品',confidence:0.98,count:12}]:source.expectedType==='json'?{result:'OK',count:12}:'OK'}]))}]));
}
