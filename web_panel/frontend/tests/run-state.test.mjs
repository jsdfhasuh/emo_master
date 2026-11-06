// Actual production App/React; only HTTP/WebSocket boundaries are controlled.
// Normal accepted Start -> STARTING directory -> RUNNING/ABORTED RunState.
// This is a component counterexample, not visible browser/Runtime acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {JSDOM} from 'jsdom';
const require=createRequire(import.meta.url);
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true});
for(const key of ['window','document','location','history','sessionStorage','HTMLElement','HTMLInputElement','Event','MouseEvent','FormData'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.ResizeObserver=class{observe(){}disconnect(){}};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.confirm=()=>true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
const sourceRoot=fileURLToPath(new URL('../src/',import.meta.url));
const mainPath=require.resolve('../src/main.tsx');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>{
 let source=readFileSync(file,'utf8');
 if(file===mainPath)source=source.slice(0,source.lastIndexOf('createRoot(document'))+'\nexport {App};';
 module._compile(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
};
require.extensions['.css']=()=>{};
const button=name=>[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===name);
const option=(label,job)=>[...document.querySelector(`select[aria-label="${label}"]`).options].find(x=>x.value===job)?.textContent;
const authority=()=>document.querySelector('[aria-label="原控制运行状态"]')?.textContent;
const renderedState=()=>document.querySelector('[data-component-id="state"]')?.textContent;
async function waitFor(check,limit=2200){const end=Date.now()+limit;while(Date.now()<end){if(check())return;await act(async()=>{await new Promise(r=>setTimeout(r,10));});}assert.ok(check(),'UI condition timed out');}
async function choose(label,id){await act(async()=>{const el=document.querySelector(`select[aria-label="${label}"]`);assert.ok(el,label);el.value=id;el.dispatchEvent(new Event('change',{bubbles:true}));});}
async function click(name){const el=button(name);assert.ok(el,name);assert.equal(el.disabled,false,name);await act(async()=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));}
async function writeInputs(value){await act(async()=>{const el=document.querySelector('textarea[aria-label="工作流输入"]');Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));});}
function job(id,status='STARTING',extra={}){return {id,jobId:id,runId:id,runtimeInstanceId:'instance',projectId:'project',workflowId:id==='consumer'?'consumer':'main',runGroupId:id==='ordinary'?'':'group',sourceJobId:id==='ordinary'?'':'root',targetSlotId:id==='consumer'?'detection':'',name:id,status,state:status.toLowerCase(),active:!['COMPLETED','ABORTED'].includes(status),ownsResources:true,displayResourcesReleased:false,allowedActions:['stop'],...extra};}
function stateOf(run){return {event:'job_state',...Object.fromEntries(['runtimeInstanceId','projectId','jobId','runGroupId','status','active','ownsResources','displayResourcesReleased','allowedActions'].map(key=>[key,run[key]]))};}
function result(jobId,count){return {event:'result',runtimeInstanceId:'instance',projectId:'project',result:{identity:{runtimeInstanceId:'instance',jobId,resultScopeId:'scope',resultKey:`${jobId}-${count}`,resultOrdinal:count},expectedSourceIds:['count'],sources:[{sourceId:'count',state:'AVAILABLE',valueJson:String(count)}]}};}
async function fixture(kind='ordinary',{admission=false}={}){
 for(const file of Object.keys(require.cache))if(file.startsWith(sourceRoot))delete require.cache[file];
 sessionStorage.clear();
 const f={kind,jobs:[],requests:[],views:new Map(),sockets:[],nextView:0,runtimeInstanceId:'instance',projectId:'project',configurationRevision:'2',holdNextState:false,heldState:undefined,failNextCatalog:false,holdNextCatalog:false,heldCatalog:undefined,holdNextWorkflows:false,heldWorkflows:[],failNextWorkflows:false};
 const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const panel={id:'panel',packageRevision:'published-v1',defaultPageId:'page',pages:[{id:'page',title:'Result',components:[{id:'count',type:'number',label:'Count',binding:{sourceId:'count',scopeId:'scope'}},{id:'state',type:'runtime_status',label:'Runtime status'}]}]};
 class Socket{
  constructor(url){this.url=url;this.view=f.views.get(url.split('/').at(-1));this.readyState=1;f.sockets.push(this);queueMicrotask(()=>this.onopen?.({}));}
  close(){if(this.readyState===3)return;this.readyState=3;queueMicrotask(()=>this.onclose?.({}));}
  emit(message){if(this.readyState===1)this.onmessage?.({data:JSON.stringify(message)});}
 }
 globalThis.WebSocket=Socket;
 globalThis.fetch=async(raw,options={})=>{
  const url=new URL(raw,location.origin),p=url.pathname;
  f.requests.push({path:p,query:url.search,method:options.method || 'GET'});
  if(p==='/api/health')return reply({localNoAuth:false});
  if(p==='/api/me')return reply({userId:'operator',username:'operator',role:'operator',csrf:'synthetic'});
  if(p==='/api/logout')return reply({});
  if(p==='/api/runtimes')return reply([{id:'configured',mode:'grpc',permissions:['control']}]);
  if(p==='/api/runtimes/configured/info')return reply({runtimeInstanceId:f.runtimeInstanceId,projectId:f.projectId,configurationRevision:f.configurationRevision});
  if(p==='/api/runtimes/configured/runs'){
   if(f.failNextCatalog){f.failNextCatalog=false;return reply({detail:'catalog temporarily unavailable'},503);}
   const response=reply(f.nextCatalogSnapshot || f.jobs);f.nextCatalogSnapshot=undefined;
   if(f.holdNextCatalog){f.holdNextCatalog=false;await new Promise(resolve=>{f.heldCatalog=resolve;});}
   return response;
  }
  if(p==='/api/runtimes/configured/workflows'){
   if(f.failNextWorkflows){f.failNextWorkflows=false;return reply({detail:'admission temporarily unavailable'},503);}
   const canStart=!admission || !f.jobs.some(run=>run.active || run.ownsResources);
   const response=reply([{id:'main',name:'Main',runtimeInstanceId:f.runtimeInstanceId,projectId:f.projectId,configurationRevision:f.configurationRevision,canStart,startBlockReason:canStart?'':'E_MAX_CONCURRENT_JOBS'}]);
   if(f.holdNextWorkflows){f.holdNextWorkflows=false;await new Promise(release=>f.heldWorkflows.push({release,signal:options.signal}));}
   return response;
  }
  if(p==='/api/panels')return reply([panel]);
  if(p.startsWith('/api/panels/'))return reply(panel);
  if(p==='/api/runs/start'){
   f.jobs=kind==='ordinary'?[job('ordinary')]:[job('root'),job('consumer')];
   return reply({state:'accepted',runId:f.jobs[0].id,status:'STARTING'});
  }
  if(p.startsWith('/api/runs/') && p.endsWith('/stop')){
   const id=p.split('/')[3];f.jobs=f.jobs.map(r=>r.id===id?job(id,'STOPPING'):r);
   return reply({state:'accepted',runId:id,status:'STOPPING'});
  }
  if(p.startsWith('/api/runs/')){
   const run=f.jobs.find(r=>r.id===p.split('/')[3]);assert.ok(run,p);
   if(f.holdNextState){
    f.holdNextState=false;
    // Complete even after abort to exercise the App's late-response guard.
    const delayed=await new Promise(resolve=>{f.heldState={signal:options.signal,release:resolve};});
    return reply(delayed || run);
   }
   return reply(run);
  }
  if(p==='/api/views' && options.method==='POST'){
   const body=JSON.parse(options.body),id='view-'+(++f.nextView);f.views.set(id,body);
   return reply({id,sourceProblems:{},runIds:body.runId==='root'?['root','consumer']:body.runId?[body.runId]:[]});
  }
  if(p.startsWith('/api/views/') && options.method==='DELETE')return reply({});
  if(p==='/api/runtimes/configured/status')return reply({runtime:{runtimeInstanceId:'instance',projectId:'project'},counters:{source:'runtime',counters:[]},events:{events:[],nextSequence:'0',source:'runtime'}});
  if(p.endsWith('/events'))return reply({events:[],nextSequence:'0',source:'runtime',runtimeInstanceId:'instance',projectId:'project'});
  if(p.endsWith('/logs'))return reply({events:[],nextCursor:'0',highWatermark:'0',hasMore:false,runtimeInstanceId:'instance'});
  throw Error('Unhandled HTTP '+p);
 };
 const {App}=require(mainPath),root=createRoot(document.getElementById('root'));
 await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(App))));
 await waitFor(()=>button('启动工作流') && !button('启动工作流').disabled);
 f.current=()=>f.sockets.filter(s=>s.readyState===1).at(-1);
 f.start=async()=>{await click('启动工作流');await waitFor(()=>document.querySelector('select[aria-label="查看运行"]')?.options.length===f.jobs.length+1);};
 f.select=async id=>{await choose('查看运行',id);await waitFor(()=>f.current()?.view.runId===id);};
 f.send=async message=>{await act(async()=>f.current().emit(message));};
 f.snapshot=()=>({viewOptions:Object.fromEntries(f.jobs.map(j=>[j.id,option('查看运行',j.id)])),stopOptions:Object.fromEntries(f.jobs.map(j=>[j.id,option('停止控制目标',j.id)])),selectedView:document.querySelector('select[aria-label="查看运行"]').value,selectedStop:document.querySelector('select[aria-label="停止控制目标"]').value,authority:authority(),renderer:renderedState(),count:document.querySelector('[data-component-id="count"]')?.textContent,catalogReads:f.requests.filter(r=>r.path==='/api/runtimes/configured/runs').length,jobReads:f.requests.filter(r=>r.path.startsWith('/api/runs/')&&r.method==='GET'),alerts:[...document.querySelectorAll('[role="alert"]')].map(x=>x.textContent)});
 f.releaseState=async snapshot=>{const held=f.heldState;assert.ok(held);f.heldState=undefined;await act(async()=>held.release(snapshot));};
 f.releaseCatalog=async()=>{const release=f.heldCatalog;assert.ok(release);f.heldCatalog=undefined;await act(async()=>release());};
 f.releaseWorkflows=async()=>{const held=f.heldWorkflows.shift();assert.ok(held);await act(async()=>held.release());};
 f.workflowReads=()=>f.requests.filter(r=>r.path==='/api/runtimes/configured/workflows').length;
 f.unmount=async()=>{await act(async()=>root.unmount());};
 f.close=async()=>{await f.unmount();if(f.heldState)await f.releaseState();if(f.heldCatalog)await f.releaseCatalog();while(f.heldWorkflows.length)await f.releaseWorkflows();};
 return f;
}

