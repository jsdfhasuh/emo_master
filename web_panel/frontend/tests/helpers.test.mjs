import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {readFileSync} from 'node:fs';
const js=ts.transpileModule(readFileSync(new URL('../src/data/api.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const apiUrl='data:text/javascript;base64,'+Buffer.from(js).toString('base64');
const {safeAssetUrl,setCsrf,api}=await import(apiUrl);
globalThis.location={origin:'http://panel.example'};
test('resource URLs limited to current-origin authorized paths',()=>{
 assert.equal(safeAssetUrl('/api/assets/x'),'http://panel.example/api/assets/x');
 for(const value of ['https://evil.example/api/assets/x','javascript:alert(1)','/api/admin/users','//evil.example/x','file:///tmp/x'])assert.equal(safeAssetUrl(value),undefined);
});
test('mutating requests carry CSRF and never put it in URL',async()=>{let captured;globalThis.fetch=async(path,options)=>{captured={path,options};return{ok:true,status:200,json:async()=>({ok:true})}};setCsrf('fixture');await api('/api/runs/start',{method:'POST',body:'{}'});assert.equal(captured.options.headers.get('X-CSRF-Token'),'fixture');assert.equal(captured.path,'/api/runs/start');assert.equal(captured.options.credentials,'same-origin')});
test('server failures are errors rather than fake success',async()=>{globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'Official SDK unavailable'})});await assert.rejects(api('/api/runs'),/Official SDK unavailable/)});
const prepSource=readFileSync(new URL('../src/state/prepare.ts',import.meta.url),'utf8').replace("from '../data/api'",`from '${apiUrl}'`);
const prepJs=ts.transpileModule(prepSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {prepareResult}=await import('data:text/javascript;base64,'+Buffer.from(prepJs).toString('base64'));
test('wrong owner result clears image instead of mixing rounds',async()=>{const result={resultKey:'new',sources:{image:{state:'available',image:{url:'/api/assets/a',ownerResultKey:'old'}},score:{state:'available',value:100}}};const ready=await prepareResult(result,new AbortController().signal);assert.equal(ready.sources.image.state,'error');assert.equal(ready.sources.image.image,undefined);assert.equal(ready.sources.score.value,100);assert.equal(result.sources.image.state,'available')});
test('failed image resolves entire scope with explicit current-round failure',async()=>{globalThis.Image=class{set src(v){if(v)setTimeout(()=>this.onerror?.(),0)}};const ready=await prepareResult({resultKey:'new',sources:{image:{state:'available',image:{url:'/api/assets/a',ownerResultKey:'new'}}}},new AbortController().signal);assert.equal(ready.sources.image.state,'error')});
