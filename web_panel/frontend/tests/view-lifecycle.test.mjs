// Actual App/React with controlled HTTP and WebSocket boundaries. This exercises
// subscription ownership and recovery, not natural browser network scheduling.
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
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
const sourceRoot=fileURLToPath(new URL('../src/',import.meta.url));
const mainPath=require.resolve('../src/main.tsx');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,path)=>{
 let source=readFileSync(path,'utf8');
 if(path===mainPath)source=source.slice(0,source.lastIndexOf('createRoot(document'))+'\nexport {App};';
 module._compile(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,path);
};
require.extensions['.css']=()=>{};
const button=name=>[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===name);
const alerts=()=>[...document.querySelectorAll('.alert')].map(x=>x.textContent);
const value=()=>document.querySelector('[data-component-id="count"] .number')?.textContent.trim();
const connectionText=()=>document.querySelector('.status')?.textContent;
async function waitFor(check,limit=3000){const end=Date.now()+limit;while(Date.now()<end){if(check())return;await act(async()=>{await new Promise(r=>setTimeout(r,10));});}assert.ok(check(),'UI condition timed out');}
async function click(name){const el=button(name);assert.ok(el,`button ${name}`);assert.equal(el.disabled,false);await act(async()=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));}
async function choose(job){await act(async()=>{const el=document.querySelector('select[aria-label="查看运行"]');assert.ok(el);el.value=job;el.dispatchEvent(new Event('change',{bubbles:true}));});}
const scopeStatus=job=>({event:'scope_status',runtimeInstanceId:'runtime',projectId:'project',jobId:job,latestStartedOrdinals:{scope:'1'},expiredScopeOrdinals:{}});
function result(job,count=12,ordinal=1){return {event:'result',runtimeInstanceId:'runtime',projectId:'project',result:{identity:{runtimeInstanceId:'runtime',jobId:job,resultScopeId:'scope',resultKey:`${job}-${ordinal}`,resultOrdinal:ordinal},expectedSourceIds:['count'],sources:[{sourceId:'count',state:'AVAILABLE',valueJson:String(count)}]}};}
async function fixture(options={}){
 for(const path of Object.keys(require.cache))if(path.startsWith(sourceRoot))delete require.cache[path];
 sessionStorage.clear();
 const f={jobs:['first','second'],panelId:'panel-one',panelAvailable:true,sourceProblems:{},viewCounter:0,views:new Map(),sockets:[],requests:[],onViewRequest:undefined,holdNextView:false,releaseView:undefined,freezeFailure:undefined,...options};
 const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const panel=()=>({id:f.panelId,packageRevision:f.panelId,defaultPageId:'page',pages:[{id:'page',title:'Result',components:[{id:'count',type:'number',label:'Count',binding:{sourceId:'count',scopeId:'scope'}},{id:'extra',type:'number',label:'Extra',binding:{sourceId:'extra',scopeId:'scope'}}]}]});
 class Socket{
  constructor(url){this.url=url;this.view=f.views.get(url.split('/').at(-1));this.readyState=1;f.sockets.push(this);queueMicrotask(()=>this.onopen?.({}));}
  close(){if(this.readyState===3)return;this.readyState=3;queueMicrotask(()=>this.onclose?.({}));}
  emit(message){if(this.readyState!==1)return false;this.onmessage?.({data:typeof message==='string'?message:JSON.stringify(message)});return true;}
 }
 globalThis.WebSocket=Socket;
 globalThis.fetch=async(raw,options={})=>{
  const p=new URL(raw,location.origin).pathname;
  f.requests.push({path:p,method:options.method || 'GET'});
  if(p==='/api/health')return reply({localNoAuth:false});
  if(p==='/api/me')return reply({userId:'viewer',username:'viewer',role:'viewer',csrf:'synthetic'});
  if(p==='/api/runtimes')return reply([{id:'configured',mode:'grpc',permissions:[]}]);
  if(p==='/api/runtimes/configured/info')return reply({runtimeInstanceId:'runtime',projectId:'project',configurationRevision:'2'});
  if(p==='/api/runtimes/configured/runs')return reply(f.jobs.map(id=>({id,state:'RUNNING',runtimeInstanceId:'runtime',projectId:'project'})));
  if(p==='/api/runtimes/configured/workflows')return reply([]);
  if(p==='/api/panels')return reply(f.panelAvailable?[panel()]:[]);
  if(p.startsWith('/api/panels/'))return reply(panel());
  if(p==='/api/views' && options.method==='POST'){
   const body=JSON.parse(options.body),id='view-'+(++f.viewCounter);
   f.views.set(id,body);
   f.onViewRequest?.(body,f.sockets.filter(s=>s.readyState===1).at(-1));
   if(f.holdNextView){f.holdNextView=false;await new Promise(resolve=>{f.releaseView=resolve;});}
   return reply({id,sourceProblems:{...f.sourceProblems},runIds:f.allowedRunIds?.(body.runId) ?? (body.runId?[body.runId]:[])});
  }
  if(p.startsWith('/api/views/') && options.method==='DELETE')return reply({});
  if(p.endsWith('/freeze'))return f.freezeFailure?reply({detail:f.freezeFailure},409):reply({viewGeneration:'1'});
  if(p.endsWith('/resume'))return reply({viewGeneration:'2'});
  if(p==='/api/runtimes/configured/status')return reply({runtime:{runtimeInstanceId:'runtime',projectId:'project'},counters:{source:'runtime',counters:[]},events:{events:[],nextSequence:'0',source:'runtime'}});
  if(p.endsWith('/events'))return reply({events:[],nextSequence:'0',source:'runtime',runtimeInstanceId:'runtime',projectId:'project'});
  if(p.endsWith('/logs'))return reply({events:[],nextCursor:'0',highWatermark:'0',hasMore:false,runtimeInstanceId:'runtime'});
  throw Error('Unhandled HTTP '+p);
 };
 const {App}=require(mainPath),root=createRoot(document.getElementById('root'));
 await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(App))));
 await waitFor(()=>document.querySelector('select[aria-label="查看运行"]')?.options.length===f.jobs.length+1);
 f.current=()=>f.sockets.filter(s=>s.readyState===1).at(-1);
 f.select=async job=>{await choose(job);await waitFor(()=>f.current()?.view.runId===job);};
 f.send=async message=>{await act(async()=>f.current().emit(message));};
 f.release=async()=>{const done=f.releaseView;assert.ok(done);f.releaseView=undefined;await act(async()=>done());};
 f.close=async()=>{if(f.releaseView)await f.release();await act(async()=>root.unmount());};
 return f;
}

