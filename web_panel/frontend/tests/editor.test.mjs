import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import ts from 'typescript';
const require=createRequire(import.meta.url);
require.extensions['.ts']=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,path);
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const {newPresentation,newComponent,newPage,bindChoice,clone,renderModel,copyComponent,copyPage,tidyBindings,compatible,exampleResults,definitionKey,sameSource,arrangeGrid,restoreSelection}=require('../src/editor/model.ts');
const scope={entryWorkflowId:'wf',scopeWorkflowId:'wf',callPath:[],commitPolicy:'workflow_terminal'};
const choice=(type='integer',port='count')=>({label:'流程 / 节点 / 输出',scopeId:'scope-stable',sourceId:`source-${port}`,scope,source:{kind:'node_output',resultScopeId:'scope-stable',workflowId:'wf',nodeId:'node-stable',port,expectedType:type}});

test('history keeps a surviving page and its nested selection',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId,container=newComponent('container'),nested=newComponent('text');container.children.push(nested);doc.pages[pid].components.push(container);
    assert.deepEqual(restoreSelection(doc,pid,nested.componentId),{pageId:pid,componentId:nested.componentId});
});
test('history selects the restored home or first existing page when current page was removed',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;
    assert.deepEqual(restoreSelection(doc,'undone-new-page','removed-component'),{pageId:pid,componentId:''});
    doc.defaultPageId='missing-home';doc.pageOrder=['missing-order-entry',pid];
    assert.deepEqual(restoreSelection(doc,'removed-copy',''),{pageId:pid,componentId:''});
});
test('history clears selections which are absent or belong to another surviving page',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId,c=newComponent('number');doc.pages.detail=newPage('详情');doc.pageOrder.push('detail');doc.pages.detail.components.push(c);
    assert.deepEqual(restoreSelection(doc,pid,c.componentId),{pageId:pid,componentId:''});
    assert.deepEqual(restoreSelection(doc,pid,'deleted-component'),{pageId:pid,componentId:''});
});

