from emo_master.apps.package_selftest import runSelfTest
import pytest

pytest.importorskip("PySide2.QtWidgets")


def testPackageGuiGateRequiresNineCustomRenderedSamples():
    result = runSelfTest(guiIcons=True)
    assert result["status"] == "ok"
    samples = result["checks"]["guiIcons"]["samples"]
    assert len(samples) == 9
    assert all(sample["renderSource"] == "custom" and sample["accentPixels"] >= 8 for sample in samples)
    assert {sample["pixelSize"] for sample in samples} == {24, 36, 48}
