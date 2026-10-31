import test from 'node:test';import assert from 'node:assert/strict';import ts from 'typescript';import {readFileSync} from 'node:fs';
const js=ts.transpileModule(readFileSync(new URL('../src/state/latest.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {LatestScopeQueue,resumeExpiredScopes}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
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

test('Resume retains last displayed result while slow candidate loads, so immediate Freeze pins that result',async()=>{
 let shown=97,frozen=false,epoch=0,release,restages=0;
 const queue=new LatestScopeQueue(async value=>{const candidateEpoch=epoch;await new Promise(resolve=>release=resolve);if(candidateEpoch!==epoch || frozen)return false;shown=value;return true});
 queue.push('scope',888,'late-image');
 resumeExpiredScopes(new Set(),()=>{restages++;shown=undefined;queue.invalidate()});
 assert.equal(shown,97);assert.equal(restages,0);
 frozen=true;epoch++;queue.invalidate();release();await tick();assert.equal(shown,97);
});
test('Resume restages expired scopes once after clearing expiry markers',()=>{
 const expired=new Set(['scope']);let calls=0;resumeExpiredScopes(expired,()=>{calls++;assert.equal(expired.size,0)});assert.equal(calls,1);
 resumeExpiredScopes(expired,()=>calls++);assert.equal(calls,1);
});
test('scoped expiry invalidates only its pending items, not other live scopes',async()=>{
 const seen=[],releases={};const queue=new LatestScopeQueue(async value=>{seen.push(value);await new Promise(resolve=>releases[value]=resolve);return true});
 queue.push('a','a1','a1');queue.push('a','a2','a2');queue.push('b','b1','b1');queue.push('b','b2','b2');
 queue.invalidateScope('a');releases.a1();releases.b1();await tick();assert.deepEqual(seen,['a1','b1','b2']);releases.b2();await tick();
});
