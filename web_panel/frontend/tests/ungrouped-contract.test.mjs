import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const require=createRequire(import.meta.url);
for(const extension of ['.ts','.tsx'])require.extensions[extension]=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,path);
const {normalizeMessage,isInitialJobConfirmation}=require('../src/data/contract.ts');
const {commandResolution}=require('../src/state/commands.ts');
const {PanelRenderer}=require('../src/renderer/PanelRenderer.tsx');
const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');
const fixture=name=>JSON.parse(readFileSync(new URL(`../../contracts/panel-contract-v1/fixtures/${name}.json`,import.meta.url),'utf8'));
const normalize=wire=>normalizeMessage(wire,'view',wire.previousJobId || wire.jobId,wire.runtimeInstanceId,[wire.jobId]);
const renderStatus=status=>renderToStaticMarkup(React.createElement(PanelRenderer,{panel:{id:'panel',pages:[{id:'page',title:'Page',components:[{id:'status',type:'runtime_status'}]}]},pageId:'page',onPage(){},onAction(){},state:'ready',runtimeState:status}));

test('SDK 1.1.1 ordinary job_state, job_changed and control_result preserve explicit empty group',()=>{
 for(const name of ['ungrouped-running','ungrouped-job-changed','ungrouped-start-accepted','ungrouped-stop-accepted','ungrouped-aborted']){
  const wire=fixture(name),original=structuredClone(wire),message=normalize(wire);
  assert.equal(message.type,wire.event);assert.equal(message.runGroupId,'');assert.ok(Object.hasOwn(message,'runGroupId'));
  assert.equal(message.jobId,wire.jobId);assert.deepEqual(wire,original);assert.equal(message.contractStatus,undefined);
 }
});
test('retained grouped official fixtures preserve their real group instead of converting to empty',()=>{
 for(const name of ['terminal','job-changed','start-accepted','stop-accepted']){
  const wire=fixture(name),message=normalize(wire);assert.ok(wire.runGroupId);assert.equal(message.runGroupId,wire.runGroupId);assert.equal(message.type,wire.event);
 }
});
test('official ordinary accepted Start/Stop acknowledges intent, ABORTED alone supplies terminal display/resources',()=>{
 const running=normalize(fixture('ungrouped-running')),start=normalize(fixture('ungrouped-start-accepted')),stop=normalize(fixture('ungrouped-stop-accepted'));
 assert.equal(commandResolution('start',start).unknown,false);assert.equal(commandResolution('start',start).runId,running.jobId);
 const resolution=commandResolution('stop',stop);assert.equal(resolution.confirmed,true);assert.match(resolution.message,/停止已受理/);assert.match(resolution.message,/资源释放以 Runtime 状态为准/);
 assert.equal(stop.status,undefined);assert.equal(stop.ownsResources,undefined);assert.equal(stop.displayResourcesReleased,undefined);
 assert.equal(running.active,true);assert.equal(running.ownsResources,true);assert.equal(running.displayResourcesReleased,false);assert.match(renderStatus(running.status),/RUNNING/);
 const aborted=normalize(fixture('ungrouped-aborted'));assert.equal(aborted.status,'ABORTED');assert.equal(aborted.active,false);assert.equal(aborted.ownsResources,false);assert.equal(aborted.displayResourcesReleased,true);
 const html=renderStatus(aborted.status);assert.match(html,/ABORTED/);assert.doesNotMatch(html,/COMPLETED|RUNNING/);
});

test('initial formal job_changed confirms exact current selection; actual changes still invalidate',()=>{
 const wire={...fixture('ungrouped-job-changed'),previousJobId:null};const message=normalize(wire);
 const matches=value=>isInitialJobConfirmation(value,wire.jobId,wire.runtimeInstanceId,wire.projectId);
 assert.equal(matches(message),true);
 for(const change of [{jobId:'other'},{runtimeInstanceId:'replacement'},{projectId:'other-project'},{previousJobId:'previous'},{previousJobId:undefined},{type:'job_state'}])assert.equal(matches({...message,...change}),false);
 assert.equal(isInitialJobConfirmation(message,'',wire.runtimeInstanceId,wire.projectId),false);
 assert.equal(isInitialJobConfirmation(message,wire.jobId,undefined,wire.projectId),false);
});
