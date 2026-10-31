import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const require=createRequire(import.meta.url);
for(const extension of ['.ts','.tsx'])require.extensions[extension]=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,path);
const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');
const {PanelRenderer}=require('../src/renderer/PanelRenderer.tsx');
const render=(components,extra={})=>renderToStaticMarkup(React.createElement(PanelRenderer,{panel:{id:'p',pages:[{id:'page',title:'Page',components}]},pageId:'page',onPage(){},onAction(){},state:'ready',...extra}));
test('binding uses the validated scopeId and nested style whitelist',()=>{
 const html=render([{id:'n',type:'number',binding:{sourceId:'score',scopeId:'second'},props:{style:{color:'#123456',fontSize:24,fontWeight:'bold'}}}],{result:{resultKey:'a',sources:{score:{state:'available',value:11}}},results:{second:{resultKey:'b',sources:{score:{state:'available',value:42}}}}});
 assert.match(html,/>42 /);assert.doesNotMatch(html,/>11 /);assert.match(html,/color:#123456/);assert.match(html,/font-size:24px/);assert.match(html,/font-weight:bold/);
});
test('unavailable bound values never render fallback or stale OK business values',()=>{
 for(const type of ['text','number','indicator','table']){
 const html=render([{id:'c',type,binding:'bad',props:{value:'OK',text:'OK'}}],{result:{resultKey:'r',sources:{bad:{state:'error',value:'OK',reason:'sensor offline'}}}});
 assert.match(html,/sensor offline/);assert.doesNotMatch(html,/>OK</);
 const absent=render([{id:'c',type,binding:'absent',props:{value:'OK'}}]);assert.match(absent,/不可用/);assert.doesNotMatch(absent,/>OK</);
 }
});
test('draft nested grid, field selection and all permitted safe styles render',()=>{
 const html=render([{id:'n',type:'text',binding:{sourceId:'data',field:'verdict'},props:{grid:{column:2,columnSpan:2},style:{color:'red',fontWeight:'600',fontSize:96}}}],{result:{resultKey:'r',sources:{data:{state:'available',value:{verdict:'PASS'}}}}});
 assert.match(html,/>PASS</);assert.match(html,/grid-column:2 \/ span 2/);assert.match(html,/color:red/);assert.match(html,/font-weight:600/);assert.match(html,/font-size:96px/);
});
test('clock timezone, navigation plain icon and fixed history image are supported',()=>{
 globalThis.location={origin:'http://panel.example'};
 const html=render([{id:'clock',type:'clock',props:{timezone:'UTC',format:'time'}},{id:'nav',type:'navigation_button',props:{icon:'▶',text:'Open',targetPageId:'page'}},{id:'fixed',type:'image',props:{sourceMode:'history_fixed'}}],{viewId:'v'});
 assert.match(html,/UTC/);assert.match(html,/▶/);assert.match(html,/\/api\/views\/v\/images\/fixed/);
});
test('aggregate null stays empty, never substitutes row count',()=>{
 const {historyAggregateText}=require('../src/renderer/PanelRenderer.tsx');assert.equal(historyAggregateText({value:null,total:0}),'—');assert.equal(historyAggregateText({total:100}),'统计能力未就绪');assert.equal(historyAggregateText({value:0,total:9}),'0');
});
test('official number valueJson preserves unsafe integer without rounding',()=>{
 const html=render([{id:'n',type:'number',binding:'count'}],{result:{resultKey:'r',sources:{count:{state:'AVAILABLE',valueJson:'9007199254740993'}}}});
 assert.match(html,/9007199254740993/);assert.doesNotMatch(html,/9007199254740992/);
});
test('official indicator JSON keys do not confuse boolean with string',()=>{
 const components=[{id:'n',type:'indicator',binding:'x',props:{indicatorStates:{true:{text:'BOOLEAN',color:'green'},'"true"':{text:'STRING',color:'red'}}}}];
 assert.match(render(components,{result:{resultKey:'r',sources:{x:{state:'AVAILABLE',valueJson:'true'}}}}),/BOOLEAN/);
 assert.match(render(components,{result:{resultKey:'r',sources:{x:{state:'AVAILABLE',valueJson:'"true"'}}}}),/STRING/);
});
test('official responsive layout preserves child order and fieldPath columns',()=>{
 const components=[{id:'wrap',type:'container',grid:{type:'responsive',direction:'wrap',spacing:7},children:[{id:'b',type:'text',props:{text:'FIRST'},layout:{row:3}},{id:'a',type:'table',binding:'rows',layout:{row:0},props:{columns:[{title:'Nested',fieldPath:['part','count']}],pageSize:1}}]}];
 const html=render(components,{result:{resultKey:'r',sources:{rows:{state:'available',value:[{part:{count:3}},{part:{count:5}}]}}}});
 assert.ok(html.indexOf('FIRST')<html.indexOf('Nested'));assert.match(html,/flex-wrap:wrap/);assert.match(html,/gap:7px/);assert.match(html,/>3</);assert.doesNotMatch(html,/>5</);
});
test('official history refresh reselects only stable identities with new refs',()=>{
 const {normalizeHistory,restoreHistorySelection}=require('../src/renderer/PanelRenderer.tsx');
 const page={columns:[{name:'id'}],rows:[{rowId:'one',values:[1],imageRefs:{picture:{status:'AVAILABLE',imageRef:'new'}}}],stableRowIdentity:true,historyDefinitionRevision:'rev'};
 const normalized=normalizeHistory(page);const restored=restoreHistorySelection(normalized,{rowId:'one',imageRefs:{picture:{imageRef:'old'}}},'rev');
 assert.equal(restored.__displayValues.id,1);assert.equal(restored.imageRefs.picture.imageRef,'new');
 assert.equal(restoreHistorySelection({...normalized,stableRowIdentity:false},{rowId:'one'},'rev'),undefined);
 assert.equal(restoreHistorySelection(normalized,{rowId:'one'},'different'),undefined);
 assert.equal(restoreHistorySelection(normalized,{rowId:'missing'},'rev'),undefined);
});
test('lossless JSON handles nested data, escaped strings, and finite floats',()=>{
 const {parseLosslessJSON}=require('../src/data/lossless.ts');
 assert.deepEqual(parseLosslessJSON('{"x":[9007199254740993,-9007199254740994,1.25],"s":"9007199254740993\\\""}'),{x:['9007199254740993','-9007199254740994',1.25],s:'9007199254740993"'});
 assert.throws(()=>parseLosslessJSON('{"x":NaN}'));
});
test('official result maps exact identity and per-source view URLs without rounding',()=>{
 const {normalizeMessage}=require('../src/data/contract.ts');
 const raw={event:'result',runtimeInstanceId:'runtime',projectId:'project',cursor:'999999999999999999',result:{identity:{runtimeInstanceId:'runtime',jobId:'job',resultScopeId:'root',resultKey:'result',resultOrdinal:'9007199254740993'},expectedSourceIds:['image','number'],sources:[{sourceId:'image',state:'AVAILABLE',image:{resourceId:'private',ownerResultKey:'result'}},{sourceId:'number',state:'AVAILABLE',valueJson:'9007199254740993'}]}};
 const message=normalizeMessage(raw,'view','job','runtime');
 assert.equal(message.result.resultOrdinal,'9007199254740993');assert.equal(message.result.sources.number.valueJson,'9007199254740993');
 assert.equal(message.result.sources.image.image.url,'/api/views/view/assets/image?scope=root&resultKey=result');
 assert.throws(()=>normalizeMessage(raw,'view','other-job','runtime'));
 assert.throws(()=>normalizeMessage(raw,'view','job','other-runtime'));
});
test('official grid placement uses zero-based coordinates and static navigation icon',()=>{
 globalThis.location={origin:'http://panel.example'};
 const html=render([{id:'n',type:'navigation_button',layout:{row:0,column:0,columnSpan:2},props:{iconResourceId:'logo',text:'Go'},actions:{clicked:{type:'navigate',pageId:'page'}}}]);
 assert.match(html,/grid-column:1 \/ span 2/);assert.match(html,/grid-row:1 \/ span 1/);assert.match(html,/\/api\/assets\/p\/logo/);
});
test('SDK coverage problems hide even available payloads without mutating wire result',()=>{
 const {applySourceProblems}=require('../src/data/contract.ts');
 const raw={resultKey:'r',sources:{stale:{state:'available',valueJson:'123'}}};
 const view=applySourceProblems(raw,{stale:'SOURCE_DEFINITION_CHANGED',missing:'SOURCE_NOT_CAPTURED'});
 assert.deepEqual(view.sources.stale,{state:'unavailable',reason:'SOURCE_DEFINITION_CHANGED'});
 assert.equal(view.sources.missing.reason,'SOURCE_NOT_CAPTURED');assert.equal(raw.sources.stale.valueJson,'123');
});
test('official fixed history images use view-bound no-ref RPC route',()=>{
 globalThis.location={origin:'http://panel.example'};
 const html=render([{id:'fixed',type:'image',history:{kind:'fixed_image',componentId:'fixed'},props:{sourceMode:'history_fixed'}}],{viewId:'view'});
 assert.match(html,/\/api\/views\/view\/history-images\/fixed/);
});
test('per-Job event merge sorts exact decimal sequence, deduplicates and bounds100',()=>{
 const {mergeJobEvents}=require('../src/components/RuntimeStatus.tsx');
 const events=Array.from({length:105},(_,i)=>({sequence:String(BigInt('9007199254740993')+BigInt(i)),kind:'info',message:String(i)}));
 const merged=mergeJobEvents([events[0]],{events:events.reverse(),nextSequence:events[0].sequence,resetRequired:false,source:'runtime'});
 assert.equal(merged.length,100);assert.equal(merged[0].message,'5');assert.equal(merged.at(-1).message,'104');
 assert.equal(mergeJobEvents(merged,{events:[],nextSequence:'0',resetRequired:true,source:'runtime'}).length,0);
});
test('official input editor keeps raw numeric text and avoids lossy form reserialization',()=>{
 const {WorkflowInputs}=require('../src/components/WorkflowInputs.tsx');
 const html=renderToStaticMarkup(React.createElement(WorkflowInputs,{workflow:{inputsJson:'{"n":9007199254740993}',inputDeclarations:{n:'integer'}},value:'{"n":9007199254740993}',onChange(){}}));
 assert.match(html,/9007199254740993/);assert.doesNotMatch(html,/type="number"/);assert.match(html,/integer/);
});
test('displayed-result navigation waits for confirmed freeze and stays put on failure',async()=>{
 const {navigateDisplayedResult}=require('../src/renderer/PanelRenderer.tsx');let finish;const pages=[];
 const pending=navigateDisplayedResult('detail','scope',true,()=>new Promise(resolve=>finish=resolve),page=>pages.push(page),()=>true);
 assert.deepEqual(pages,[]);finish(false);assert.equal(await pending,false);assert.deepEqual(pages,[]);
 assert.equal(await navigateDisplayedResult('detail','scope',true,async()=>true,page=>pages.push(page),()=>true),true);
 assert.deepEqual(pages,['detail']);
});
test('interrupted or superseded displayed-result navigation never changes page',async()=>{
 const {navigateDisplayedResult}=require('../src/renderer/PanelRenderer.tsx');let finish;let generation=1;const pages=[];
 const old=navigateDisplayedResult('old','scope',true,()=>new Promise(resolve=>finish=resolve),page=>pages.push(page),()=>generation===1);
 generation=2;await navigateDisplayedResult('new',undefined,false,async()=>true,page=>pages.push(page),()=>generation===2);
 finish(true);assert.equal(await old,false);assert.deepEqual(pages,['new']);
 assert.equal(await navigateDisplayedResult('bad',undefined,true,async()=>true,page=>pages.push(page),()=>true),false);
});
test('only SDK validated group runIds can supply group result and state',()=>{
 const {normalizeMessage}=require('../src/data/contract.ts');
 const state={event:'job_state',runtimeInstanceId:'runtime',projectId:'project',jobId:'consumer',status:'RUNNING'};
 assert.equal(normalizeMessage(state,'v','root','runtime',['root','consumer']).jobId,'consumer');
 assert.throws(()=>normalizeMessage(state,'v','root','runtime',['root']));
 const result={event:'result',runtimeInstanceId:'runtime',projectId:'project',cursor:'1',result:{identity:{runtimeInstanceId:'runtime',jobId:'consumer',resultScopeId:'dispatch',resultKey:'r',resultOrdinal:1},expectedSourceIds:[],sources:[]}};
 assert.equal(normalizeMessage(result,'v','root','runtime',['root','consumer']).result.resultScopeId,'dispatch');
 assert.throws(()=>normalizeMessage(result,'v','root','runtime',['root']));
});
test('official history renders declared filter columns and authoritative sort defaults',()=>{
 const html=render([{id:'records',type:'sqlite_table',history:{componentId:'records',kind:'table',orderBy:'id',descending:true},props:{columns:[{key:'id',title:'ID'},{key:'verdict',title:'Verdict'}]}}],{viewId:'view'});
 assert.match(html,/aria-label="records 筛选列"/);assert.match(html,/所有声明列/);assert.match(html,/<option value="id" selected="">ID<\/option>/);assert.match(html,/<option value="desc" selected="">降序<\/option>/);
});
test('gateway-transformed mixed results retain available image routing and official epoch',()=>{
 const {normalizeMessage}=require('../src/data/contract.ts');
 const result={gatewayTransformed:true,runtimeInstanceId:'runtime',projectId:'project',jobId:'job',resultScopeId:'scope',resultKey:'r',sources:{missing:{state:'unavailable',reason:'SOURCE_NOT_CAPTURED'},image:{state:'available',image:{resourceId:'resource',ownerResultKey:'r'}}}};
 const message=normalizeMessage({type:'result',sessionEpoch:'random-server-epoch',result},'view','job','runtime');
 assert.equal(message.sessionEpoch,'runtime/project/job');assert.equal(message.result.sources.missing.reason,'SOURCE_NOT_CAPTURED');assert.equal(message.result.sources.missing.image,undefined);
 assert.equal(message.result.sources.image.image.url,'/api/views/view/assets/image?scope=scope&resultKey=r');assert.equal(result.sources.image.image.url,undefined);
 assert.throws(()=>normalizeMessage({type:'result',result},'view','other','runtime'));
});
test('published emptyText is used by scalar, image and table empty states',()=>{
 const html=render([{id:'n',type:'number',binding:'n',props:{emptyText:'NO NUMBER'}},{id:'i',type:'image',props:{emptyText:'NO IMAGE'}},{id:'t',type:'table',binding:'rows',props:{emptyText:'NO ROWS',columns:[{title:'Value',fieldPath:[]}]}}],{result:{resultKey:'r',sources:{n:{state:'available',valueJson:'null'},rows:{state:'available',valueJson:'[]'}}}});
 for(const text of ['NO NUMBER','NO IMAGE','NO ROWS'])assert.match(html,new RegExp(text));
});
test('published navigation mode distinguishes tabs from ordinary buttons',()=>{
 for(const mode of ['tabs','buttons']){
 const html=render([{id:'nav',type:'page_navigation'}],{panel:{id:'p',navigation:{mode},pages:[{id:'page',title:'Page',components:[{id:'nav',type:'page_navigation'}]}]}});
 if(mode==='tabs'){assert.match(html,/role="tablist"/);assert.match(html,/role="tab"/);assert.match(html,/aria-selected="true"/)}else assert.doesNotMatch(html,/role="tab/);
 }
});
test('local expiry and package notices never switch the official result epoch',()=>{
 const {normalizeMessage}=require('../src/data/contract.ts');
 for(const type of ['freeze_expired','package_updated','reset_required']){
 const message=normalizeMessage({type,sessionEpoch:'local-random'},'v','job','runtime');assert.equal(message.sessionEpoch,undefined);assert.equal(message.type,type);
 }
});
test('scope progress never retires completed result on newer start; expiry is job/scoped and exact',()=>{
 const {mergeScopeStatus,isScopeResultExpired,scopeActivity}=require('../src/state/scopes.ts');
 const result={jobId:'job',resultScopeId:'scope',resultKey:'r',resultOrdinal:'9007199254740993',sources:{image:{state:'available',image:{url:'/exact'}}}};
 const started=mergeScopeStatus({},'job',{scope:'9007199254740994'},{});
 assert.equal(isScopeResultExpired(result,started),false);assert.match(scopeActivity(Object.values(started)[0],result),/处理中 #9007199254740994/);
 const expired=mergeScopeStatus(started,'job',{}, {scope:'9007199254740993'});
 assert.equal(isScopeResultExpired(result,expired),true);
 assert.equal(isScopeResultExpired({...result,resultOrdinal:'9007199254740994'},expired),false);
 assert.equal(isScopeResultExpired({...result,resultScopeId:'other'},expired),false);
 assert.equal(isScopeResultExpired({...result,jobId:'other'},expired),false);
 assert.equal(isScopeResultExpired(result,expired,'r'),false);assert.match(scopeActivity(Object.values(expired)[0],result,true),/已冻结查看/);
 assert.equal(result.sources.image.image.url,'/exact');
});
test('gateway scope-status metadata is identity checked and retains decimal strings',()=>{
 const {normalizeMessage}=require('../src/data/contract.ts');
 const message={type:'scope_status',runtimeInstanceId:'runtime',projectId:'project',jobId:'consumer',latestStartedOrdinals:{s:'9007199254740994'},expiredScopeOrdinals:{s:'9007199254740993'}};
 assert.equal(normalizeMessage(message,'v','root','runtime',['root','consumer']).latestStartedOrdinals.s,'9007199254740994');
 assert.throws(()=>normalizeMessage(message,'v','root','runtime',['root']));
 assert.throws(()=>normalizeMessage({...message,expiredScopeOrdinals:{s:1}},'v','root','runtime',['consumer']));
});
test('Resume removes pinned result whose live ordinal expired even without a new payload',()=>{
 const {mergeScopeStatus,isScopeResultExpired}=require('../src/state/scopes.ts');
 const pinned={jobId:'job',resultScopeId:'scope',resultKey:'pinned',resultOrdinal:'10',sources:{}};
 const status=mergeScopeStatus({},'job',{scope:'11'},{scope:'10'});
 assert.equal(isScopeResultExpired(pinned,status,'pinned'),false);
 // Successful Resume removes pin immunity; no new snapshot is needed to retire it.
 assert.equal(isScopeResultExpired(pinned,status,undefined),true);
 assert.equal(isScopeResultExpired({...pinned,resultKey:'new',resultOrdinal:'11'},status,undefined),false);
});
