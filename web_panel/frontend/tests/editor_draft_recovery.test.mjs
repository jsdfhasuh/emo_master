// Actual React components in jsdom, with a synthetic auth/CAS HTTP boundary.
// Included in npm test; dependencies are pinned by package-lock.json.
// This verifies event/state flows, not browser layout or Runtime/device operation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {JSDOM} from 'jsdom';
const require=createRequire(import.meta.url);
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/editor'});
for(const key of ['window','document','location','history','sessionStorage','HTMLElement','HTMLInputElement','FormData','Event','MouseEvent'])globalThis[key]=dom.window[key];
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const React=require('react');
const {act}=React;
const {createRoot}=require('react-dom/client');
// dot: use native paths for both the Windows loader and cache reset, including spaces.
const sourceRoot=fileURLToPath(new URL('../src/',import.meta.url));
const mainPath=require.resolve('../src/main.tsx');
for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,path)=>{
 let source=readFileSync(path,'utf8');
 if(path===mainPath)source=source.slice(0,source.lastIndexOf('createRoot(document'))+'\nexport {App};';
 module._compile(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,path);
};
require.extensions['.css']=()=>{};
function clearSourceModules(){
 for(const path of Object.keys(require.cache))if(path.startsWith(sourceRoot))delete require.cache[path];
}
const nameInput=()=>document.querySelector('input[aria-label="页面名称"]');
const button=name=>[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===name);
async function waitFor(check){for(let i=0;i<100;i++){if(check())return;await act(async()=>{await new Promise(r=>setTimeout(r,10));});}assert.ok(check(),'UI condition timed out');}
async function click(name){const el=button(name);assert.ok(el,`button ${name}`);assert.equal(el.disabled,false,`${name} enabled`);await act(async()=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));}
async function input(el,value){assert.ok(el);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));});}
const rename=value=>input(nameInput(),value);
async function refresh(){await click('刷新目录和任务');await waitFor(()=>!button('保存草稿')?.disabled);}
async function save(){await click('保存草稿');await waitFor(()=>document.querySelector('.editor-draft-state')?.textContent.includes('已保存'));}
async function login(username='alice'){await input(document.querySelector('input[name="username"]'),username);await input(document.querySelector('input[name="password"]'),'synthetic');await act(async()=>document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));await waitFor(()=>nameInput());}
function source(name='A saved'){return {schemaVersion:'1.2',defaultPageId:'page',pageOrder:['page'],navigation:{mode:'tabs'},dataSources:{},resultScopes:{},pages:{page:{name,targets:['web'],layout:{type:'responsive',direction:'vertical',spacing:12},components:[],resultScopeIds:[]}}};}
async function fixture(options={}){
 clearSourceModules();
 history.replaceState(null,'',options.route || '/editor');
 sessionStorage.clear();globalThis.confirm=()=>true;
 const f={project:'A',instance:'one',username:'alice',authenticated:true,expire:'',revision:1,docs:{A:source(),B:source('B saved')},saves:[],attempts:[],publishes:[],holdSave:undefined,holdSaveBody:undefined,targetIds:['configured'],localNoAuth:false,localSessions:0,requests:[],...options};
 const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const identity=()=>({userId:'user-'+f.username,username:f.username,role:'admin',csrf:'synthetic',localNoAuth:f.localNoAuth,authMode:f.localAuth?'local':'password'});
 globalThis.fetch=async(path,options={})=>{
  f.requests.push({path,method:options.method || 'GET'});
  if(path==='/api/health')return reply({localNoAuth:f.localNoAuth},f.healthStatus || 200);
  if(path==='/api/local/session'){assert.equal(options.method,'POST');assert.equal(options.headers.get('X-Local-Session'),'1');assert.equal(options.body,undefined);f.localSessions++;f.authenticated=true;f.localAuth=true;f.username='local-admin';return reply(identity());}
  if(path==='/api/admin/users' || path==='/api/admin/runtimes')return reply([]);
  if(path==='/api/session'){f.localAuth=false;f.username=JSON.parse(options.body).username;f.authenticated=true;return reply(identity());}
  if(path===f.expire){f.authenticated=false;f.expire='';}
  if(!f.authenticated)return reply({detail:'Login required'},401);
  if(path==='/api/me')return reply(identity());
  if(path==='/api/logout'){f.authenticated=false;return reply({});}
  if(path==='/api/runtimes')return reply(f.targetIds.map(id=>({id,mode:'mock'})));
  if(/^\/api\/editor\/[^/]+\/context$/.test(path))return reply({runtime:{projectId:f.project,runtimeInstanceId:f.instance},catalog:{choices:[]},versions:[],draft:{presentation:f.docs[f.project],draftRevision:String(f.revision)}});
  if(/^\/api\/runtimes\/[^/]+\/runs$/.test(path))return reply([{id:'run',state:'COMPLETED'}]);
  if(path==='/api/editor/configured/draft'){
   const value=JSON.parse(options.body);f.attempts.push(value);
   if(value.expectedDraftRevision!==String(f.revision))return reply({code:'EDITOR_CONFLICT',detail:'draft conflict'},409);
   f.docs[value.projectId]=structuredClone(value.presentation);f.revision++;f.saves.push(value);
   const result=reply({draftRevision:String(f.revision),presentation:value.presentation});
   if(f.holdSaveBody){const text=result.text.bind(result);result.text=async()=>{await f.holdSaveBody;return text();};}
   if(f.holdSave)await f.holdSave;
   return result;
  }
  if(path==='/api/editor/configured/publish'){
   const value=JSON.parse(options.body);f.publishes.push(value);
   assert.equal(value.expectedDraftRevision,String(f.revision));
   return reply({versionId:'published',state:'PUBLISHED'});
  }
  if(path.startsWith('/api/editor/configured/versions?'))return reply([]);
  throw Error('Unhandled HTTP '+path);
 };
 const {App}=require('../src/main.tsx');
 let root=createRoot(document.getElementById('root'));
 await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(App))));
 const ready=options.ready || (()=>f.route==='/'?button('退出'):nameInput());
 await waitFor(ready);
 f.close=async()=>{await act(async()=>root.unmount());};
 f.reload=async(check=ready)=>{await f.close();clearSourceModules();const {App:FreshApp}=require('../src/main.tsx');root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(FreshApp))));await waitFor(check);};
 return f;
}

