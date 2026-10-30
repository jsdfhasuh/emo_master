import type { WorkflowTarget } from '../contracts/types';
/** Limited JSON-schema form mapping: never execute schema code, HTML or patterns. */
export function WorkflowInputs({workflow,value,onChange}:{workflow?:WorkflowTarget;value:string;onChange:(value:string)=>void}) {
 let parsed: Record<string,unknown>={};try{const x=JSON.parse(value);if(x&&typeof x==='object'&&!Array.isArray(x))parsed=x}catch{/* Raw JSON error is reported on submission. */}
 const properties=workflow?.inputSchema?.properties;
 const fields=properties&&typeof properties==='object'&&!Array.isArray(properties)?Object.entries(properties as Record<string,unknown>):[];
 const set=(key:string,next:unknown)=>onChange(JSON.stringify({...parsed,[key]:next},null,2));
 return <><div className="workflow-fields">{fields.map(([key,raw])=>{if(!raw||typeof raw!=='object')return null;const field=raw as Record<string,unknown>,label=typeof field.title==='string'?field.title:key;
 if(Array.isArray(field.enum)&&field.enum.every(v=>['string','number','boolean'].includes(typeof v)))return <label key={key}>{label}<select aria-label={`输入 ${key}`} value={JSON.stringify(parsed[key]??null)} onChange={e=>set(key,JSON.parse(e.target.value))}><option value="null">未设置</option>{field.enum.map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{String(v)}</option>)}</select></label>;
 if(field.type==='boolean')return <label key={key}>{label}<select aria-label={`输入 ${key}`} value={parsed[key]===true?'true':parsed[key]===false?'false':'null'} onChange={e=>set(key,JSON.parse(e.target.value))}><option value="null">未设置</option><option value="true">是</option><option value="false">否</option></select></label>;
 if(['string','number','integer'].includes(String(field.type)))return <label key={key}>{label}<input aria-label={`输入 ${key}`} type={field.type==='string'?'text':'number'} step={field.type==='integer'?1:'any'} min={typeof field.minimum==='number'?field.minimum:undefined} max={typeof field.maximum==='number'?field.maximum:undefined} value={String(parsed[key]??'')} onChange={e=>set(key,field.type==='string'?e.target.value:e.target.value===''?null:Number(e.target.value))}/></label>;
 return <p key={key}>{label}：此字段需通过下方 JSON 输入；最终由 Runtime 合同校验。</p>;
 })}</div><label>工作流输入（JSON 对象）<textarea aria-label="工作流输入" value={value} onChange={e=>onChange(e.target.value)} rows={2}/></label></>;
}
