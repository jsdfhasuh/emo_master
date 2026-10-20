"""Test input selector only: alternate two real ImageLoader outputs at low speed."""
import time


class Selector:
    class Meta:
        inputPorts = {"a": "image", "b": "image", "fa": "bbox2d", "fb": "bbox2d"}
        outputPorts = {"image": "image", "frame": "bbox2d"}
    meta = Meta()

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, runtimeContext):
        time.sleep(1)
        odd = runtimeContext["iterationPath"][-1] % 2
        return {"status": "ok", "outputs": {"image": inputs["b" if odd else "a"],
                                             "frame": inputs["fb" if odd else "fa"]}}