test('React harness exports App without mounting and clears native source module paths',()=>{
 const first=require(mainPath);
 assert.equal(typeof first.App,'function');
 assert.equal(document.getElementById('root').childElementCount,0);
 const sourcePaths=Object.keys(require.cache).filter(path=>path.startsWith(sourceRoot));
 assert.ok(sourcePaths.includes(mainPath),'source directory matches the native require cache path');
 clearSourceModules();
 for(const path of sourcePaths)assert.equal(require.cache[path],undefined);
 assert.notEqual(require(mainPath).App,first.App);
 clearSourceModules();
});

test('Runtime restart preserves unsaved source and undo but invalidates live task selection',async()=>{
 const f=await fixture();try{await rename('local restart edit');const select=document.querySelector('select[aria-label="实时预览任务"]');await act(async()=>{select.value='run';select.dispatchEvent(new Event('change',{bubbles:true}));});f.instance='two';await refresh();assert.equal(nameInput().value,'local restart edit');assert.equal(button('撤销').disabled,false);assert.equal(select.value,'');assert.equal(button('实时预览').disabled,true);assert.equal(f.saves.length,0);await save();assert.equal(f.saves[0].expectedDraftRevision,'1');}finally{await f.close();}
});
test('save response headers are not saved UI completion before reload and publish',async()=>{
 for(const waitForSaved of [false,true]){
  let release;const bodyReady=new Promise(resolve=>{release=resolve;});
  const f=await fixture({holdSaveBody:bodyReady});
  try{
   await rename('package browser save');await click('保存草稿');
   assert.equal(f.saves.length,1,'server has saved and returned response headers');
   assert.equal(button('保存草稿').disabled,true);
   assert.match(document.querySelector('.editor-draft-state').textContent,/未保存/);
   if(waitForSaved){release();await waitFor(()=>!button('保存草稿').disabled && document.querySelector('.editor-draft-state').textContent.includes('已保存'));}
   await f.reload();release();f.holdSaveBody=undefined;
   await click('发布页面');
   if(waitForSaved){
    await waitFor(()=>f.publishes.length===1);
    assert.equal(f.attempts.at(-1).expectedDraftRevision,'2');
    assert.equal(f.publishes[0].expectedDraftRevision,'3');
    assert.equal(document.querySelector('.editor-draft-conflict'),null);
   }else{
    await waitFor(()=>button('保留本地并覆盖此版本'));
    assert.equal(f.attempts.at(-1).expectedDraftRevision,'1');
    assert.equal(f.publishes.length,0,'conflicted pre-publish save must not publish');
   }
  }finally{release();await f.close();}
 }
});
test('401 and explicit logout preserve a user-scoped draft and never replay save after login',async()=>{
 const f=await fixture();try{await rename('alice private edit');f.expire='/api/editor/configured/draft';await click('保存草稿');await waitFor(()=>button('登录'));assert.equal(f.saves.length,0);await login('bob');assert.equal(nameInput().value,'A saved');await click('退出');await login();assert.equal(nameInput().value,'alice private edit');assert.equal(f.saves.length,0);await click('退出');await login();assert.equal(nameInput().value,'alice private edit');await save();assert.equal(f.saves.length,1);assert.equal(Object.keys(sessionStorage).filter(k=>k.startsWith('emo-editor-draft:')).length,0);}finally{await f.close();}
});
test('project changes retain the old local source separately and restore only the matching project',async()=>{
 const f=await fixture();try{await rename('A local');f.project='B';await refresh();assert.equal(nameInput().value,'B saved');assert.equal(button('撤销').disabled,true);f.project='A';await refresh();assert.equal(nameInput().value,'A local');await save();assert.equal(f.saves[0].projectId,'A');assert.equal(f.docs.B.pages.page.name,'B saved');}finally{await f.close();}
});
test('CAS offers comparison and explicit overwrite, rejects a newer remote write, and allows undo after server adoption',async()=>{
 const f=await fixture();try{
  await rename('local');f.docs.A=source('other tab');f.revision=2;await click('保存草稿');await waitFor(()=>button('保留本地并覆盖此版本'));assert.equal(nameInput().value,'local');assert.equal(f.saves.length,0);
  // dot: textarea content must not become part of the exact comparison labels.
  const comparisonFields=[...document.querySelectorAll('.editor-draft-conflict textarea')];
  assert.deepEqual(comparisonFields.map(field=>[...field.labels].map(label=>label.textContent.trim())),[['本地草稿源'],['服务器草稿源']]);
  assert.match(comparisonFields[0].value,/local/);assert.match(comparisonFields[1].value,/other tab/);
  f.docs.A=source('newer remote');f.revision=3;await click('保留本地并覆盖此版本');await waitFor(()=>document.querySelectorAll('textarea')[1]?.value.includes('newer remote'));assert.equal(f.saves.length,0);assert.equal(f.attempts.at(-1).expectedDraftRevision,'2');
  await click('保留本地并覆盖此版本');await waitFor(()=>!button('保留本地并覆盖此版本'));assert.equal(f.saves.at(-1).expectedDraftRevision,'3');
  await rename('before adopt');f.docs.A=source('server choice');f.revision=5;await refresh();await click('采用服务器草稿');assert.equal(nameInput().value,'server choice');await click('撤销');assert.equal(nameInput().value,'before adopt');await save();assert.equal(f.saves.at(-1).expectedDraftRevision,'5');
 }finally{await f.close();}
});
test('canceling logout leaves editing and server session untouched',async()=>{
 const f=await fixture();try{await rename('keep editing');globalThis.confirm=()=>false;await click('退出');assert.equal(nameInput().value,'keep editing');assert.equal(f.authenticated,true);assert.equal(f.saves.length,0);}finally{await f.close();}
});
test('text entered while save is in flight stays dirty and is included in the next explicit save',async()=>{
 const f=await fixture();try{await rename('first');let release;f.holdSave=new Promise(resolve=>{release=resolve;});await click('保存草稿');await rename('second');await act(async()=>release());await waitFor(()=>!button('保存草稿').disabled);assert.equal(nameInput().value,'second');assert.equal(document.querySelector('.editor-draft-state').textContent,'● 未保存');f.holdSave=undefined;await save();assert.equal(f.saves[1].presentation.pages.page.name,'second');assert.equal(f.saves[1].expectedDraftRevision,'2');}finally{await f.close();}
});

