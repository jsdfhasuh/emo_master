"""Small trusted output adapters. No identity inference from image dimensions."""
from collections import OrderedDict
from uuid import uuid4
import weakref


ADAPTERS = {"vision.io.image_loader": "1.1.0", "vision.analysis.blob": "1.2.0"}


class FrameTracker:
    def __init__(self, versions):
        self.versions = versions
        self.frames = OrderedDict()

    def lookup(self, image):
        entry = self.frames.get(id(image))
        return entry[1] if entry and entry[0]() is image else None

    def observe(self, node, inputs, outputs):
        import numpy as np
        operatorId = node.operatorId
        trusted = self.versions.get(operatorId) == ADAPTERS.get(operatorId) and operatorId in ADAPTERS
        parent = self.lookup(inputs.get("image")) if operatorId == "vision.analysis.blob" else None
        for port, value in outputs.items():
            if not isinstance(value, np.ndarray):
                continue
            provenance = {"frameIdentity": str(uuid4()), "coordinateSpaceId": str(uuid4()), "trust": "unknown"}
            if trusted and operatorId == "vision.io.image_loader" and port == "image":
                provenance.update(trust="proven", adapterVersion="image-loader-1.1.0")
            elif trusted and operatorId == "vision.analysis.blob" and port in {"mask", "overlay"} and parent and parent["trust"] == "proven":
                provenance.update(trust="proven", adapterVersion="blob-1.2.0",
                                  parentFrameIdentity=parent["frameIdentity"], coordinateSpaceId=parent["coordinateSpaceId"])
            self.frames[id(value)] = (weakref.ref(value), provenance)
            self.frames.move_to_end(id(value))
            while len(self.frames) > 16:
                self.frames.popitem(last=False)