test('ordinary Start updates both run selectors and original command status on official RUNNING event',async()=>{
 const f=await fixture();try{
  await f.start();await f.select('ordinary');const before=f.snapshot();
  f.jobs=[job('ordinary','RUNNING')];await f.send(stateOf(f.jobs[0]));await f.send(result('ordinary',7));
  assert.match(renderedState(),/RUNNING/,'message reached production renderer');
  assert.match(f.snapshot().count,/7/,'normal result updates without refresh');
  assert.match(option('查看运行','ordinary'),/running/i);assert.match(option('停止控制目标','ordinary'),/running/i);
  assert.match(authority(),/权威状态：RUNNING/);assert.equal(f.snapshot().catalogReads,before.catalogReads,'state event does not refresh catalogs');
 }finally{await f.close();}
});

test('source-selected Dispatch applies consumer state only to consumer and source state only to source',async()=>{
 const f=await fixture('dispatch');try{
  await f.start();await f.select('root');await choose('停止控制目标','root');
  f.jobs=[job('root'),job('consumer','COMPLETED',{active:false,ownsResources:false,allowedActions:['view']})];
  await f.send(stateOf(f.jobs[1]));
  assert.match(authority(),/权威状态：STARTING/,'consumer terminal must not overwrite source');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'root');
  assert.match(option('查看运行','consumer'),/completed/i);
  f.jobs[0]=job('root','RUNNING');await f.send(stateOf(f.jobs[0]));
  assert.match(authority(),/权威状态：RUNNING/);assert.match(option('查看运行','root'),/running/i);
 }finally{await f.close();}
});

