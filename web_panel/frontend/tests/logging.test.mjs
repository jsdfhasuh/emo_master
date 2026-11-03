import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
const require=createRequire(import.meta.url);
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(m,p)=>m._compile(ts.transpileModule(readFileSync(p,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,p);
const {mergeRuntimeLogs}=require('../src/components/RuntimeLogs.tsx');
const {apiBlob,ApiError,setCsrf,onSessionExpired}=require('../src/data/api.ts');
test('global event IDs stay lossless, historical pages retain more than 100 and Job sequence is independent',()=>{
 const events=Array.from({length:220},(_,i)=>({eventId:(9007199254740993n+BigInt(i)).toString(),sequence:'1'}));
 const page={ok:true,events,nextEventId:events.at(-1).eventId,upperEventId:events.at(-1).eventId};
 const result=mergeRuntimeLogs([],page);assert.equal(result.length,220);assert.equal(result[0].eventId,'9007199254740993');
 assert.equal(mergeRuntimeLogs(result,{...page,events:[events[0]]}).length,220);
 assert.throws(()=>mergeRuntimeLogs(result,{...page,ok:false,code:'EVENT_TOO_LARGE',message:'too large'}),e=>e.code==='EVENT_TOO_LARGE');
});
test('Blob shares credentials, CSRF, abort, structured errors and current-session 401 handling',async()=>{
 const original=globalThis.fetch;let expired=0;const off=onSessionExpired(()=>expired++);setCsrf('csrf-test');
 try{
  const abort=new AbortController();let options;
  globalThis.fetch=async(path,init)=>{options=init;return new Response('{"eventId":"9007199254740993"}\n')};
  const blob=await apiBlob('/api/runtimes/r/logs/export',{method:'GET',signal:abort.signal});
  assert.equal(await blob.text(),'{"eventId":"9007199254740993"}\n');assert.equal(options.credentials,'same-origin');assert.equal(options.signal,abort.signal);
  globalThis.fetch=async()=>new Response('{"ok":false,"code":"RESET_REQUIRED","message":"Changed","runtimeInstanceId":"new"}',{status:409});
  await assert.rejects(apiBlob('/api/runtimes/r/logs/export'),e=>e instanceof ApiError&&e.code==='RESET_REQUIRED'&&e.runtimeInstanceId==='new');
  globalThis.fetch=async()=>new Response('{"detail":"Login required"}',{status:401});
  await assert.rejects(apiBlob('/api/runtimes/r/logs/export'));assert.equal(expired,1);
  abort.abort();globalThis.fetch=async(path,init)=>{assert.equal(init.signal.aborted,true);throw new DOMException('Aborted','AbortError')};
  await assert.rejects(apiBlob('/api/runtimes/r/logs/export',{signal:abort.signal}),e=>e.name==='AbortError');
 }finally{off();globalThis.fetch=original;setCsrf('')}
});

// Runs the production component/effects with explicit React state doubles.
function componentHarness() {
 const source=readFileSync(new URL('../src/components/RuntimeLogs.tsx',import.meta.url),'utf8');
 const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const requests=[],timers=new Map();let active,nextTimer=0;
 const hooks={
  useState(initial){const runner=active,index=runner.index++;if(!runner.slots[index])runner.slots[index]={value:typeof initial==='function'?initial():initial};return [runner.slots[index].value,next=>{const old=runner.slots[index].value,value=typeof next==='function'?next(old):next;if(!Object.is(old,value)){runner.slots[index].value=value;runner.dirty=true}}]},
  useRef(initial){const runner=active,index=runner.index++;if(!runner.slots[index])runner.slots[index]={current:initial};return runner.slots[index]},
  useEffect(effect,deps){const runner=active,index=runner.index++,previous=runner.slots[index];if(!previous||deps.some((value,i)=>!Object.is(value,previous.deps[i])))runner.pending.push({index,effect,deps})}
 };
 const apiMock={ApiError,getSessionGeneration:()=>1,onSessionExpired:()=>()=>{},apiBlob:()=>{throw Error('unexpected download')},api:(path,options)=>new Promise((resolve,reject)=>requests.push({path,query:Object.fromEntries(new URL('http://fixture'+path).searchParams),signal:options.signal,resolve,reject}))};
 const module={exports:{}};
 const context={exports:module.exports,module,require:id=>id==='react'?hooks:id==='../data/api'?apiMock:require(id),AbortController,URLSearchParams,Map,BigInt,Date,Error,setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer},clearTimeout:id=>timers.delete(id)};
 require('node:vm').runInNewContext(compiled,context);
 class Runner {
  constructor(props){this.props=props;this.slots=[];this.dirty=true}
  flush(){for(let turn=0;this.dirty;turn++){assert.ok(turn<30,'component settles');this.dirty=false;this.index=0;this.pending=[];active=this;this.tree=module.exports.RuntimeLogs(this.props);for(const item of this.pending)this.slots[item.index]?.cleanup?.();for(const item of this.pending)this.slots[item.index]={deps:item.deps,cleanup:item.effect()}}}
  update(props){this.props={...this.props,...props};this.dirty=true;this.flush()}
  unmount(){for(const slot of this.slots)slot?.cleanup?.()}
 }
 const nodes=(node,type,out=[])=>{if(Array.isArray(node)){node.forEach(item=>nodes(item,type,out));return out}if(node&&typeof node==='object'){if(node.type===type)out.push(node);nodes(node.props?.children,type,out)}return out};
 return {requests,timers,create:props=>{const runner=new Runner(props);runner.flush();return runner},async settle(runner){for(let i=0;i<5;i++){await new Promise(resolve=>setImmediate(resolve));runner.flush()}},ids:runner=>Array.from(nodes(runner.tree,'tbody')[0]?.props.children||[],row=>row.key),changeMode(runner,mode){nodes(runner.tree,'select')[0].props.onChange({target:{value:mode}});runner.flush()},nextPage(runner){nodes(runner.tree,'button').find(button=>button.props.children==='下一页').props.onClick();runner.flush()},poll(){const [id,fn]=timers.entries().next().value;timers.delete(id);fn()}};
}
function logPage({instance='instance-a',upper='10',next=upper,ids=[],more=false}={}) {
 return {ok:true,code:'OK',message:'',runtimeInstanceId:instance,upperEventId:upper,nextEventId:next,hasMore:more,
  events:ids.map(eventId=>({eventId:String(eventId),sequence:'1',timestampMs:'1793664000000',originApp:'runtime',component:'operator',jobId:'job',projectId:'project',nodeId:'node',level:'INFO',code:'TEST',message:'event-'+eventId,payload:{}}))};
}
test('late status confirms the log instance without losing rows, current upper bound or consumed cursor',async()=>{
 const h=componentHarness(),runner=h.create({runtimeId:'runtime-a'});
 h.requests[0].resolve(logPage({upper:'10'}));await h.settle(runner);
 h.requests[1].resolve(logPage({upper:'12',next:'11',ids:[11],more:true}));await h.settle(runner);
 assert.deepEqual(h.ids(runner),['11']);assert.equal(h.requests[2].query.upperEventId,'12');
 runner.update({runtimeInstanceId:'instance-a'});await h.settle(runner);
 assert.equal(h.requests.length,3);assert.equal(h.requests[2].signal.aborted,false);assert.deepEqual(h.ids(runner),['11']);
 h.requests[2].resolve(logPage({upper:'12',ids:[12]}));await h.settle(runner);
 assert.deepEqual(h.ids(runner),['11','12']);h.poll();await h.settle(runner);
 assert.equal(h.requests[3].query.afterEventId,'12');assert.equal(h.requests[3].query.upperEventId,undefined);
 assert.equal(h.requests.filter(item=>item.query.limit==='1').length,1);runner.unmount();
});
test('status arriving first is used by one baseline and does not recapture the live starting point',async()=>{
 const h=componentHarness(),runner=h.create({runtimeId:'runtime-a',runtimeInstanceId:'instance-a'});
 assert.equal(h.requests[0].query.runtimeInstanceId,'instance-a');
 h.requests[0].resolve(logPage({upper:'10'}));await h.settle(runner);
 h.requests[1].resolve(logPage({upper:'11',ids:[11]}));await h.settle(runner);
 runner.update({runtimeInstanceId:'instance-a'});await h.settle(runner);
 assert.deepEqual(h.ids(runner),['11']);assert.equal(h.requests.filter(item=>item.query.limit==='1').length,1);runner.unmount();
});
test('actual instance change aborts old reads and rejects their late responses before rebuilding',async()=>{
 const h=componentHarness(),runner=h.create({runtimeId:'runtime-a',runtimeInstanceId:'instance-a'});
 h.requests[0].resolve(logPage({upper:'10'}));await h.settle(runner);
 h.requests[1].resolve(logPage({upper:'11',ids:[11]}));await h.settle(runner);h.poll();await h.settle(runner);
 const old=h.requests[2];runner.update({runtimeInstanceId:'instance-b'});await h.settle(runner);
 assert.equal(old.signal.aborted,true);assert.deepEqual(h.ids(runner),[]);
 const baseline=h.requests[3];assert.equal(baseline.query.limit,'1');assert.equal(baseline.query.runtimeInstanceId,'instance-b');
 old.resolve(logPage({upper:'12',ids:[12]}));await h.settle(runner);assert.deepEqual(h.ids(runner),[]);
 baseline.resolve(logPage({instance:'instance-b',upper:'20'}));await h.settle(runner);
 h.requests[4].resolve(logPage({instance:'instance-b',upper:'21',ids:[21]}));await h.settle(runner);
 assert.deepEqual(h.ids(runner),['21']);assert.equal(h.requests[4].query.afterEventId,'20');runner.unmount();
});
test('live display and reopened cache are bounded while the cursor and complete history remain intact',async()=>{
 const h=componentHarness(),runner=h.create({runtimeId:'runtime-a',runtimeInstanceId:'instance-a'});
 h.requests[0].resolve(logPage({upper:'0'}));await h.settle(runner);
 for(let page=0;page<6;page++){
  h.requests[page+1].resolve(logPage({upper:'1200',next:String((page+1)*200),ids:Array.from({length:200},(_,i)=>page*200+i+1),more:page<5}));await h.settle(runner);
 }
 assert.equal(h.ids(runner).length,1000);assert.equal(h.ids(runner)[0],'201');assert.equal(h.ids(runner).at(-1),'1200');runner.unmount();
 const reopened=h.create({runtimeId:'runtime-a',runtimeInstanceId:'instance-a'});await h.settle(reopened);
 assert.equal(h.ids(reopened).length,1000);assert.equal(h.requests[7].query.afterEventId,'1200');assert.equal(h.requests[7].query.limit,'200');
 h.changeMode(reopened,'history');await h.settle(reopened);
 assert.equal(h.requests[7].signal.aborted,true);assert.equal(h.requests[8].query.afterEventId,'0');
 for(let page=0;page<6;page++){
  if(page){h.nextPage(reopened);await h.settle(reopened)}
  h.requests[page+8].resolve(logPage({upper:'1200',next:String((page+1)*200),ids:Array.from({length:200},(_,i)=>page*200+i+1),more:page<5}));await h.settle(reopened);
 }
 assert.equal(h.ids(reopened).length,1200);assert.equal(h.ids(reopened)[0],'1');assert.equal(h.ids(reopened).at(-1),'1200');reopened.unmount();
});
