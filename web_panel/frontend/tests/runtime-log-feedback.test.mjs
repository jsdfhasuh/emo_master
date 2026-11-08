// Production RuntimeLogs and React effects; only HTTP/download boundaries are controlled.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import {JSDOM} from 'jsdom';

const require=createRequire(import.meta.url);
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
for(const key of ['window','document','HTMLElement','HTMLInputElement','Event','MouseEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,path);
const {RuntimeLogs}=require('../src/components/RuntimeLogs.tsx');

const button=name=>[...document.querySelectorAll('button')].find(el=>el.textContent===name);
const alerts=()=>[...document.querySelectorAll('[role="alert"]')].map(el=>el.textContent);
const feedback=()=>[...document.querySelectorAll('[role="status"]')].map(el=>el.textContent).join(' ');
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const failure=(code,message)=>json({ok:false,code,message},409);
const file=()=>new Response('{"eventId":"1"}\n',{headers:{'Content-Type':'application/x-ndjson'}});
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve};};
async function click(name){
 const el=button(name);assert.ok(el,name);assert.equal(el.disabled,false,name);
 await act(async()=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
}
async function settle(){await act(async()=>{await new Promise(resolve=>setImmediate(resolve))});}
let fixtureId=0;
async function fixture({queryFailure}={}){
 const originalFetch=globalThis.fetch,originalClick=dom.window.HTMLAnchorElement.prototype.click;
 const f={props:{runtimeId:'runtime-'+(++fixtureId),runtimeInstanceId:'instance-a'},queryFailure,reads:[],exports:[],downloads:0,nextExport:undefined};
 globalThis.fetch=async(raw,options={})=>{
  const url=new URL(raw,'http://localhost'),call={url,signal:options.signal};
  if(url.pathname.endsWith('/logs/export')){
   f.exports.push(call);
   const respond=f.nextExport;f.nextExport=undefined;
   return respond?respond(call):file();
  }
  assert.ok(url.pathname.endsWith('/logs'),'unexpected HTTP '+url.pathname);
  f.reads.push(call);
  if(f.queryFailure)return failure('QUERY_UNAVAILABLE',f.queryFailure);
  return json({ok:true,code:'OK',message:'',runtimeInstanceId:f.props.runtimeInstanceId,upperEventId:'10',nextEventId:'10',hasMore:false,events:[]});
 };
 dom.window.HTMLAnchorElement.prototype.click=function(){f.downloads++;};
 const root=createRoot(document.getElementById('root'));
 f.render=async changes=>{
  Object.assign(f.props,changes);
  await act(async()=>root.render(React.createElement(RuntimeLogs,{...f.props})));
  await settle();
 };
 f.filter=async value=>{
  const input=[...document.querySelectorAll('label')].find(el=>el.textContent==='工程').querySelector('input');
  await act(async()=>{
   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);
   input.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await click('应用筛选');await settle();
 };
 f.close=async()=>{
  await act(async()=>root.unmount());
  globalThis.fetch=originalFetch;dom.window.HTMLAnchorElement.prototype.click=originalClick;
 };
 try{
  await f.render();
  await act(async()=>{
   const select=document.querySelector('select[aria-label="范围"]');select.value='history';select.dispatchEvent(new Event('change',{bubbles:true}));
  });
  await settle();
  return f;
 }catch(error){await f.close();throw error;}
}

test('successful log export retry removes its previous failure and performs one download',async()=>{
 const f=await fixture();
 try{
  f.nextExport=()=>failure('DEADLINE_EXCEEDED','Deadline Exceeded');
  await click('导出筛选历史 JSONL');await settle();
  assert.ok(alerts().some(text=>text.includes('DEADLINE_EXCEEDED')));
  assert.equal(f.downloads,0);
  await click('导出筛选历史 JSONL');await settle();
  assert.equal(f.exports.length,2);assert.equal(f.downloads,1);
  assert.match(feedback(),/JSONL 已下载/);
  assert.deepEqual(alerts(),[],'a successful retry must clear the old export failure');
 }finally{await f.close();}
});

test('export failure, successful retry and cancellation preserve an independent query failure',async()=>{
 const f=await fixture({queryFailure:'查询服务暂不可用'});
 try{
  assert.ok(alerts().some(text=>text.includes('QUERY_UNAVAILABLE')));
  f.nextExport=()=>failure('DEADLINE_EXCEEDED','Deadline Exceeded');
  await click('导出筛选历史 JSONL');await settle();
  assert.ok(alerts().some(text=>text.includes('日志查询失败')&&text.includes('QUERY_UNAVAILABLE')),'export must not overwrite a query failure');
  assert.ok(alerts().some(text=>text.includes('日志导出失败')&&text.includes('DEADLINE_EXCEEDED')),'export errors identify their own operation');
  await click('导出筛选历史 JSONL');await settle();
  assert.equal(f.downloads,1);
  assert.equal(alerts().length,1);assert.match(alerts()[0],/日志查询失败.*QUERY_UNAVAILABLE/);
  const pending=deferred();f.nextExport=()=>pending.promise;
  await click('导出筛选历史 JSONL');await click('取消导出');
  assert.equal(f.exports.at(-1).signal.aborted,true);
  await act(async()=>pending.resolve(file()));await settle();
  assert.equal(f.downloads,1,'a cancelled export cannot create a late download');
  assert.equal(alerts().length,1);assert.match(alerts()[0],/日志查询失败.*QUERY_UNAVAILABLE/);
  assert.equal(feedback(),'导出已取消');
 }finally{await f.close();}
});

test('a successful query refresh does not erase the last export failure',async()=>{
 const f=await fixture();
 try{
  f.nextExport=()=>failure('DEADLINE_EXCEEDED','Deadline Exceeded');
  await click('导出筛选历史 JSONL');await settle();
  const reads=f.reads.length;
  await click('重新查询');await settle();
  assert.ok(f.reads.length>reads,'a real query refresh must complete');
  assert.equal(alerts().length,1);assert.match(alerts()[0],/日志导出失败.*DEADLINE_EXCEEDED/);
 }finally{await f.close();}
});

for(const target of ['runtime','instance','filter'])test(`changing ${target} retires the old export state and rejects an in-flight old download`,async()=>{
 const f=await fixture();
 let filterRevision=0;
 const change=()=>target==='runtime'?f.render({runtimeId:f.props.runtimeId+'-next'}):target==='instance'?f.render({runtimeInstanceId:f.props.runtimeInstanceId+'-next'}):f.filter('project-'+(++filterRevision));
 try{
  f.nextExport=()=>failure('DEADLINE_EXCEEDED','Deadline Exceeded');
  await click('导出筛选历史 JSONL');await settle();
  assert.ok(alerts().some(text=>text.includes('DEADLINE_EXCEEDED')));
  await change();
  assert.deepEqual(alerts(),[],'old export errors must not follow the target');
  const pending=deferred();f.nextExport=()=>pending.promise;
  await click('导出筛选历史 JSONL');
  const old=f.exports.at(-1);
  await change();
  assert.equal(old.signal.aborted,true);
  assert.equal(button('导出筛选历史 JSONL').disabled,false,'the new target can export without waiting for the retired request');
  await act(async()=>pending.resolve(file()));await settle();
  assert.equal(f.downloads,0);assert.equal(feedback(),'');assert.deepEqual(alerts(),[]);
 }finally{await f.close();}
});
