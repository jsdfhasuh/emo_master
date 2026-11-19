import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const require=createRequire(import.meta.url);
for(const extension of ['.ts','.tsx'])require.extensions[extension]=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,path);
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const {HostControlButton,shellHeaderStyle,shellTextStyle}=require('../src/renderer/shell.tsx');
const {runLabel,runTooltip}=require('../src/state/runLabel.ts');

test('authored header and controls render semantic styles, labels and disabled state',()=>{
 const shell={headerSurface:{kind:'card',background:'subtle',radius:10,padding:{left:20,right:30,top:12,bottom:16}},
  headerTypography:{title:{size:28,color:'primary'},helper:{size:16}}};
 assert.equal(shellHeaderStyle(shell).padding,'12px 30px 16px 20px');
 assert.equal(shellHeaderStyle(shell).borderRadius,10);
 assert.equal(shellTextStyle(shell,'title').fontSize,28);
 assert.equal(shellTextStyle(shell,'helper').fontSize,16);
 const html=renderToStaticMarkup(React.createElement(HostControlButton,{kind:'start',fallbackLabel:'启动工作流',disabled:true,
  title:'启动工作流：装配 · wf',onClick(){},control:{label:'开始检测',appearance:{style:'primary',text:{size:20},radius:10,minWidth:128,minHeight:52,showIcon:true}}}));
 assert.match(html,/开始检测/);assert.doesNotMatch(html,/>启动工作流</);
 assert.match(html,/disabled=""/);assert.match(html,/font-size:20px/);assert.match(html,/min-width:128px/);
 assert.match(html,/min-height:52px/);assert.match(html,/<svg aria-hidden="true"/);
 const style=readFileSync(new URL('../src/style.css',import.meta.url),'utf8');
 assert.match(style,/\.operator-shell \.operator-control:disabled,\.preview-shell \.operator-control:disabled\{color:var\(--panel-muted\);background:var\(--panel-subtle\)/);
});

test('button presentation leaves enabled click and disabled behavior to the host',async()=>{
 const {JSDOM}=require('jsdom');
 const dom=new JSDOM('<div id="root"></div>');
 const oldWindow=globalThis.window,oldDocument=globalThis.document,oldAct=globalThis.IS_REACT_ACT_ENVIRONMENT;
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const {createRoot}=require('react-dom/client');
 const root=createRoot(document.getElementById('root'));
 let clicks=0;
 const button=disabled=>React.createElement(HostControlButton,{kind:'stop',fallbackLabel:'停止选中运行',disabled,title:'停止运行：exact-id',
  control:{label:'停止检测'},onClick(){clicks++;}});
 try{
  await React.act(async()=>root.render(button(true)));
  await React.act(async()=>document.querySelector('button').click());assert.equal(clicks,0);
  await React.act(async()=>root.render(button(false)));
  await React.act(async()=>document.querySelector('button').click());assert.equal(clicks,1);
  assert.equal(document.querySelector('button').title,'停止运行：exact-id');
 }finally{
  await React.act(async()=>root.unmount());dom.window.close();
  globalThis.window=oldWindow;globalThis.document=oldDocument;globalThis.IS_REACT_ACT_ENVIRONMENT=oldAct;
 }
});

test('repeated workflow runs have distinct visible labels and complete IDs in tooltips',()=>{
 const runs=[{id:'11111111-aaaa-4444-8000-123456789012',name:'装配检测',state:'COMPLETED'},
  {id:'22222222-bbbb-4444-8000-123456789012',name:'装配检测',state:'COMPLETED'}];
 assert.notEqual(runLabel(runs[0]),runLabel(runs[1]));
 for(const run of runs){assert.ok(runLabel(run).includes('装配检测'));assert.ok(runTooltip(run).includes(run.id));}
});
