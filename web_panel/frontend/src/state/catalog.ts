import type {Run,WorkflowTarget} from '../contracts/types';
export interface RuntimeInfo {runtimeInstanceId:string;projectId:string;configurationRevision?:string;documentRevision?:string}
export interface CatalogSelection {runId:string;controlRunId:string;workflowId:string;workflowInputs:string;dirty:boolean}
export function reconcileCatalogSelection(previous:CatalogSelection,oldInfo:RuntimeInfo|undefined,info:RuntimeInfo,runs:Run[],workflows:WorkflowTarget[],initialMock=false) {
 const identityChanged=Boolean(oldInfo && (oldInfo.runtimeInstanceId!==info.runtimeInstanceId || oldInfo.projectId!==info.projectId));
 const notices:string[]=[];const next={...previous};
 if(identityChanged){next.runId='';next.controlRunId='';next.workflowId='';notices.push('Runtime 实例或工程已改变；旧查看和控制选择已清除，请重新选择。')}
 else {
  for(const key of ['runId','controlRunId'] as const)if(next[key] && !runs.some(run=>run.id===next[key])){next[key]='';notices.push(key==='runId'?'原查看运行已失效，已清除选择。':'原停止目标已失效，已清除；不会改为其他 Job。')}
  if(next.workflowId && !workflows.some(workflow=>workflow.id===next.workflowId)){next.workflowId='';notices.push('原工作流已失效；输入文本保留，请重新选择并核对。')}
  if(!oldInfo && !next.workflowId){next.workflowId=workflows[0]?.id || '';if(!next.dirty)next.workflowInputs=workflows[0]?.inputsJson || JSON.stringify(workflows[0]?.inputs || {})}
  if(!oldInfo && initialMock && !next.runId)next.runId=runs[0]?.id || '';
 }
 const revisionChanged=Boolean(oldInfo && (oldInfo.configurationRevision || oldInfo.documentRevision)!==(info.configurationRevision || info.documentRevision));
 if(revisionChanged)notices.push('正式配置版本已更新；输入文本未覆盖，请核对最新工作流输入描述。');
 return {selection:next,identityChanged,revisionChanged,notices};
}
export const targetContext=(runtimeId:string,projectId?:string)=>`runtimeId=${encodeURIComponent(runtimeId)}${projectId?`&projectId=${encodeURIComponent(projectId)}`:''}`;
