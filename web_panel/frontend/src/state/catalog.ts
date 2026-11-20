import type {Run,WorkflowTarget} from '../contracts/types';
export interface RuntimeInfo {
 runtimeInstanceId:string;
 projectId:string;
 configurationRevision?:string;
 documentRevision?:string;
 mode?:string;
 productionMode?:boolean;
 productionExecutionMode?:string;
 capabilities?:string[];
}

export function projectRunModeLabel(info?: RuntimeInfo): string {
 if (!info) return '待获取';
 if (!info.projectId) return '未加载工程';
 if (info.mode === 'mock' || info.mode === 'fixtures') return '模拟演示';
 if (info.productionExecutionMode === 'single') return '单次运行';
 if (info.productionExecutionMode === 'continuous') {
  if (info.productionMode === false && !info.capabilities?.includes('project_run_mode_v1')) {
   return '持续运行（需升级 Runtime）';
  }
  return '持续运行';
 }
 return 'Runtime 未提供';
}

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
export const targetContext=(runtimeId:string,projectId?:string,runtimeInstanceId?:string)=>`runtimeId=${encodeURIComponent(runtimeId)}${projectId?`&projectId=${encodeURIComponent(projectId)}`:''}${runtimeInstanceId?`&runtimeInstanceId=${encodeURIComponent(runtimeInstanceId)}`:''}`;

/** Keep formal observations received during a directory read, including Jobs
 * that its earlier snapshot omitted. Unchanged missing rows still retire. */
export function mergeRunCatalog(before:readonly Run[],current:readonly Run[],incoming:Run[],info:RuntimeInfo):Run[] {
 const observations=current.filter(run=>!before.includes(run) && run.runtimeInstanceId===info.runtimeInstanceId && run.projectId===info.projectId);
 const merged=incoming.map(run=>{
  const observed=observations.find(item=>item.id===run.id && item.runtimeInstanceId===run.runtimeInstanceId && item.projectId===run.projectId);
  if(!observed)return run;
  const {status,state,active,ownsResources,displayResourcesReleased,allowedActions}=observed;
  return {...run,status,state,active,ownsResources,displayResourcesReleased,allowedActions};
 });
 return [...merged,...observations.filter(run=>!incoming.some(item=>item.id===run.id))];
}

/** Catalog status belongs only to the selected view Job, never the Stop target. */
export function selectedViewState(runId:string,info:RuntimeInfo,runs:Run[]):string {
 const run=runs.find(item=>item.id===runId && item.runtimeInstanceId===info.runtimeInstanceId && item.projectId===info.projectId);
 return run?.status || run?.state || '';
}
