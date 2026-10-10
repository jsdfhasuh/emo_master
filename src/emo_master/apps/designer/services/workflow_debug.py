import threading
import time
from uuid import uuid4

from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import MAX_REQUEST_BYTES, encode


class WorkflowDebugConnection(DebugConnection):
    def __init__(self, client):
        super().__init__(client)
        self.preStartBreakpoints = False

    def call(self, method, **fields):
        return super().call(method.replace("OperatorDebug", "WorkflowDebug"), **fields)

    def openWorkflow(self, payload, workflowId):
        capability = self.call("GetWorkflowDebugCapabilities")
        if capability.get("debugKind") != "workflow":
            raise RuntimeClientError("E_DEBUG_UNSUPPORTED", "Runtime does not support workflow debugging")
        self.preStartBreakpoints = capability.get("preStartBreakpoints") is True
        self.identity = dict(runtime_instance_id=capability["runtimeInstanceId"])
        try:
            state = self.call("OpenWorkflowDebugSession", open_request_id=self.openId,
                project_json=encode(payload, MAX_REQUEST_BYTES), project_id=payload["project"]["projectId"], workflow_id=workflowId)
        except Exception as error:
            if isinstance(error, RuntimeClientError) and error.code.startswith("E_"):
                self.identity = {}
                raise
            state = self.call("GetWorkflowDebugSession", open_request_id=self.openId)
        self.identity.update(session_id=state["sessionId"], generation=state["generation"])
        self.renewer = threading.Thread(target=self._renew, name="designer-workflow-debug-lease", daemon=True)
        self.renewer.start()
        return dict(capability=capability, session=state)

    def confirmed(self, method, **fields):
        if self.uncertain:
            raise RuntimeClientError("E_DEBUG_UNCERTAIN", "End this session before another command")
        requestId = uuid4().hex
        try:
            self.call(method, request_id=requestId, **fields)
        except RuntimeClientError as error:
            if error.code.startswith("E_"):
                raise
        except Exception:
            pass
        deadline = time.monotonic() + 6
        try:
            while time.monotonic() < deadline:
                response = self.call("GetWorkflowDebugCommand", request_id=requestId)
                if response["status"] == "REJECTED":
                    raise RuntimeClientError(response["code"], response.get("message", "Command rejected"))
                if response["status"] == "CONFIRMED":
                    return response
                time.sleep(.02)
        except RuntimeClientError as error:
            if error.code not in {"E_DEBUG_RESULT_EXPIRED", "E_DEBUG_STALE_SESSION"} and error.code.startswith("E_"):
                raise
        except Exception:
            pass
        self.uncertain = True
        raise RuntimeClientError("E_DEBUG_UNCERTAIN", "Control acknowledgement unavailable; request " + requestId)

    def start(self, inputs, *, breakpoints=None):
        if breakpoints and not self.preStartBreakpoints:
            raise RuntimeClientError("E_DEBUG_UNSUPPORTED", "当前 Runtime 不支持启动前断点，请升级 Runtime 后重试；未启动流程")
        wire = {port: pb.OperatorDebugValue(inline_json=encode(value["inline"])) if "inline" in value
                else pb.OperatorDebugValue(asset_ref=value["assetRef"]) for port, value in inputs.items()}
        prepared = self.mutation("PrepareWorkflowDebugInputs", inputs=wire)
        if breakpoints is not None and self.preStartBreakpoints:
            self.control("breakpoints", breakpoints=breakpoints)
        if self.stop.is_set():
            raise RuntimeClientError("E_DEBUG_STALE_SESSION", "Debug session is closing")
        return self.confirmed("StartWorkflowDebug", input_set_id=prepared["inputSetId"])

    def control(self, action, pauseSequence=0, **fields):
        return self.confirmed("ControlWorkflowDebug", control_json=encode(dict(action=action, pauseSequence=pauseSequence, **fields)))

    def flowSnapshot(self, sequence=0):
        state = self.call("GetWorkflowDebugSession")
        events = self.call("ReadWorkflowDebugEvents", after_sequence=sequence)
        return dict(session=state, events=events, leaseError=self.leaseError)