test('consumer-selected Dispatch refreshes the explicitly controlled source through its own state read',async()=>{
 const f=await fixture('dispatch');try{
  await f.start();await f.select('consumer');await choose('停止控制目标','root');
  const before=f.snapshot();f.jobs=[job('root','RUNNING'),job('consumer','RUNNING')];
  await f.send(stateOf(f.jobs[1]));await f.send(result('consumer',11));
  await waitFor(()=>/权威状态：RUNNING/.test(authority()));
  assert.match(option('查看运行','consumer'),/running/i);assert.match(option('查看运行','root'),/running/i);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'consumer');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'root');
  assert.equal(f.snapshot().catalogReads,before.catalogReads,'no periodic full catalog refresh');
  assert.ok(f.snapshot().jobReads.some(r=>r.path==='/api/runs/root'),'source status must be read for the source identity');
 }finally{await f.close();}
});

test('ordinary Stop displays ABORTED and actual resource retirement without changing control selection',async()=>{
 const f=await fixture();try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
  f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:true,allowedActions:['view']})];
  await f.send(stateOf(f.jobs[0]));
  assert.match(authority(),/权威状态：ABORTED/);assert.match(authority(),/持有资源：是/);
  f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']})];
  await f.send(stateOf(f.jobs[0]));
  assert.match(authority(),/持有资源：否/);assert.equal(button('停止选中运行').disabled,true);
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'ordinary');
 }finally{await f.close();}
});