test('switching ordinary or consumer views retires an open old stream before it can reject a valid message',async t=>{
 for(const jobs of [['ordinary-old','ordinary-new'],['consumer-old','consumer-new']])await t.test(jobs[0],async()=>{
  const f=await fixture({jobs});
  try{
   await f.select(jobs[0]);await f.send(result(jobs[0]));assert.equal(value(),'12');
   let dispatched=false;f.holdNextView=true;
   f.onViewRequest=(_body,old)=>{queueMicrotask(()=>{dispatched=old.emit(scopeStatus(jobs[0]));});f.onViewRequest=undefined;};
   await choose(jobs[1]);await waitFor(()=>f.releaseView);
   assert.equal(dispatched,true,'controlled callback arrives while old socket is still open');
   assert.deepEqual(alerts(),[]);assert.equal(value(),undefined,'old scope does not reappear during selection');
   await f.release();await waitFor(()=>f.current()?.view.runId===jobs[1]);
   await f.send(result(jobs[1],15));assert.equal(value(),'15');assert.deepEqual(alerts(),[]);
  }finally{await f.close();}
 });
});

test('online publication keeps new coverage with its own view and stale socket events cannot overwrite the new status',async()=>{
 const f=await fixture();
 try{
  await f.select('first');await f.send(result('first'));
  const old=f.current();f.panelId='panel-two';f.sourceProblems={extra:'SOURCE_NOT_CAPTURED'};f.holdNextView=true;
  f.onViewRequest=()=>{queueMicrotask(()=>old.emit(scopeStatus('first')));f.onViewRequest=undefined;};
  await click('刷新页面包和状态');await waitFor(()=>f.releaseView);assert.deepEqual(alerts(),[]);
  await f.release();await waitFor(()=>f.current()!==old);await f.send(result('first',13,2));
  assert.equal(value(),'13');assert.match(document.querySelector('[data-component-id="extra"]').textContent,/SOURCE_NOT_CAPTURED/);
  const currentStatus=connectionText();
  await act(async()=>{old.onopen?.({});old.onerror?.({});old.onclose?.({});old.onmessage?.({data:'broken late JSON'});});
  assert.equal(connectionText(),currentStatus);assert.deepEqual(alerts(),[]);assert.equal(value(),'13');
 }finally{await f.close();}
});

test('only a displayed current result recovers subscription errors and preserves an unrelated operation error',async t=>{
 const warnings=[];t.mock.method(console,'warn',(...args)=>warnings.push(args));
 const f=await fixture();
 try{
  await f.select('first');await f.send(result('first'));
  f.freezeFailure='Freeze unavailable';await click('冻结查看');assert.ok(alerts().some(s=>s.includes('Freeze unavailable')));
  await f.send(scopeStatus('wrong-job'));assert.ok(alerts().some(s=>s.includes('收到无效消息')));
  await f.send({type:'heartbeat'});await f.send({type:'package_updated'});assert.ok(alerts().some(s=>s.includes('收到无效消息')));
  await f.send(result('first',15,2));assert.equal(value(),'15');
  assert.ok(alerts().some(s=>s.includes('Freeze unavailable')));assert.ok(!alerts().some(s=>s.includes('收到无效消息')));
  assert.equal(warnings.length,1);assert.match(warnings[0][1].message,/Wrong Job identity/);
 }finally{await f.close();}
});

test('scope/group allowlists remain authoritative, including an explicitly empty list',async t=>{
 t.mock.method(console,'warn',()=>{});
 for(const allowed of [['root','consumer'],[]])await t.test(allowed.length?'group':'empty',async()=>{
  const f=await fixture({jobs:['root','consumer'],allowedRunIds:()=>allowed});
  try{
   await f.select('root');await f.send(result('consumer'));
   if(allowed.length){assert.equal(value(),'12');assert.deepEqual(alerts(),[]);}
   else{assert.equal(value(),undefined);assert.ok(alerts().some(s=>s.includes('收到无效消息')));}
  }finally{await f.close();}
 });
});

test('reset_required still refreshes the catalog and reconnects after retiring the delivery generation',async()=>{
 const f=await fixture();
 try{
  await f.select('first');await f.send(result('first'));const old=f.current();
  await f.send({type:'reset_required'});assert.equal(old.readyState,3);assert.equal(value(),undefined);
  await waitFor(()=>f.current() && f.current()!==old,4000);
  await f.send(result('first',16,2));assert.equal(value(),'16');assert.deepEqual(alerts(),[]);
 }finally{await f.close();}
});
