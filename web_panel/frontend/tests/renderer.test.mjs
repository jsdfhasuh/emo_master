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