test('persisted tab draft survives full React/module teardown without auto-saving',async()=>{
 const f=await fixture();try{await rename('reload draft');await f.reload();assert.equal(nameInput().value,'reload draft');assert.equal(f.saves.length,0);await save();assert.equal(f.saves.at(-1).expectedDraftRevision,'1');}finally{await f.close();}
});
test('configured targets with the same projectId keep their recovery drafts separate',async()=>{
 const f=await fixture({targetIds:['configured','other']});try{await rename('target one draft');let select=document.querySelector('select[aria-label="编辑 Runtime 目标"]');await act(async()=>{select.value='other';select.dispatchEvent(new Event('change',{bubbles:true}));});await waitFor(()=>nameInput()?.value==='A saved');assert.equal(button('撤销').disabled,true);select=document.querySelector('select[aria-label="编辑 Runtime 目标"]');await act(async()=>{select.value='configured';select.dispatchEvent(new Event('change',{bubbles:true}));});await waitFor(()=>nameInput()?.value==='target one draft');assert.equal(f.saves.length,0);}finally{await f.close();}
});
test('blocked browser storage warns and still recovers after in-page reauthentication',async()=>{
 const f=await fixture(),storage=globalThis.sessionStorage;try{globalThis.sessionStorage={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};await rename('in-page recovery');assert.match(document.body.textContent,/浏览器暂不能保存恢复副本/);await click('退出');await login();assert.equal(nameInput().value,'in-page recovery');assert.equal(f.saves.length,0);}finally{globalThis.sessionStorage=storage;await f.close();}
});