test('new source retains current Presentation format, Web target and complete separate identity maps',()=>{
    const doc=newPresentation();assert.equal(doc.schemaVersion,'1.2');assert.deepEqual(doc.pages[doc.defaultPageId].targets,['web']);assert.deepEqual(doc.dataSources,{});assert.deepEqual(doc.resultScopes,{});assert.equal(doc.pageOrder.length,1);
});
test('direct binding uses catalogue IDs and layout or display-name changes never recreate source identity',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;const c=newComponent('number');doc.pages[pid].components.push(c);
    bindChoice(doc,pid,c.componentId,choice());assert.equal(c.bindings.value,'source-count');assert.equal(doc.dataSources['source-count'].nodeId,'node-stable');
    c.responsive.preferredWidth=640;const renamed=choice();renamed.label='新流程名 / 新节点名 / 数量';bindChoice(doc,pid,c.componentId,renamed);
    assert.deepEqual(Object.keys(doc.dataSources),['source-count']);assert.deepEqual(Object.keys(doc.resultScopes),['scope-stable']);assert.equal(c.responsive.preferredWidth,640);
});
test('incompatible output gives a concrete reason without partially changing document',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;const c=newComponent('image');doc.pages[pid].components.push(c);const before=clone(doc);
    assert.throws(()=>bindChoice(doc,pid,c.componentId,choice()),/不支持 integer/);assert.deepEqual(doc,before);assert.equal(compatible('table',choice('json').source),'集合表格不支持 json 输出');assert.equal(compatible('runtime_status',choice().source),'此组件不绑定检测输出');
});
test('single-direction rendering preserves complete editable source and resource references',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;const image=newComponent('image');image.props.imageResourceId='asset-stable';image.props.emptyText='等待本轮图片';doc.pages[pid].components.push(image);const counter=newComponent('number');doc.pages[pid].components.push(counter);bindChoice(doc,pid,counter.componentId,choice());
    const before=clone(doc),view=renderModel(doc,id=>`/api/editor/r/assets/${id}?projectId=p`);assert.deepEqual(doc,before);assert.equal(view.pages[0].components[0].props.assetUrl,'/api/editor/r/assets/asset-stable?projectId=p');assert.equal(view.pages[0].components[1].binding.sourceId,'source-count');view.pages[0].components[0].props.emptyText='changed';assert.equal(image.props.emptyText,'等待本轮图片');assert.equal(doc.schemaVersion,'1.2');assert.deepEqual(doc.pages[pid].targets,['web']);
});
test('copy changes component identity recursively but keeps shared immutable binding definitions',()=>{
    const c=newComponent('container');c.children.push(newComponent('number'));c.children[0].bindings.value='same-source';const copied=copyComponent(c);assert.notEqual(copied.componentId,c.componentId);assert.notEqual(copied.children[0].componentId,c.children[0].componentId);assert.equal(copied.children[0].bindings.value,'same-source');copied.children[0].props.title='Copy';assert.notEqual(c.children[0].props.title,'Copy');
});
test('all pages contribute sources, removing last binding prunes only its unused capture definitions',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;doc.pages.detail=newPage('详情');doc.pageOrder.push('detail');const a=newComponent('number'),b=newComponent('indicator');doc.pages[pid].components.push(a);doc.pages.detail.components.push(b);bindChoice(doc,pid,a.componentId,choice());bindChoice(doc,'detail',b.componentId,choice('boolean','result'));tidyBindings(doc);assert.equal(Object.keys(doc.dataSources).length,2);assert.deepEqual(doc.pages.detail.resultScopeIds,['scope-stable']);a.bindings={};tidyBindings(doc);assert.deepEqual(Object.keys(doc.dataSources),['source-result']);assert.deepEqual(doc.pages[pid].resultScopeIds,[]);assert.deepEqual(Object.keys(doc.resultScopes),['scope-stable']);
});
test('sample values have explicit sample result identities and never manufacture live images',()=>{
    const doc=newPresentation(),pid=doc.defaultPageId;const a=newComponent('image'),b=newComponent('number');doc.pages[pid].components.push(a,b);bindChoice(doc,pid,a.componentId,choice('image','image'));bindChoice(doc,pid,b.componentId,choice());const sample=exampleResults(doc)['scope-stable'];assert.match(sample.resultKey,/^sample-/);assert.equal(sample.sources['source-image'].state,'unavailable');assert.equal(sample.sources['source-count'].value,12);assert.equal(sample.sources['source-image'].image,undefined);
});
test('definition comparison ignores object key order without equating different invocation paths',()=>{
    assert.equal(definitionKey({a:1,b:['x']}),definitionKey({b:['x'],a:1}));assert.notEqual(definitionKey({...scope,callPath:[{nodeId:'call-a'}]}),definitionKey({...scope,callPath:[{nodeId:'call-b'}]}));
});
test('binding selector distinguishes field projections and expected types on the same output',()=>{
 const a=choice().source;assert.equal(sameSource({...a,callPath:[],fieldPath:[],ruleVersion:'1.0'},a),true);assert.equal(sameSource({...a,fieldPath:['items','*','area']},a),false);assert.equal(sameSource({...a,expectedType:'string'},a),false);assert.equal(sameSource({...a,resultScopeId:'different-scope'},a),false);
});
test('grid reorder and duplication preserve spans and keep occupied cells disjoint',()=>{
 const wide=newComponent('image'),tall=newComponent('table'),small=newComponent('number');wide.layout={row:0,column:0,rowSpan:2,columnSpan:2};tall.layout={row:0,column:0,rowSpan:3,columnSpan:1};const children=[tall,wide,small,copyComponent(wide)];arrangeGrid(children,{type:'grid',columns:3});assert.equal(wide.layout.columnSpan,2);assert.equal(wide.layout.rowSpan,2);assert.equal(tall.layout.rowSpan,3);const occupied=new Set();for(const c of children)for(let r=c.layout.row;r<c.layout.row+c.layout.rowSpan;r++)for(let col=c.layout.column;col<c.layout.column+c.layout.columnSpan;col++){const key=`${r}/${col}`;assert.equal(occupied.has(key),false);occupied.add(key);}arrangeGrid(children,{type:'grid',columns:1});assert.equal(wide.layout.columnSpan,1);assert.equal(wide.layout.rowSpan,2);
});
test('duplicated page remaps self-navigation while keeping cross-page targets and capture identities',()=>{
 const page=newPage('检测页'),container=newComponent('container'),self=newComponent('navigation_button'),other=newComponent('navigation_button'),counter=newComponent('number');self.actions.clicked={type:'navigate',pageId:'original',context:'live'};other.actions.clicked={type:'navigate',pageId:'details',context:'live'};counter.bindings.value='source-stable';container.children=[self,other,counter];page.components=[container];page.resultScopeIds=['scope-stable'];const copied=copyPage(page,'original','copied');assert.equal(copied.components[0].children[0].actions.clicked.pageId,'copied');assert.equal(copied.components[0].children[1].actions.clicked.pageId,'details');assert.equal(copied.components[0].children[2].bindings.value,'source-stable');assert.deepEqual(copied.resultScopeIds,['scope-stable']);assert.notEqual(copied.components[0].componentId,container.componentId);assert.equal(self.actions.clicked.pageId,'original');
});
