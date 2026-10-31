/** Acknowledgement of a command is distinct from the selected Job's lifetime. */
export interface CommandReply {
 state?:string;outcome?:string;operationId?:string;runId?:string;jobId?:string;status?:string;message?:string;
 run?:{id?:string;jobId?:string;status?:string;state?:string;ownsResources?:boolean;displayResourcesReleased?:boolean;active?:boolean};
}
export function commandResolution(kind:'start'|'stop',reply:CommandReply) {
 const state=(reply.state || reply.outcome || 'unknown').toLowerCase();
 const runId=reply.runId || reply.jobId || reply.run?.jobId || reply.run?.id;
 if(['rejected','failed','cancelled'].includes(state))return {confirmed:true,unknown:false,rejected:true,runId,message:`操作被拒绝 / 未执行成功：${reply.message || state}`};
 if(state==='accepted' && (kind==='stop' || runId))return {confirmed:true,unknown:false,rejected:false,runId,message:kind==='start'?`启动已受理：${runId}；Job 尚未结束，可明确选择后停止。`:`停止已受理：${runId || '原选中 Job'}；停止中，结束及资源释放以 Runtime 状态为准。`};
 if(state==='succeeded')return {confirmed:true,unknown:false,rejected:false,runId,message:kind==='stop'?`原 Job 已确认：${reply.run?.status || reply.status || '查询完成'}；资源状态以 Runtime 返回为准。`:`操作已受理：${reply.operationId || runId || 'start'}`};
 return {confirmed:false,unknown:true,rejected:false,runId,message:reply.message || '命令结果尚未确认；请查询原请求，不自动重发。'};
}
