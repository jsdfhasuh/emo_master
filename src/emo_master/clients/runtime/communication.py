"""Restricted communication-management client, independent of Designer and Qt.

The observer owns its single I/O thread. Closing this client cancels only its
observations; it never closes the Runtime or stops the shared TCP service.
"""
from __future__ import annotations

import json
import threading
from uuid import uuid4

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.clients.runtime.calls import DisplayCallContext, DisplayCallError


class CommunicationClientError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class CommunicationClient:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.contexts = set()
        self.closed = False

    def communicationState(self, projectId="", afterSequence=0, *, projectPath="", cancellation=None):
        return self._call("GetCommunicationState", pb.CommunicationRequest(protocol_version="1.1",
                          project_id=projectId, after_sequence=afterSequence, project_path=projectPath), cancellation)

    def controlCommunication(self, action, state, *, config=None, requestId=None, saveToken="", cancellation=None):
        return self._call("ControlCommunication", pb.CommunicationRequest(protocol_version="1.1",
            project_id=state["projectId"], project_path=state["projectPath"], save_token=saveToken, runtime_instance_id=state["runtimeInstanceId"],
            project_load_generation=state["projectLoadGeneration"], expected_config_revision=state["desiredRevision"],
            request_id=requestId or uuid4().hex, action=action,
            config_json=json.dumps(config, ensure_ascii=True) if config is not None else ""), cancellation)

    def _call(self, name, request, cancellation):
        context = cancellation or DisplayCallContext()
        with self.lock:
            if self.closed:
                raise CommunicationClientError("E_DISPLAY_CANCELLED", "通信视图已关闭")
            self.contexts.add(context)
        try:
            context.start(5000)
            method = getattr(self.service, name, None)
            if not callable(method):
                raise CommunicationClientError("E_TCP_UNSUPPORTED", "此 Runtime 不支持项目 TCP 通信服务，请更新 Runtime")
            future = getattr(method, "future", None)
            if callable(future):
                call = future(request, timeout=context.time_remaining(), wait_for_ready=False)
                context.attach(call)
                reply = call.result()
            else:
                reply = method(request, context)
            context.check()
            if not reply.ok:
                code = "E_TCP_PROTOCOL_MISMATCH" if reply.code == "E_TCP_UNSUPPORTED" else reply.code
                raise CommunicationClientError(code, reply.message)
            state = json.loads(reply.state_json)
            if not isinstance(state, dict) or state.get("protocolVersion") != "1.1":
                raise CommunicationClientError("E_TCP_PROTOCOL", "无效通信状态，请更新 Runtime")
            return state
        except grpc.RpcError as error:
            try:
                context.check()
            except DisplayCallError as cancelled:
                raise CommunicationClientError(cancelled.code, str(cancelled)) from error
            code = "E_TCP_UNSUPPORTED" if error.code() == grpc.StatusCode.UNIMPLEMENTED else str(error.code())
            raise CommunicationClientError(code, error.details() or "通信管理 RPC 失败") from error
        except DisplayCallError as error:
            raise CommunicationClientError(error.code, str(error)) from error
        finally:
            with self.lock:
                self.contexts.discard(context)

    def closeDisplayOwner(self, owner):
        with self.lock:
            self.closed = True
            contexts = list(self.contexts)
        for context in contexts:
            context.cancel()