test('local first visit enters once under StrictMode without creating an account and warns on both routes',async()=>{
 for(const route of ['/editor','/']){
  const f=await fixture({route,localNoAuth:true,authenticated:false,targetIds:route==='/'?[]:['configured']});
  try{
   assert.equal(f.localSessions,1);
   assert.equal(document.querySelector('.login'),null);
   assert.match(document.querySelector('[role="note"]').textContent,/本机免密管理员，可控制全部已登记 Runtime/);
   assert.match(document.querySelector('[role="note"]').textContent,/退出不关闭免密入口.*--local-no-auth/);
   await f.reload();assert.equal(f.localSessions,1,'existing session survives reload without another issuance');
  }finally{await f.close();}
 }
});

test('local expiry and logout require explicit reentry, preserve drafts, and never replay writes even after reload',async()=>{
 const f=await fixture({localNoAuth:true,authenticated:false});
 try{
  await rename('local unsaved draft');
  f.expire='/api/editor/configured/draft';await click('保存草稿');
  await waitFor(()=>button('进入本机模式'));
  assert.equal(f.localSessions,1);assert.equal(f.saves.length,0);
  await f.reload(()=>button('进入本机模式'));
  assert.equal(f.localSessions,1,'reload must not silently recreate an expired session');
  await click('进入本机模式');await waitFor(()=>nameInput());
  assert.equal(f.localSessions,2);assert.equal(nameInput().value,'local unsaved draft');assert.equal(f.saves.length,0);
  await click('退出');await waitFor(()=>button('进入本机模式'));
  await f.reload(()=>button('进入本机模式'));
  assert.equal(f.localSessions,2,'logout remains effective after reload');
  await click('进入本机模式');await waitFor(()=>nameInput());
  assert.equal(f.localSessions,3);assert.equal(nameInput().value,'local unsaved draft');assert.equal(f.saves.length,0);
  await save();assert.equal(f.saves.length,1);assert.equal(f.publishes.length,0);
 }finally{await f.close();}
});

