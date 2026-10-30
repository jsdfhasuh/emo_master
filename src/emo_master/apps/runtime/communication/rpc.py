"""Versioned management API. Workers have a separate, Job-scoped send path."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _pb
from emo_master.core.contracts.tcp_service import TcpServiceError
from emo_master.core.project.communication import CommunicationSettings
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument

pb: Any = _pb
MAX_CONTROL_REQUESTS = 2048


class CommunicationRpcMixin:
    def GetCommunicationState(self, request, context):
        runtime: Any = self
        if runtime.loadedDocument is not None and runtime.loadedDocument.schemaVersion in {'2.7', '2.8'}:
            return pb.CommunicationReply(ok=False, code='E_DEVICE_MANAGEMENT_REQUIRED',
                message='TCP 已纳入设备管理，请选择 deviceId 查看状态和控制')
        try:
            if request.protocol_version != "1.1":
                raise TcpServiceError("E_TCP_UNSUPPORTED", "不支持的项目 TCP 通信协议版本")
            state = runtime.communicationManager.snapshot(request.after_sequence)
            if ((request.project_id and request.project_id != state["projectId"])
                    or (request.project_path and os.path.normcase(str(Path(request.project_path).resolve())) != state["projectPath"])):
                raise TcpServiceError("E_TCP_PROJECT_MISMATCH", "Runtime 尚未加载此项目，请先同步工程")
            state.update(supported=True, isolatedSendDebug=False)
            return pb.CommunicationReply(ok=True, state_json=json.dumps(state, ensure_ascii=True))
        except Exception as error:
            return pb.CommunicationReply(ok=False, code=getattr(error, "code", "E_TCP_STATE"), message=str(error))

    def ControlCommunication(self, request, context):
        runtime: Any = self
        if runtime.loadedDocument is not None and runtime.loadedDocument.schemaVersion in {'2.7', '2.8'}:
            return pb.CommunicationReply(ok=False, code='E_DEVICE_MANAGEMENT_REQUIRED',
                message='请使用统一设备控制；配置变更从 Runtime 菜单写入')
        manager = runtime.communicationManager
        try:
            runtime.jobSupervisor.assertMutationAllowed()
            if request.ByteSize() > 65536 or len(request.request_id) > 160:
                raise TcpServiceError("E_TCP_LIMIT", "通信管理请求超过上限")
            if request.protocol_version != "1.1":
                raise TcpServiceError("E_TCP_UNSUPPORTED", "不支持的项目 TCP 通信协议版本")
            with runtime._projectStateLock:
                if (request.runtime_instance_id != runtime.runtimeInstanceId
                        or request.project_id != runtime.loadedProjectId or not runtime.loadedDocument
                        or not request.project_path
                        or os.path.normcase(str(Path(request.project_path).resolve())) != manager.projectKey
                        or request.project_load_generation != manager.projectLoadGeneration):
                    raise TcpServiceError("E_TCP_STALE_CONTEXT", "Runtime 或项目代次已改变，请刷新通信状态")
                if runtime._closing:
                    raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime 正在关闭")
                configuration = getattr(runtime, "projectConfiguration", None)
                if configuration is not None and configuration.active and request.action != "STOP":
                    raise TcpServiceError("E_CONFIG_WRITER_BUSY", "online configuration transition is pending")
                if context is not None:
                    check = getattr(context, "check", None)
                    if callable(check):
                        check()
                    elif not context.is_active():
                        raise TcpServiceError("E_TCP_CONTROL_EXPIRED", "通信控制请求已取消或超时")
                if not request.request_id:
                    raise TcpServiceError("E_TCP_REQUEST_ID", "通信控制需要唯一请求标识")
                digest = hashlib.sha256(request.SerializeToString(deterministic=True)).hexdigest()
                saved = runtime._communicationRequests.get(request.request_id)
                if saved:
                    if saved[0] != digest:
                        raise TcpServiceError("E_TCP_REQUEST_MISMATCH", "同一控制请求的数据发生变化")
                    if saved[1].ok:
                        return pb.CommunicationReply(ok=True, message="此请求已处理，返回当前状态；未重复执行",
                            state_json=json.dumps(manager.snapshot(), ensure_ascii=True))
                    return saved[1]
                if len(runtime._communicationRequests) >= MAX_CONTROL_REQUESTS:
                    raise TcpServiceError("E_TCP_LIMIT", "通信管理请求记录已满，请重启 Runtime；不会淘汰后重放操作")
                # Reserve before dispatch. A lost reply can be queried with the same
                # request; an incomplete dispatch can never become a second action.
                unknown = pb.CommunicationReply(ok=False, code="E_TCP_CONTROL_UNKNOWN",
                    message="控制结果尚未确认，请刷新状态；不要自动重试新操作")
                runtime._communicationRequests[request.request_id] = (digest, unknown)
                try:
                    state = manager.snapshot()
                    if request.action != "ABORT_DISABLE" and request.expected_config_revision != state["desiredRevision"]:
                        raise TcpServiceError("E_TCP_CONFIG_STALE", "通信配置已改变，请刷新后操作")
                    if request.action == "SYNC":
                        configuration = getattr(runtime, "projectConfiguration", None)
                        if configuration is not None and configuration.current is not None:
                            raise TcpServiceError("E_CONFIG_UNIFIED_WRITE", "在线工程的通信配置必须通过统一配置写入接口修改")
                        config = CommunicationSettings.model_validate_json(request.config_json)
                        if runtime.loadedDocument.communication is None and config == CommunicationSettings():
                            candidate = runtime.loadedDocument
                        else:
                            payload = migrateProjectPayload(runtime.loadedDocument.model_dump(mode="python"),
                                                            enableCommunication=True)
                            payload["communication"] = config.model_dump()
                            candidate = ProjectDocument.model_validate(payload)
                        from emo_master.core.workflow.compiler import WorkflowCompiler
                        WorkflowCompiler(runtime.pluginScanResult.activeOperators).compile(candidate)
                        manager.configure(runtime.loadedProjectId, manager.projectKey, config, saveToken=request.save_token)
                        runtime.loadedDocument = candidate
                        runtime.loadedPayload = candidate.model_dump(mode="json")
                    elif request.action == "PREPARE_DISABLE":
                        manager.prepareDisableSave(request.save_token)
                    elif request.action == "PREPARE_SAVE":
                        manager.prepareSave(request.save_token)
                    elif request.action == "ABORT_DISABLE":
                        manager.abortDisableSave(request.save_token)
                    elif request.action == "START":
                        # Same camera/debug protection as formal StartJob. No
                        # implicit session closure or replay of device writes.
                        with runtime._previewJobLock:
                            if runtime.livePreviewManager.hasConfigurationSession() or runtime.operatorDebugManager.ownsResources():
                                raise TcpServiceError("E_RESOURCE_BUSY", "请先关闭相机配置或算子调试会话")
                            manager.start()
                    elif request.action == "STOP":
                        manager.controlRevision += 1
                        manager.stop()
                    elif request.action == "UNLOAD":
                        configuration = getattr(runtime, "projectConfiguration", None)
                        if configuration is not None and configuration.current is not None:
                            raise TcpServiceError("E_CONFIG_UNIFIED_WRITE", "在线工程不能通过旧通信接口卸载")
                        with runtime._previewJobLock:
                            if (runtime.livePreviewManager.hasConfigurationSession() or runtime.operatorDebugManager.ownsResources()
                                    or any(not record.isTerminal or runtime.jobSupervisor.ownsJobResources(record.jobId)
                                           for record in runtime.jobRepository.all())):
                                raise TcpServiceError("E_RESOURCE_BUSY", "请先停止并释放流程、相机配置和调试会话再切换工程")
                            runtime._clearLoadedProject("项目已卸载，TCP 连接已释放")
                    else:
                        raise TcpServiceError("E_TCP_CONTROL", "未知通信控制动作")
                    reply = pb.CommunicationReply(ok=True, state_json=json.dumps(manager.snapshot(), ensure_ascii=True))
                except Exception as error:
                    reply = pb.CommunicationReply(ok=False, code=getattr(error, "code", "E_TCP_CONTROL"), message=str(error))
                runtime._communicationRequests[request.request_id] = (digest, reply)
                return reply
        except Exception as error:
            return pb.CommunicationReply(ok=False, code=getattr(error, "code", "E_TCP_CONTROL"), message=str(error))
