import test from 'node:test';import assert from 'node:assert/strict';import ts from 'typescript';import {readFileSync} from 'node:fs';
const js=ts.transpileModule(readFileSync(new URL('../src/state/latest.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {LatestScopeQueue}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const tick=()=>new Promise(r=>setTimeout(r,0));
test('repeated identical slow result loads once; newer items coalesce without starving current',async()=>{
 const seen=[],releases=[];const queue=new LatestScopeQueue(async v=>{seen.push(v);await new Promise(r=>releases.push(r));return true});
 queue.push('s',1,'one');queue.push('s',1,'one');queue.push('s',2,'two');queue.push('s',3,'three');assert.deepEqual(seen,[1]);releases.shift()();await tick();assert.deepEqual(seen,[1,3]);releases.shift()();await tick();queue.push('s',3,'three');assert.deepEqual(seen,[1,3]);
 queue.push('s',4,'same-key-but-expired-source');assert.deepEqual(seen,[1,3,4]);releases.shift()();await tick();
});
test('page/reset invalidation discards pending and permits reprepare same identity',async()=>{
 const seen=[],releases=[];const queue=new LatestScopeQueue(async v=>{seen.push(v);await new Promise(r=>releases.push(r));return true});
 queue.push('s',1,'one');queue.push('s',2,'two');queue.invalidate();queue.push('s',1,'one');releases.shift()();await tick();assert.deepEqual(seen,[1,1]);releases.shift()();await tick();
});
