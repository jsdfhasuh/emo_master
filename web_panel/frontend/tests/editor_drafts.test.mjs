import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const require=createRequire(import.meta.url);
require.extensions['.ts']=(module,path)=>module._compile(ts.transpileModule(readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,path);
const {draftKey,rememberDraft,recoverDraft}=require('../src/editor/drafts.ts');
const entries=new Map();
globalThis.sessionStorage={getItem:key=>entries.get(key) || null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};
const draft=()=>({presentation:{schemaVersion:'1.2',pageOrder:['page'],pages:{page:{name:'unsaved'}}},saved:'saved source',draftRevision:'original-revision',pageId:'page',selected:'component'});

test('recovery keys isolate user, configured target and project without Runtime instance',()=>{
 const key=draftKey('alice','target','project'),value=draft();rememberDraft(key,value);
 assert.deepEqual(recoverDraft(key),value);
 for(const parts of [['bob','target','project'],['alice','other','project'],['alice','target','other']])assert.equal(recoverDraft(draftKey(...parts)),undefined);
 value.presentation.pages.page.name='later mutation';assert.equal(recoverDraft(key).presentation.pages.page.name,'unsaved');
});
test('confirmed save removes the recovery copy and preserves edits made while saving',()=>{
 const key=draftKey('alice','target','saved'),value=draft();rememberDraft(key,value);
 const saved={...value,saved:JSON.stringify(value.presentation)};rememberDraft(key,saved);assert.equal(recoverDraft(key),undefined);assert.equal(entries.has(key),false);
 saved.presentation.pages.page.name='typed while saving';rememberDraft(key,saved);assert.equal(recoverDraft(key).presentation.pages.page.name,'typed while saving');
});
test('a persisted tab backup survives module memory loss and retains its original CAS revision',()=>{
 const key=draftKey('alice','target','restored'),value=draft();entries.set(key,JSON.stringify(value));
 assert.deepEqual(recoverDraft(key),value);
 entries.set(draftKey('alice','target','malformed'),'{');assert.equal(recoverDraft(draftKey('alice','target','malformed')),undefined);
});
test('unavailable session storage is reported and preserves a separate in-page recovery copy',()=>{
 const original=globalThis.sessionStorage;globalThis.sessionStorage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')},removeItem(){throw Error('blocked')}};
 try{const key=draftKey('alice','target','memory-only');assert.equal(rememberDraft(key,draft()),false);assert.deepEqual(recoverDraft(key),draft());assert.equal(recoverDraft(draftKey('bob','target','memory-only')),undefined);}
 finally{globalThis.sessionStorage=original;}
});
