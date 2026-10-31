import test from 'node:test';import assert from 'node:assert/strict';import ts from 'typescript';import {readFileSync} from 'node:fs';
const js=ts.transpileModule(readFileSync(new URL('../src/state/commands.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {commandResolution}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
test('accepted Start with exact Job clears unknown tracking, same path after lookup',()=>{
 for(const reply of [{state:'accepted',runId:'j'},{state:'accepted',runId:'j',startRequestId:'original'}]){const r=commandResolution('start',reply);assert.equal(r.confirmed,true);assert.equal(r.unknown,false);assert.match(r.message,/启动已受理/);}
 assert.equal(commandResolution('start',{state:'accepted'}).unknown,true);
});
test('accepted Stop acknowledges command but not Job completion/resources',()=>{const r=commandResolution('stop',{state:'accepted',runId:'j'});assert.equal(r.confirmed,true);assert.match(r.message,/停止已受理/);assert.match(r.message,/结束及资源释放以 Runtime 状态为准/);assert.doesNotMatch(r.message,/已经停止完成/)});
test('HTTP200 rejection shows reason and clears pending, never accepted wording',()=>{const r=commandResolution('start',{state:'rejected',message:'revision changed'});assert.equal(r.confirmed,true);assert.equal(r.rejected,true);assert.match(r.message,/revision changed/);assert.doesNotMatch(r.message,/已受理/)});
test('unknown Stop resolved via ABORTED ends tracking without claiming command acceptance',()=>{const r=commandResolution('stop',{state:'succeeded',outcome:'UNKNOWN',runId:'j',run:{status:'ABORTED',ownsResources:true,displayResourcesReleased:false}});assert.equal(r.confirmed,true);assert.match(r.message,/ABORTED/);assert.doesNotMatch(r.message,/已受理|已释放/)});
test('genuinely unknown outcomes remain queryable with manual exit available',()=>{assert.equal(commandResolution('start',{state:'unknown',message:'transport lost'}).unknown,true);assert.equal(commandResolution('stop',{state:'unknown'}).confirmed,false)});
