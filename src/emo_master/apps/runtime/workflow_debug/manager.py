from copy import deepcopy
import time

from emo_master.apps.runtime.operator_debug.assets import CACHE_BYTES
from emo_master.apps.runtime.operator_debug.contracts import encode, fail, parse
from emo_master.apps.runtime.operator_debug.data import retainedAssetIds


def control(manager, session, action, request):
    if session.spec.get("kind") != "workflow":
        fail("E_DEBUG_CONTEXT_INVALID", "workflow command requires a workflow session")
    if session.flow.get("pendingCommand"):
        fail("E_RESOURCE_BUSY", "previous control is awaiting Worker confirmation")
    if action == "flow_start":
        if session.state != "READY" or session.flow.get("started"):
            fail("E_RESOURCE_BUSY", "workflow start requires a new ready session")
        prepared = session.prepared.get(request.get("inputSetId"))
        if prepared is None:
            fail("E_DEBUG_RESULT_EXPIRED", "input set unavailable")
        command = dict(action="start", inputs=prepared["values"], outputBudget=CACHE_BYTES-session.assets.used)
    else:
        command = parse(request.get("controlJson", "{}"))
        if not isinstance(command, dict) or command.get("action") not in {"pause", "continue", "into", "over", "out", "runTo", "breakpoints", "trial"}:
            fail("E_DEBUG_CONTEXT_INVALID", "invalid workflow control")
        readyBreakpoints = session.state == "READY" and command["action"] == "breakpoints"
        if session.state not in {"RUNNING", "PAUSE_REQUESTED", "PAUSED"} and not readyBreakpoints:
            fail("E_DEBUG_STALE_SESSION", "workflow is not active")
        if command["action"] in {"continue", "into", "over", "out", "runTo", "trial"}:
            if (type(command.get("pauseSequence")) is not int or session.state != "PAUSED"
                    or command.get("pauseSequence") != session.flow.get("pauseSequence") or session.flow.get("trialRunning")):
                fail("E_DEBUG_STALE_PAUSE", "pause moved or a trial is in progress")
    command["requestId"] = request["requestId"]
    encode(command)
    session.worker.control(command)
    session.flow["pendingCommand"] = request["requestId"]
    if action == "flow_start":
        session.flow["started"] = True
        session.state = "RUNNING"
    elif command["action"] == "pause" and session.state == "RUNNING":
        session.state = "PAUSE_REQUESTED"
    return dict(requestId=request["requestId"], status="ACCEPTED")


def consume(manager, session, event):
    kind = event["kind"]
    if kind == "flow_ack":
        entry = session.requests.get(event["requestId"])
        if entry is not None:
            entry[1].update({key: value for key, value in event.items() if key != "kind"})
        if session.flow.get("pendingCommand") == event["requestId"]:
            session.flow["pendingCommand"] = ""
    elif kind == "flow_snapshot":
        snapshot = {key: value for key, value in event.items() if key != "kind"}
        for field in ("inputsAssets", "outputsAssets"):
            for asset in snapshot.get(field, {}).values():
                session.assets.adopt(asset)
        session.checkpoints[snapshot["snapshotId"]] = snapshot
        if snapshot["snapshotKind"] == "paused":
            session.flow.update(pauseSequence=snapshot["pauseSequence"], current=snapshot["snapshotId"])
            if not session.retiringAt:
                session.state = "PAUSED"
        elif snapshot["snapshotKind"] == "trial":
            session.flow["trial"] = snapshot["snapshotId"]
        else:
            session.flow["lastOutput"] = snapshot["snapshotId"]
        session.variables = deepcopy(snapshot.get("variables", session.variables)) if snapshot["snapshotKind"] != "trial" else session.variables
        protected = {session.flow.get(key) for key in ("current", "trial", "lastOutput")}
        while len(session.checkpoints) > 32:
            expired = next(key for key in session.checkpoints if key not in protected)
            del session.checkpoints[expired]
        retained = retainedAssetIds(session)
        for row in session.checkpoints.values():
            for field in ("inputsAssets", "outputsAssets"):
                retained.update(asset["assetId"] for asset in row.get(field, {}).values())
        session.assets.collectOutputs(retained)
        session.worker.snapshotAck.set()
    elif kind == "flow_state":
        if not session.retiringAt:
            session.state = event["state"]
    elif kind == "flow_active":
        session.flow["nodeDeadline"] = time.monotonic() + 30 if event["active"] else 0
    elif kind == "flow_trial_started":
        session.flow.update(trialRunning=True, trialDeadline=time.monotonic() + 30, trial="", trialError={})
    elif kind == "flow_trial_finished":
        session.flow.update(trialRunning=False, trialDeadline=0)
    elif kind == "flow_trial_failed":
        session.flow["trialError"] = {key: value for key, value in event.items() if key != "kind"}
    elif kind == "flow_terminal":
        session.flow.update(nodeDeadline=0, terminal={key: value for key, value in event.items() if key != "kind"})
        if not session.retiringAt:
            session.state = event["state"]
    elif kind == "flow_location":
        manager._event(session, kind, phase=event["phase"], identity=event["identity"],
                       nodeId=event["nodeId"], condition=event.get("condition"))
        return
    manager._event(session, kind, **{key: value for key, value in event.items()
        if key in {"snapshotId", "snapshotKind", "phase", "state", "requestId", "status", "code", "message"}})
