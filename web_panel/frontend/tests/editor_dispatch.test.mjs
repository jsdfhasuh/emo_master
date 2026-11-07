import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

// Execute the actual message branch without requiring browser IPC or a mocked React renderer.
const source=ts.createSourceFile('Editor.tsx',readFileSync(new URL('../src/editor/Editor.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let branch;
function visit(node){if(ts.isIfStatement(node) && node.expression.getText(source)==="message.type==='capture_unavailable'")branch=node;ts.forEachChild(node,visit);}
visit(source);assert.ok(branch,'Editor capture-unavailable branch must exist');
const handle=new Function('message','runId','rawResults','queue','candidates','setResults','setLiveState','invalidate',
 ts.transpileModule(branch.getText(source),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText);
const queueJs=ts.transpileModule(readFileSync(new URL('../src/state/latest.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {LatestScopeQueue}=await import('data:text/javascript;base64,'+Buffer.from(queueJs).toString('base64'));
const result=(job,scope=job)=>({jobId:job,resultScopeId:scope,resultKey:job+'-result',sources:{}});
function fixture(rawResults,runId='root'){
 const state={results:{...rawResults},candidates:{},invalidated:[],notice:''};
 const queue={invalidateScope:scope=>state.invalidated.push(scope),invalidate:()=>state.invalidated.push('*')};
 state.handle=(jobId)=>handle({type:'capture_unavailable',jobId,reason:'No capture'},runId,rawResults,queue,state.candidates,update=>state.results=typeof update==='function'?update(state.results):update,notice=>state.notice=notice,()=>{queue.invalidate();for(const key of Object.keys(state.candidates))state.candidates[key]++;});
 return state;
}

test('uncaptured root cannot clear or invalidate its captured consumers',()=>{
 const child=result('child'),sibling=result('sibling'),raw={child,sibling},state=fixture(raw);
 state.candidates.child=3;state.candidates.sibling=4;state.handle('root');
 assert.deepEqual(raw,{child,sibling});assert.deepEqual(state.results,{child,sibling});
 assert.deepEqual(state.candidates,{child:3,sibling:4});assert.deepEqual(state.invalidated,[]);
});
test('consumer capture loss clears only its owner and fences that pending scope',()=>{
 const child=result('child'),sibling=result('sibling'),raw={child,sibling},state=fixture(raw);
 state.candidates.child=3;state.candidates.sibling=4;state.handle('child');
 assert.deepEqual(raw,{sibling});assert.deepEqual(state.results,{sibling});
 assert.deepEqual(state.candidates,{child:4,sibling:4});assert.deepEqual(state.invalidated,['child']);
});
test('ordinary Job still clears all its own scopes, including legacy selected-Job notices',()=>{
 for(const jobId of ['solo',undefined]){
  const raw={first:result('solo','first'),second:result('solo','second')},state=fixture(raw,'solo');
  state.handle(jobId);assert.deepEqual(raw,{});assert.deepEqual(state.results,{});
  assert.deepEqual(state.invalidated,['first','second']);assert.equal(state.notice,'No capture');
 }
});
test('root notice preserves an in-flight consumer; consumer notice prevents its stale completion',async()=>{
 for(const unavailableJob of ['root','child']){
  const raw=result('child'),rawResults={child:raw},candidates={};let ready,shown={};
  const queue=new LatestScopeQueue(async value=>{const attempt=(candidates.child || 0)+1;candidates.child=attempt;await new Promise(resolve=>ready=resolve);if(attempt!==candidates.child)return false;shown.child=value;return true;});
  queue.push('child',raw,raw.resultKey);
  handle({type:'capture_unavailable',jobId:unavailableJob},'root',rawResults,queue,candidates,update=>shown=typeof update==='function'?update(shown):update,()=>{},()=>{queue.invalidate();for(const key of Object.keys(candidates))candidates[key]++;});
  ready();await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(shown,unavailableJob==='root'?{child:raw}:{});
 }
});
