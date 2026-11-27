"""Opt-in diagnostic retention of the benchmark's own immutable PNG assets."""
import json
import os
from pathlib import Path
import re
from threading import Lock

MAX_IMAGES = 96
LIMIT = 8 * 1024 * 1024
IDENTITY = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ARMS = ("trial-0-credit-off", "trial-1-credit-on", "trial-2-credit-on", "trial-3-credit-off")


class RetainBytes:
    def __init__(self, root, arm):
        if arm not in ARMS or not root.is_absolute():
            raise ValueError("unexpected diagnostic retention location")
        self.output = root / arm
        self.output.mkdir(exist_ok=False)
        self.rows = []
        self.errors = 0
        self.lock = Lock()

    def wrap(self, original):
        def keep(store, staging, jobId, resultKey, provenance):
            with store.lock:
                outcome = original(store, staging, jobId, resultKey, provenance)
                with self.lock:
                    try:
                        if (len(self.rows) >= MAX_IMAGES or not isinstance(resultKey, str)
                                or not IDENTITY.fullmatch(resultKey) or outcome["ownerResultKey"] != resultKey):
                            raise ValueError("unexpected result identity")
                        resource = outcome["resourceId"]
                        size = outcome["byteSize"]
                        sha = outcome["sha256"]
                        if not (IDENTITY.fullmatch(resource) and type(size) is int and 8 <= size <= LIMIT
                                and re.fullmatch(r"[0-9a-f]{64}", sha)):
                            raise ValueError("unexpected asset")
                        path = store.items[resource]["path"]
                        if path.parent != store.root or path.name != resource:
                            raise ValueError("unexpected asset path")
                        # Same-volume hardlink retains the original file across
                        # deletion without reading/copying the PNG on this thread.
                        os.link(path, self.output / (resultKey + ".png"))
                        self.rows.append({"result_key": resultKey, "job": jobId,
                                          "asset": resource, "size": size, "sha256": sha})
                    except Exception:
                        self.errors += 1
                return outcome
        return keep

    def save(self):
        with self.lock, (self.output / "links.json").open("x", encoding="ascii") as f:
            json.dump({"version": 1, "errors": self.errors, "files": self.rows}, f,
                      sort_keys=True, separators=(",", ":"))