test('state merge preserves Runtime/project/Job ownership and never moves selections',async t=>{
 t.mock.method(console,'warn',()=>{});
 const f=await fixture();try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  f.jobs=[job('ordinary','RUNNING')];await f.send(stateOf(f.jobs[0]));
  assert.match(authority(),/权威状态：RUNNING/);
  const invalid=job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']});
  for(const wrong of [{projectId:'other-project'},{jobId:'other-job'},{runtimeInstanceId:'replacement'}]){
   await f.send({...stateOf(invalid),...wrong});
   assert.match(option('查看运行','ordinary'),/running/i);
   assert.match(authority(),/权威状态：RUNNING/);assert.match(authority(),/持有资源：是/);
   assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'ordinary');
   assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'ordinary');
  }

 }finally{await f.close();}
});

test('unsubscribed command polling continues through cleanup and stops after both resource classes retire',async()=>{
 const f=await fixture('dispatch');try{
  await f.start();await f.select('consumer');
  f.jobs=[job('root','ABORTED',{active:false,ownsResources:true,displayResourcesReleased:false,allowedActions:['view']}),job('consumer','COMPLETED')];
  await waitFor(()=>/权威状态：ABORTED/.test(authority()));assert.match(authority(),/持有资源：是/);
  f.jobs[0]=job('root','ABORTED',{active:false,ownsResources:false,displayResourcesReleased:false,allowedActions:['view']});
  await waitFor(()=>/持有资源：否/.test(authority()));
  const awaitingDisplay=f.snapshot().jobReads.length;
  await act(async()=>{await new Promise(r=>setTimeout(r,1050));});
  assert.ok(f.snapshot().jobReads.length>awaitingDisplay,'retained display state must continue being observed');
  f.jobs[0]=job('root','ABORTED',{active:false,ownsResources:false,displayResourcesReleased:true,allowedActions:[]});
  await waitFor(()=>/展示资源已释放：是/.test(authority()));
  const retiredReads=f.snapshot().jobReads.length;
  await act(async()=>{await new Promise(r=>setTimeout(r,1250));});

  assert.equal(f.snapshot().jobReads.length,retiredReads,'fully retired historical command Job is no longer polled');
 }finally{await f.close();}
});

test('switching to the source subscription cancels its polling and ignores a late response',async()=>{
 const f=await fixture('dispatch');try{
  await f.start();await f.select('consumer');f.holdNextState=true;
  await waitFor(()=>f.heldState);const signal=f.heldState.signal;
  await f.select('root');assert.equal(signal.aborted,true);
  await f.send(stateOf(job('root','RUNNING')));
  await f.releaseState(job('root','STARTING'));
  assert.match(authority(),/权威状态：RUNNING/,'retired HTTP read cannot overwrite the current stream');
  const reads=f.snapshot().jobReads.length;
  await act(async()=>{await new Promise(r=>setTimeout(r,1100));});
  assert.equal(f.snapshot().jobReads.length,reads,'a covering view supplies source state without polling');
  await f.select('consumer');
  await waitFor(()=>f.snapshot().jobReads.length>reads);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'consumer');
 }finally{await f.close();}
});

