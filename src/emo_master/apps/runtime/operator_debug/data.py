"""Complete input references and companion provenance at the invocation boundary."""
from copy import deepcopy

from emo_master.core.contracts.port_types import normalizePortType
from emo_master.plugins.builtins._image_frame import frameForInput

from .contracts import encode, fail, inputs, parse


def companionPort(imagePort, ports):
    return {"image": "frame", "imageA": "frameA", "imageB": "frameB", "croppedImage": "croppedFrame",
            "maskA": "frameA", "maskB": "frameB", "mask": "maskFrame" if "maskFrame" in ports else "frame"}.get(imagePort)


def prepareInputs(supplied, ports, assets, executions):
    values, frozen, sources = {}, {}, {}
    for port, item in supplied.items():
        source = {"kind": "inline"}
        if "outputRef" in item:
            reference = item["outputRef"]
            record = executions.get(reference["executionId"])
            if record is None or record["status"] == "RUNNING":
                fail("E_DEBUG_RESULT_EXPIRED", "complete execution output is unavailable")
            source = dict(record["sourceIdentity"], kind="debug-output", captureId=record["executionId"])
            key = reference["port"]
            if key in record.get("outputAssets", {}):
                item = {"assetRef": record["outputAssets"][key]["assetId"]}
            elif key in record.get("outputs", {}):
                item = {"inlineJson": encode(record["outputs"][key])}
            else:
                fail("E_DEBUG_RESULT_EXPIRED", "output port was not retained")
        if "assetRef" in item:
            entry = assets.describe(item["assetRef"])
            source = entry["provenance"]
            values[port] = assets.value(entry["assetId"])
            frozen[port] = {"asset": entry}
        elif "inlineJson" in item:
            values[port] = parse(item["inlineJson"])
            frozen[port] = {"inline": deepcopy(values[port])}
        else:
            fail("E_INPUT_TYPE", "input has no complete value")
        sources[port] = source
    inputs(values, ports, transport=False)
    for imagePort, spec in ports.items():
        if normalizePortType(spec) != "image" or imagePort not in values or values[imagePort] is None:
            continue
        companion = companionPort(imagePort, ports)
        if companion in values:
            first, second = sources[imagePort].get("captureId"), sources[companion].get("captureId")
            if not first or first != second:
                fail("E_INPUT_ORIGIN", "image and frame must belong to the same complete capture")
            height, width = values[imagePort].shape[:2]
            frameForInput(values, width, height, portName=companion)
    encode(frozen)
    return frozen, sources


def materialize(frozen, assets):
    values = {}
    for port, item in frozen.items():
        if "asset" in item:
            entry = item["asset"]
            assets.entries[entry["assetId"]] = entry
            values[port] = assets.value(entry["assetId"])
        else:
            values[port] = deepcopy(item["inline"])
    return values


def retainedAssetIds(session):
    retained = set()
    for prepared in session.prepared.values():
        for item in prepared["values"].values():
            if "asset" in item:
                retained.add(item["asset"]["assetId"])
    for record in session.executions.values():
        retained.update(entry["assetId"] for entry in record.get("outputAssets", {}).values())
    return retained