test('ordinary accounts remain signed in and see the open local-mode warning',async()=>{
 const f=await fixture({localNoAuth:true});
 try{assert.equal(f.localSessions,0);assert.match(document.querySelector('[role="note"]').textContent,/服务仍开放本机免密入口/);assert.ok(document.body.textContent.includes('alice'));}
 finally{await f.close();}
});

test('health 401 cannot invalidate a valid ordinary account or block legacy bootstrap',async()=>{
 const f=await fixture({healthStatus:401});
 try{assert.ok(nameInput());assert.equal(f.localSessions,0);assert.equal(document.querySelector('[role="note"]'),null);}
 finally{await f.close();}
});


test('local runtime reentry restores the original pending command without querying or resubmitting it',async()=>{
 const f=await fixture({route:'/',targetIds:[],localNoAuth:true,authenticated:false});
 try{
  const original={id:'original-request',kind:'start',target:'configured/workflow',runtimeId:'configured',runtimeInstanceId:'one',projectId:'A',state:'unknown'};
  const key='emo-pending:user-local-admin';sessionStorage.setItem(key,JSON.stringify(original));
  await f.reload();assert.ok(button('查询操作结果'));
  f.expire='/api/runtimes';await click('刷新页面包和状态');await waitFor(()=>button('进入本机模式'));
  assert.equal(f.localSessions,1);assert.deepEqual(JSON.parse(sessionStorage.getItem(key)),original);
  const before=f.requests.length;
  await click('进入本机模式');await waitFor(()=>button('查询操作结果'));
  assert.equal(f.localSessions,2);assert.deepEqual(JSON.parse(sessionStorage.getItem(key)),original);
  assert.deepEqual(f.requests.slice(before).filter(x=>x.method!=='GET').map(x=>x.path),['/api/local/session']);
  assert.equal(f.requests.some(x=>x.path.includes('original-request')),false,'queries also require an explicit click');
 }finally{await f.close();}
});


test('blocked storage never prevents explicit local entry or restores a session automatically',async()=>{
 const storage=globalThis.sessionStorage;
 for(const blockedRead of [true,false]){
  globalThis.sessionStorage={clear(){},getItem(){if(blockedRead)throw Error('blocked');return null;},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};
  let f;
  try{
   f=await fixture({route:'/',targetIds:[],localNoAuth:true,authenticated:false,ready:()=>button('进入本机模式')});
   assert.equal(f.localSessions,0,'unavailable recovery storage requires explicit first entry');
   await click('进入本机模式');await waitFor(()=>button('退出'));
   assert.equal(f.localSessions,1);assert.ok(document.body.textContent.includes('本机管理员'));
   await f.reload(()=>button('退出'));
   assert.equal(f.localSessions,1,'valid server session remains usable despite blocked marker write');
   await click('退出');await waitFor(()=>button('进入本机模式'));
   await f.reload(()=>button('进入本机模式'));assert.equal(f.localSessions,1);
   await click('进入本机模式');await waitFor(()=>button('退出'));assert.equal(f.localSessions,2);
  }finally{if(f)await f.close();globalThis.sessionStorage=storage;}
 }
});