test('session end, identity change and unmount retire the original command poll',async t=>{
 for(const exit of ['logout','instance','project','unmount'])await t.test(exit,async()=>{
  const f=await fixture('dispatch');try{
   await f.start();await f.select('consumer');f.holdNextState=true;
   await waitFor(()=>f.heldState);const signal=f.heldState.signal;
   if(exit==='logout')await click('退出');
   else if(exit==='unmount')await f.unmount();
   else{
    if(exit==='instance')f.runtimeInstanceId='replacement';else f.projectId='other-project';
    f.jobs=[job('root','STARTING',{runtimeInstanceId:f.runtimeInstanceId,projectId:f.projectId})];
    await click('刷新页面包和状态');
    await waitFor(()=>document.querySelector('[aria-label="当前 Runtime 身份"]').textContent.includes(exit==='instance'?'replacement':'other-project'));
    assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'');
    assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'');
   }
   assert.equal(signal.aborted,true);
   await f.releaseState(job('root','RUNNING'));
   assert.equal(authority(),undefined,'old command identity does not reappear');
   if(exit==='instance' || exit==='project')assert.match(option('查看运行','root'),/starting/i);
   const reads=f.requests.filter(r=>r.path==='/api/runs/root').length;
   await act(async()=>{await new Promise(r=>setTimeout(r,1100));});
   assert.equal(f.requests.filter(r=>r.path==='/api/runs/root').length,reads);
   assert.equal(f.requests.filter(r=>r.path==='/api/runs/start').length,1,'reads never replay the command');
  }finally{await f.close();}
 });
});

test('accepted Start remains observable when its first directory refresh fails',async()=>{
 const f=await fixture();try{
  f.failNextCatalog=true;await click('启动工作流');
  await waitFor(()=>f.snapshot().alerts.some(text=>text.includes('catalog temporarily unavailable')));
  f.jobs=[job('ordinary','RUNNING')];
  await waitFor(()=>/权威状态：RUNNING/.test(authority()));
  assert.match(option('查看运行','ordinary'),/running/i);
  assert.match(option('停止控制目标','ordinary'),/running/i);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'');
  assert.equal(button('停止选中运行').disabled,true);
  assert.equal(f.requests.filter(r=>r.path==='/api/runs/start').length,1);
 }finally{await f.close();}
});

test('a delayed directory keeps newer streamed Job state while refreshing its metadata',async()=>{
 const f=await fixture();try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  f.jobs=[job('ordinary','RUNNING',{name:'Refreshed name'})];
  f.holdNextCatalog=true;await click('刷新页面包和状态');await waitFor(()=>f.heldCatalog);
  await f.send(stateOf(job('ordinary','ABORTED',{active:false,ownsResources:false,displayResourcesReleased:true,allowedActions:[]})));
  await f.releaseCatalog();await waitFor(()=>!button('刷新页面包和状态').disabled);
  assert.match(option('查看运行','ordinary'),/Refreshed name · aborted/i);
  assert.match(authority(),/权威状态：ABORTED/);assert.match(authority(),/持有资源：否/);
  assert.match(authority(),/展示资源已释放：是/);assert.match(renderedState(),/ABORTED/);
  assert.equal(button('停止选中运行').disabled,true);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'ordinary');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'ordinary');
 }finally{await f.close();}
});

test('a delayed directory cannot roll back a fully retired command after polling has stopped',async()=>{
 const f=await fixture('dispatch');try{
  await f.start();await f.select('consumer');await choose('停止控制目标','root');
  f.jobs=[job('root','STOPPING',{name:'Root metadata'}),job('consumer','RUNNING')];
  f.holdNextCatalog=true;await click('刷新页面包和状态');await waitFor(()=>f.heldCatalog);
  f.jobs[0]=job('root','ABORTED',{active:false,ownsResources:false,displayResourcesReleased:true,allowedActions:[]});
  await waitFor(()=>/展示资源已释放：是/.test(authority()));
  const reads=f.snapshot().jobReads.length;
  await f.releaseCatalog();await waitFor(()=>!button('刷新页面包和状态').disabled);
  assert.match(option('查看运行','root'),/Root metadata · aborted/i);
  assert.match(authority(),/权威状态：ABORTED/);assert.match(authority(),/持有资源：否/);
  assert.match(authority(),/展示资源已释放：是/);assert.equal(button('停止选中运行').disabled,true);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'consumer');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'root');
  await act(async()=>{await new Promise(r=>setTimeout(r,1100));});
  assert.equal(f.snapshot().jobReads.length,reads,'retired command does not depend on another poll to correct the catalog');
 }finally{await f.close();}
});

