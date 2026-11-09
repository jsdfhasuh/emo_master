import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import ts from 'typescript';
const require=createRequire(import.meta.url);
require.extensions['.ts']=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,path);
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const {newPresentation,newPage,newComponent,componentContext,bindChoice,copyToClipboard,pasteFromClipboard,tidyBindings,clone,setGridPlacement,resizedGridPlacement,componentNames}=require('../src/editor/model.ts');
const choice={label:'Count',sourceId:'count',scopeId:'main',scope:{entryWorkflowId:'wf',scopeWorkflowId:'wf',callPath:[],commitPolicy:'workflow_terminal'},source:{kind:'node_output',resultScopeId:'main',workflowId:'wf',nodeId:'count',port:'count',expectedType:'integer'}};

test('page-local context returns the direct nested layout and never selects another page',()=>{
 const d=newPresentation(),pid=d.defaultPageId,c=newComponent('container'),child=newComponent('text');c.grid={type:'grid',columns:3};c.children=[child];d.pages[pid].components=[c];d.pages.other=newPage();
 assert.equal(componentContext(d.pages[pid],child.componentId).parentLayout,c.grid);
 assert.equal(componentContext(d.pages.other,child.componentId),undefined);
 assert.throws(()=>bindChoice(d,'other',child.componentId,choice),/当前页面/);
});
test('clipboard restores the deleted subtree sources and action scopes with stable identities',()=>{
 const d=newPresentation(),pid=d.defaultPageId,c=newComponent('container'),child=newComponent('text');c.children=[child];d.pages[pid].components=[c];bindChoice(d,pid,child.componentId,choice);
 const nav=newComponent('navigation_button');nav.actions.clicked={type:'navigate',pageId:pid,context:'displayed_result',resultScopeId:'action'};c.children.push(nav);d.resultScopes.action={...choice.scope};const clip=copyToClipboard(d,c);
 d.pages[pid].components=[];tidyBindings(d);assert.deepEqual(d.dataSources,{});assert.deepEqual(d.resultScopes,{});
 const pasted=pasteFromClipboard(d,clip);d.pages[pid].components=[pasted];tidyBindings(d);
 assert.equal(pasted.children[0].bindings.value,'count');assert.deepEqual(d.dataSources.count,clip.dataSources.count);assert.deepEqual(d.resultScopes,clip.resultScopes);assert.notEqual(pasted.componentId,c.componentId);assert.notEqual(pasted.children[0].componentId,child.componentId);
});
test('clipboard reuses shared definitions and handles static components without extra definitions',()=>{
 const d=newPresentation(),pid=d.defaultPageId,c=newComponent('text');d.pages[pid].components=[c];bindChoice(d,pid,c.componentId,choice);const before=clone(d.dataSources);
 pasteFromClipboard(d,copyToClipboard(d,c));assert.deepEqual(d.dataSources,before);
 const image=newComponent('image');image.props.imageResourceId='existing-static-resource';const staticClip=copyToClipboard(d,image);assert.deepEqual(staticClip.dataSources,{});assert.deepEqual(staticClip.resultScopes,{});assert.equal(pasteFromClipboard(d,staticClip).props.imageResourceId,'existing-static-resource');
});
test('clipboard rejects conflicting stable source or scope definitions before any mutation',()=>{
 const d=newPresentation(),pid=d.defaultPageId,c=newComponent('text');d.pages[pid].components=[c];bindChoice(d,pid,c.componentId,choice);const clip=copyToClipboard(d,c);
 d.dataSources.count.port='other';let before=clone(d);assert.throws(()=>pasteFromClipboard(d,clip),/来源 count 已有不同定义/);assert.deepEqual(d,before);
 d.dataSources.count=clone(clip.dataSources.count);d.resultScopes.main.scopeWorkflowId='other';before=clone(d);assert.throws(()=>pasteFromClipboard(d,clip),/作用域 main 已有不同定义/);assert.deepEqual(d,before);
});
test('grid resizing uses spans, clamps the direct grid boundary and leaves responsive values unchanged',()=>{
 const d=newPresentation(),pid=d.defaultPageId,c=newComponent('text');d.pages[pid].layout={type:'grid',columns:2};d.pages[pid].components=[c];const responsive=clone(c.responsive);
 const candidate=resizedGridPlacement(c.layout,2,250,50,430);setGridPlacement(d,pid,c.componentId,candidate);assert.equal(c.layout.columnSpan,2);assert.equal(c.layout.rowSpan,2);assert.deepEqual(c.responsive,responsive);
 assert.equal(resizedGridPlacement({...c.layout,column:1,columnSpan:1},2,10000,-10000,430).columnSpan,1);assert.equal(resizedGridPlacement(c.layout,2,0,-10000,430).rowSpan,1);
});
test('grid properties reject overlap and out-of-grid positions without changing saved placement',()=>{
 const d=newPresentation(),pid=d.defaultPageId,a=newComponent('text'),b=newComponent('text');b.layout.column=1;d.pages[pid].layout={type:'grid',columns:2};d.pages[pid].components=[a,b];const before=clone(a.layout);
 assert.throws(()=>setGridPlacement(d,pid,a.componentId,{...a.layout,columnSpan:2}),/重叠/);assert.deepEqual(a.layout,before);
 assert.throws(()=>setGridPlacement(d,pid,a.componentId,{...a.layout,column:2}),/超出/);assert.deepEqual(a.layout,before);
});
function assertPaletteNames(names,source){
 // Read the literal data without importing the palette module or Qt.
 const palette=JSON.parse(execFileSync('python',['-c',`
import ast, json, sys
module = ast.parse(sys.stdin.buffer.read().decode("utf-8"))
definitions = [
    node.value
    for node in module.body
    if isinstance(node, (ast.Assign, ast.AnnAssign))
    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    if isinstance(target, ast.Name) and target.id == "COMPONENTS"
]
if len(definitions) != 1:
    raise ValueError("Expected one top-level COMPONENTS constant")
print(json.dumps({kind: title for kind, title, _ in ast.literal_eval(definitions[0])}))
`],{input:source,encoding:'utf8'}));
 // Web intentionally supports a subset of the Designer component types.
 for(const [type,name] of Object.entries(names))assert.equal(palette[type],name,`${type}: ${name}`);
}

test('component names match the Designer palette without changing internal types',()=>{
 const source=readFileSync(new URL('../../../src/emo_master/apps/designer/page_designer/palette.py',import.meta.url),'utf8');
 assertPaletteNames(componentNames,source);
});

test('palette comparison accepts equivalent Python formatting without executing the module',()=>{
 const names={text:'文字',number:'数值'};
 for(const source of [
  "COMPONENTS=(('text','文字','Text'),('number','数值','Number'),('clock','日期时间','Clock'))",
  `raise RuntimeError("The palette module must not execute")
COMPONENTS: tuple = (
    ("text", "文字", "Text"),
    ("number", "数值", "Number"),
    ("clock", "日期时间", "Clock"),
)`,
 ])assertPaletteNames(names,source);
});

test('palette comparison rejects renamed or missing types even if old names remain in comments',()=>{
 for(const source of [
  "# ('text', '文字', 'Old name')\nCOMPONENTS=(('text','正文','Text'),)",
  "# ('text', '文字', 'Old type')\nCOMPONENTS=(('paragraph','文字','Text'),)",
 ])assert.throws(()=>assertPaletteNames({text:'文字'},source),{code:'ERR_ASSERTION'});
});
