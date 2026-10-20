from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

import emo_master.plugins.builtins._huaray_imv as imv


def testResolveDllUsesProcessArchitecture(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "Runtime" / ("x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "Win32")
    runtime.mkdir(parents=True)
    dll = runtime / "MVSDKmd.dll"
    dll.write_bytes(b"fake")
    monkeypatch.setattr(imv, "candidateMvViewerRoots", lambda: (tmp_path,))

    assert imv.resolveImvDllPath() == dll


def testResolveDllReportsEveryCheckedPath(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(imv, "candidateMvViewerRoots", lambda: (tmp_path,))

    with pytest.raises(imv.HuarayCameraError) as error:
        imv.resolveImvDllPath()

    assert error.value.code == "E_CAMERA_SDK_UNAVAILABLE"
    assert error.value.diagnostics["checkedPaths"]


def testCtypesStructuresMatchImv251AbiOn64Bit() -> None:
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit ABI size assertions")

    assert ctypes.sizeof(imv.IMV_FrameInfo) == 136
    assert ctypes.sizeof(imv.IMV_Frame) == 192
    assert ctypes.sizeof(imv.IMV_PixelConvertParam) == 96


def testImportAndConstructionDoNotLoadVendorDll(monkeypatch) -> None:
    called = False

    def failIfResolved() -> Path:
        nonlocal called
        called = True
        raise AssertionError("must stay lazy")

    monkeypatch.setattr(imv, "resolveImvDllPath", failIfResolved)
    from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator

    HuarayCameraOperator()

    assert called is False