test('a delayed directory cannot remove a newly observed terminal Job absent from its snapshot',async()=>{
 const f=await fixture();try{
  f.nextCatalogSnapshot=[];f.holdNextCatalog=true;
  await click('启动工作流');await waitFor(()=>f.heldCatalog);
  f.jobs=[job('ordinary','COMPLETED',{active:false,ownsResources:false,displayResourcesReleased:true,allowedActions:[]})];
  await waitFor(()=>/展示资源已释放：是/.test(authority()));
  const reads=f.snapshot().jobReads.length;
  await f.releaseCatalog();await waitFor(()=>!button('刷新页面包和状态').disabled);
  assert.match(option('查看运行','ordinary'),/completed/i);
  assert.match(authority(),/权威状态：COMPLETED/);assert.match(authority(),/展示资源已释放：是/);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'');
  await act(async()=>{await new Promise(r=>setTimeout(r,1100));});
  assert.equal(f.snapshot().jobReads.length,reads,'terminal observation remains without restarting its poll');
 }finally{await f.close();}
});

test('a delayed directory cannot clear a selection confirmed by a newer state observation',async()=>{
 const f=await fixture();try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  f.nextCatalogSnapshot=[];f.holdNextCatalog=true;
  await click('刷新页面包和状态');await waitFor(()=>f.heldCatalog);
  await f.send(stateOf(job('ordinary','ABORTED',{active:false,ownsResources:false,displayResourcesReleased:true,allowedActions:[]})));
  await f.releaseCatalog();await waitFor(()=>!button('刷新页面包和状态').disabled);
  assert.match(option('查看运行','ordinary'),/aborted/i);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'ordinary');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'ordinary');
  assert.match(renderedState(),/ABORTED/);
 }finally{await f.close();}
});

test('Start admission refreshes after execution retirement without a manual refresh',async t=>{
 for(const kind of ['ordinary','dispatch'])await t.test(kind,async()=>{
  const f=await fixture(kind,{admission:true});try{
   const controlled=kind==='ordinary'?'ordinary':'root',viewed=kind==='ordinary'?'ordinary':'consumer';
   await f.start();await f.select(viewed);await choose('停止控制目标',controlled);
   await writeInputs('{"nextBatch":9007199254740993}');
   await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
   assert.equal(button('启动工作流').disabled,true,'accepted Stop is not resource retirement');
   assert.match(document.body.textContent,/E_MAX_CONCURRENT_JOBS/);
   const before=f.snapshot(),views=f.views.size,workflowReads=f.workflowReads();
   f.jobs=f.jobs.map(run=>job(run.id,'ABORTED',{active:false,ownsResources:false,displayResourcesReleased:run.id==='root',allowedActions:run.id==='root'?[]:['view']}));
   if(kind==='ordinary')await f.send(stateOf(f.jobs[0]));
   // The consumer view does not subscribe to root: its existing exact-run
   // observer must trigger the same admission refresh as ordinary job_state.
   await waitFor(()=>/持有资源：否/.test(authority()));
   await waitFor(()=>!button('启动工作流').disabled);
   assert.doesNotMatch(document.body.textContent,/E_MAX_CONCURRENT_JOBS/);
   assert.equal(document.querySelector('textarea[aria-label="工作流输入"]').value,'{"nextBatch":9007199254740993}');
   assert.equal(document.querySelector('select[aria-label="查看运行"]').value,viewed);
   assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,controlled);
   assert.equal(f.views.size,views,'admission refresh does not reopen the selected view');
   assert.equal(f.snapshot().catalogReads,before.catalogReads,'retirement reads only workflow admission');
   assert.equal(f.workflowReads(),workflowReads+1);
   for(let i=0;i<3;i++)await f.send(stateOf(f.jobs.find(run=>run.id===viewed)));
   const after=f.workflowReads();
   await act(async()=>{await new Promise(resolve=>setTimeout(resolve,1100));});
   assert.equal(f.workflowReads(),after,'repeated terminal snapshots do not poll workflows');
   assert.equal(f.requests.filter(r=>r.path==='/api/runs/start').length,1,'admission reads do not replay Start');
   assert.equal(f.requests.filter(r=>r.path.endsWith('/stop')).length,1,'admission reads do not replay Stop');
  }finally{await f.close();}
 });
});

