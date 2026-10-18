"""Bounded invocation queues; worker death is terminal, never an implicit retry."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4

from emo_master.apps.runtime.devices.executor import DeviceError


def frameIds(value):
    if value["kind"] == "frame":
        return {value["value"]["frameId"]}
    if value["kind"] == "dict":
        return set().union(*(frameIds(v) for v in value["value"].values()))
    if value["kind"] == "list":
        return set().union(*(frameIds(v) for v in value["value"]))
    if value["kind"] != "value":
        raise ValueError("invalid dispatch value kind")
    return set()


def validateFrames(value, descriptors):
    if value["kind"] == "frame" and value["value"] != descriptors[value["value"]["frameId"]]:
        raise DeviceError("E_DISPATCH_FRAME_SOURCE", "frame descriptor differs from the authoritative original")
    if value["kind"] in {"dict", "list"}:
        children = value["value"].values() if value["kind"] == "dict" else value["value"]
        for child in children:
            validateFrames(child, descriptors)


@dataclass
class Slot:
    workflowId: str
    capacity: int
    jobId: str
    ready: bool = False
    retired: bool = False
    queue: deque = field(default_factory=deque)
    active: str = ""


class RunGroup:
    def __init__(self, source, slots, devices, document):
        self.groupId = uuid4().hex
        self.source = source
        self.slots = slots
        self.devices = devices
        self.document = document
        self.lock = RLock()
        self.sourceEnded = False
        self.stopping = False
        self.records = {}
        self.deliveries = {}
        self.highWater = {}
        self.closedCycles = set()
        self.retired = set()

    @property
    def members(self):
        return {self.source, *(s.jobId for s in self.slots.values())}

    def handle(self, owner, action, request):
        with self.lock:
            if owner not in self.members:
                raise DeviceError("E_DISPATCH_OWNER", "Job does not belong to this run group")
            if action == "dispatchReady":
                if self.stopping or any(s.retired for s in self.slots.values()):
                    raise DeviceError("E_DISPATCH_UNAVAILABLE", "consumer failed before run-group admission")
                return all(s.ready for s in self.slots.values())
            if action == "dispatchNext":
                return self.next(owner)
            if action == "dispatchSubmit":
                return self.submit(owner, request)
            if action == "dispatchComplete":
                return self.complete(owner, request)
            if action == "dispatchJoin":
                return self.join(owner, request)
            raise DeviceError("E_DISPATCH_ACTION", "unsupported scheduling action")

    def submit(self, owner, request):
        # Transport sequence protects the pipe; logical dispatch identity survives
        # retransmission on a later envelope without replaying accepted targets.
        request = {key: value for key, value in request.items() if key not in {"sequence", "action"}}
        sequence = request["dispatchSequence"]
        if type(sequence) is not int or sequence < 1:
            raise DeviceError("E_DISPATCH_SEQUENCE", "invalid dispatch sequence")
        key = (owner, sequence)
        if sequence <= self.highWater.get(owner, 0):
            existing = self.records.get(key)
            if existing is None:
                raise DeviceError("E_DISPATCH_EXPIRED", "delivery receipt expired; invocation will not replay")
            if existing["request"] != request:
                raise DeviceError("E_DISPATCH_IDENTITY", "duplicate delivery changed its payload")
            return deepcopy(existing["token"])
        if self.stopping:
            raise DeviceError("E_DISPATCH_STOPPING", "run group is stopping")
        if len(self.records) >= 256:
            raise DeviceError("E_DISPATCH_CAPACITY", "run-group receipt capacity exhausted")
        workflow = self.document.workflows[request["workflowId"]]
        node = next((n for n in workflow.nodes if n.nodeId == request["nodeId"] and n.kind == "dispatch"), None)
        if node is None:
            raise DeviceError("E_DISPATCH_CONTRACT", "unknown Dispatch declaration")
        cycleId, revision = request["cycleId"], request["planRevision"]
        configuration = self.devices.configuration
        if configuration is None or configuration.requireCycle(owner, cycleId) != revision:
            raise DeviceError("E_DISPATCH_PLAN", "dispatch requires its source cycle plan")
        token = dict(runGroupId=self.groupId, dispatchId=uuid4().hex, cycleId=cycleId,
                     planRevision=revision, nodeId=node.nodeId, workflowId=request["workflowId"], targets={})
        record = dict(owner=owner, request=deepcopy(request), token=token, deliveries=[])
        self.records[key] = record
        self.highWater[owner] = sequence
        for target in node.params["targets"]:
            slotId = target["targetSlotId"]
            slot = self.slots[slotId]
            deliveryId = uuid4().hex
            invocation = dict(runGroupId=self.groupId, dispatchId=token["dispatchId"], deliveryId=deliveryId,
                              invocationId=uuid4().hex, targetSlotId=slotId, cycleId=cycleId,
                              planRevision=revision, status="REJECTED", code="", outputs=None)
            record["deliveries"].append(deliveryId)
            self.deliveries[deliveryId] = invocation
            try:
                if slot.retired or not slot.ready:
                    raise DeviceError("E_DISPATCH_UNAVAILABLE", "target worker is unavailable")
                if len(slot.queue) >= slot.capacity:
                    raise DeviceError("E_DISPATCH_QUEUE_FULL", "target queue is full")
                values = {port: request["inputs"][source] for port, source in target["inputs"].items()}
                frames = frameIds(dict(kind="dict", value=values))
                descriptors = {}
                for frame in frames:
                    descriptor = self.devices.frames.retain(frame, owner, deliveryId)
                    if descriptor["cycleId"] != cycleId or descriptor["planRevision"] != revision:
                        raise DeviceError("E_DISPATCH_FRAME_SOURCE", "frame does not belong to this cycle/plan")
                    descriptors[frame] = descriptor
                validateFrames(dict(kind="dict", value=values), descriptors)
                cycle = configuration.retainCycle(owner, cycleId, deliveryId)
                invocation.update(inputs=deepcopy(values), frames=descriptors, cycle=cycle, status="QUEUED")
                slot.queue.append(deliveryId)
            except Exception as error:
                self.devices.frames.retireOwner(deliveryId)
                configuration.retireOwner(deliveryId)
                invocation.update(code=getattr(error, "code", "E_DISPATCH_INPUT"), message=str(error))
            token["targets"][slotId] = {k: deepcopy(invocation[k]) for k in (
                "deliveryId", "invocationId", "status", "code")}
        token["accepted"] = all(v["status"] == "QUEUED" for v in token["targets"].values())
        return deepcopy(token)

    def next(self, owner):
        slot = next((s for s in self.slots.values() if s.jobId == owner), None)
        if slot is None or slot.retired:
            raise DeviceError("E_DISPATCH_OWNER", "Job is not a live target slot")
        slot.ready = True
        if slot.active:
            raise DeviceError("E_DISPATCH_ACTIVE", "previous invocation must complete first")
        if slot.queue and not self.stopping:
            deliveryId = slot.queue.popleft()
            invocation = self.deliveries[deliveryId]
            self.devices.configuration.retainCycle(deliveryId, invocation["cycleId"], owner)
            for frame in invocation["frames"]:
                self.devices.frames.retain(frame, deliveryId, owner)
            slot.active = deliveryId
            invocation["status"] = "RUNNING"
            return deepcopy(invocation)
        pending = any(s.queue or s.active for s in self.slots.values())
        return dict(status="CLOSED" if self.stopping or (self.sourceEnded and not pending) else "IDLE")

    def complete(self, owner, request):
        slot = next(s for s in self.slots.values() if s.jobId == owner)
        invocation = self.deliveries.get(request["deliveryId"])
        if invocation is None or slot.active != request["deliveryId"]:
            raise DeviceError("E_DISPATCH_IDENTITY", "completion does not match active invocation")
        for key in ("invocationId", "cycleId", "planRevision", "dispatchId", "runGroupId"):
            if request[key] != invocation[key]:
                raise DeviceError("E_DISPATCH_IDENTITY", "completion identity mismatch: " + key)
        outputs = request.get("outputs")
        if request["status"] not in {"COMPLETED", "FAILED"}:
            raise DeviceError("E_DISPATCH_STATUS", "invalid completion status")
        outputFrames = frameIds(outputs) if outputs is not None else set()
        descriptors = {}
        for frame in outputFrames:
            descriptors[frame] = self.devices.frames.retain(frame, owner, invocation["deliveryId"])
            if (descriptors[frame]["cycleId"], descriptors[frame]["planRevision"]) != (
                    invocation["cycleId"], invocation["planRevision"]):
                raise DeviceError("E_DISPATCH_FRAME_SOURCE", "output belongs to another cycle/plan")
        if outputs is not None:
            validateFrames(outputs, descriptors)
        invocation.update(status=request["status"], outputs=outputs, code=request.get("code", ""),
                          message=request.get("message", ""))
        for frame in invocation["frames"]:
            if frame not in outputFrames:
                self.devices.frames.release(frame, invocation["deliveryId"])
        self.devices.frames.retireOwner(owner)
        self.devices.configuration.retireOwner(owner)
        self.devices.configuration.retireOwner(invocation["deliveryId"])
        slot.active = ""
        self.endCycle(owner, invocation["cycleId"])
        return None

    def join(self, owner, request):
        token = request["token"]
        record = next((r for r in self.records.values() if r["token"] == token and r["owner"] == owner), None)
        if record is None:
            raise DeviceError("E_DISPATCH_IDENTITY", "Join token is expired, foreign, or altered")
        if request["cycleId"] != token["cycleId"] or request["planRevision"] != token["planRevision"]:
            raise DeviceError("E_DISPATCH_IDENTITY", "Join belongs to another cycle/plan")
        results = {self.deliveries[d]["targetSlotId"]: self.deliveries[d] for d in record["deliveries"]}
        if any(v["status"] in {"QUEUED", "RUNNING"} for v in results.values()):
            return dict(status="WAITING")
        for result in results.values():
            if result.get("outputs") is not None:
                for frame in frameIds(result["outputs"]):
                    self.devices.frames.retain(frame, result["deliveryId"], owner)
        return dict(status="COMPLETED" if all(v["status"] == "COMPLETED" for v in results.values()) else "FAILED",
                    results=deepcopy(results))

    def endCycle(self, owner, cycleId):
        with self.lock:
            if any(r["owner"] == owner and r["token"]["cycleId"] == cycleId for r in self.records.values()):
                self.closedCycles.add((owner, cycleId))
            self._expire()

    def _expire(self):
        for key, record in list(self.records.items()):
            cycle = record["owner"], record["token"]["cycleId"]
            if cycle not in self.closedCycles and record["owner"] not in self.retired:
                continue
            if any(self.deliveries[d]["status"] in {"QUEUED", "RUNNING"} for d in record["deliveries"]):
                continue
            for delivery in record["deliveries"]:
                self.devices.frames.retireOwner(delivery)
                self.devices.configuration.retireOwner(delivery)
                del self.deliveries[delivery]
            del self.records[key]
        self.closedCycles.intersection_update((r["owner"], r["token"]["cycleId"]) for r in self.records.values())

    def stop(self):
        with self.lock:
            self.stopping = True
            for slot in self.slots.values():
                while slot.queue:
                    invocation = self.deliveries[slot.queue.popleft()]
                    invocation.update(status="CANCELLED", code="E_CANCELLED")
                    self.devices.frames.retireOwner(invocation["deliveryId"])
                    self.devices.configuration.retireOwner(invocation["deliveryId"])

    def workerRetired(self, owner):
        """Only supervisor process retirement may release an interrupted reader."""
        with self.lock:
            self.retired.add(owner)
            if owner == self.source:
                self.sourceEnded = True
            for slot in self.slots.values():
                if slot.jobId != owner:
                    continue
                slot.retired = True
                for deliveryId in [*slot.queue, *([slot.active] if slot.active else [])]:
                    invocation = self.deliveries[deliveryId]
                    invocation.update(status="FAILED", code="E_DISPATCH_WORKER_EXITED",
                                      message="consumer exited; effects may be unknown; no replay")
                    self.devices.frames.retireOwner(deliveryId)
                    self.devices.configuration.retireOwner(deliveryId)
                slot.active = ""
                slot.queue.clear()
            self._expire()
