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