test('retired selected Job does not override Runtime denial while another Job owns capacity',async()=>{
 const f=await fixture('ordinary',{admission:true});try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
  const reads=f.workflowReads();
  f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']}),job('other','RUNNING')];
  await f.send(stateOf(f.jobs[0]));await waitFor(()=>f.workflowReads()>reads);
  assert.match(authority(),/持有资源：否/);
  assert.equal(button('启动工作流').disabled,true);
  assert.match(document.body.textContent,/E_MAX_CONCURRENT_JOBS/);
  const stable=f.workflowReads();
  await act(async()=>{await new Promise(resolve=>setTimeout(resolve,1100));});
  assert.equal(f.workflowReads(),stable,'a legitimate denial does not cause endless retry');
 }finally{await f.close();}
});

test('a late full catalog cannot overwrite admission refreshed after resource retirement',async()=>{
 const f=await fixture('ordinary',{admission:true});try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
  f.holdNextCatalog=true;await click('刷新页面包和状态');await waitFor(()=>f.heldCatalog);
  const reads=f.workflowReads();
  f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']})];
  await f.send(stateOf(f.jobs[0]));await waitFor(()=>f.workflowReads()>reads && !document.body.textContent.includes('E_MAX_CONCURRENT_JOBS'));
  await f.releaseCatalog();await waitFor(()=>!button('刷新页面包和状态').disabled);
  assert.equal(button('启动工作流').disabled,false,'old pre-retirement workflow data cannot re-disable Start');
  assert.doesNotMatch(document.body.textContent,/E_MAX_CONCURRENT_JOBS/);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'ordinary');
  assert.equal(document.querySelector('select[aria-label="停止控制目标"]').value,'ordinary');
 }finally{await f.close();}
});

test('a late admission response cannot replace a newer Runtime, project or configuration catalog',async t=>{
 for(const changed of ['instance','project','configuration'])await t.test(changed,async()=>{
  const f=await fixture('ordinary',{admission:true});try{
   await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
   await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
   f.holdNextWorkflows=true;
   f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']})];
   await f.send(stateOf(f.jobs[0]));await waitFor(()=>f.heldWorkflows.length);
   const signal=f.heldWorkflows[0].signal;
   if(changed==='instance')f.runtimeInstanceId='replacement';
   else if(changed==='project')f.projectId='other-project';
   else f.configurationRevision='3';
   f.jobs=[job('ordinary','RUNNING',{runtimeInstanceId:f.runtimeInstanceId,projectId:f.projectId})];
   await click('刷新页面包和状态');await waitFor(()=>!button('刷新页面包和状态').disabled);
   assert.equal(signal.aborted,true);
   await f.releaseWorkflows();
   if(changed!=='configuration')await choose('启动工作流目标','main');
   assert.equal(button('启动工作流').disabled,true,'old allowed result cannot override current denied admission');
   assert.match(document.body.textContent,/E_MAX_CONCURRENT_JOBS/);
   assert.equal(f.requests.filter(r=>r.path==='/api/runs/start').length,1);
  }finally{await f.close();}
 });
});

test('failed admission refresh preserves the official denial and offers normal explicit recovery',async()=>{
 const f=await fixture('ordinary',{admission:true});try{
  await f.start();await f.select('ordinary');await choose('停止控制目标','ordinary');
  await click('停止选中运行');await waitFor(()=>/权威状态：STOPPING/.test(authority()));
  f.failNextWorkflows=true;
  f.jobs=[job('ordinary','ABORTED',{active:false,ownsResources:false,allowedActions:['view']})];
  await f.send(stateOf(f.jobs[0]));
  await waitFor(()=>f.snapshot().alerts.some(text=>text.includes('admission temporarily unavailable')));
  assert.equal(button('启动工作流').disabled,true);
  await click('刷新页面包和状态');await waitFor(()=>!button('启动工作流').disabled);
  assert.equal(f.snapshot().alerts.length,0);
  assert.equal(document.querySelector('select[aria-label="查看运行"]').value,'ordinary');
 }finally{await f.close();}
});
