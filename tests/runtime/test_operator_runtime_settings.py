from pathlib import Path

import pytest

from emo_master.apps.operator_runtime.settings import resolveDataRoot, validateSavedDataRoot


@pytest.mark.parametrize("explicit,environment,saved,source", [
    ("explicit", "environment", "saved", "explicit"),
    (None, "environment", "saved", "environment"),
    (None, None, "saved", "saved"),
    (None, None, None, "system"),
])
def testPriorityAndNormalizedPathsDoNotCreateDirectories(tmp_path, explicit, environment, saved, source):
    env = {"EMO_RUNTIME_DATA_DIR": str(tmp_path / environment)} if environment else {}
    env["EMO_RUNTIME_LOG_DIR"] = "not-an-operator-override"
    resolution = resolveDataRoot(tmp_path / explicit if explicit else None, environment=env,
        saved=tmp_path / saved if saved else None, platformDefault=tmp_path / "system")
    assert resolution.source == source
    assert resolution.path == (tmp_path / source).resolve()
    assert resolution.ignoredVariables == ("EMO_RUNTIME_LOG_DIR",)
    assert resolution.overridden == (source in {"explicit", "environment"})
    assert not resolution.path.exists()


def testCliAndEnvironmentRelativePathsUseStartupCwd(tmp_path):
    assert resolveDataRoot("relative/../中文 空格", environment={}, cwd=tmp_path).path == tmp_path / "中文 空格"
    assert resolveDataRoot(environment={"EMO_RUNTIME_DATA_DIR": "relative"}, cwd=tmp_path).path == tmp_path / "relative"


@pytest.mark.parametrize("value", ["", "  ", "relative", "C:relative", "bad\x00path"])
def testInvalidPreferenceHasActionableError(value):
    with pytest.raises(ValueError):
        validateSavedDataRoot(value)


def testSavedDirectoryMayBeNewButNotAFileOrBelowAFile(tmp_path):
    assert validateSavedDataRoot(str(tmp_path / "中文 新目录")) == tmp_path / "中文 新目录"
    assert not (tmp_path / "中文 新目录").exists()
    tmp_path.joinpath("file").write_text("not a directory")
    for path in (tmp_path / "file", tmp_path / "file/child"):
        with pytest.raises(ValueError, match="文件"):
            validateSavedDataRoot(str(path))
    assert validateSavedDataRoot(str(tmp_path)) == tmp_path


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path contract")
def testWindowsDriveUncAndChinesePreferenceLexically(tmp_path, monkeypatch):
    # No connection to a real UNC host. Verify syntax without IO to a network share.
    monkeypatch.setattr(Path, "resolve", lambda path: path.absolute())
    monkeypatch.setattr(Path, "exists", lambda path: False)
    for raw in (r"D:\Runtime 目录\内部数据", r"\\synthetic-host\synthetic-share\Runtime 目录"):
        assert validateSavedDataRoot(raw) == Path(raw)
        assert resolveDataRoot(raw, environment={}).path == Path(raw)
    for raw in (r"D:\bad?name", r"D:\CON", r"D:\bad:name", "D:\\bad. "):
        with pytest.raises(ValueError):
            validateSavedDataRoot(raw)


def testCheckUsesSavedRootAndDoesNotInitializeOrWrite(tmp_path, monkeypatch, retainedQtApplication):
    from PySide2.QtCore import QSettings
    from emo_master.apps.operator_runtime import main as entry
    from emo_master.core.project import runtime_directory
    from tests.runtime.current_production_fixture import currentProductionProject
    from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY

    project, _document = currentProductionProject(tmp_path / "project", autoStart=True)
    prefs = QSettings(str(tmp_path / "check.ini"), QSettings.IniFormat)
    root = tmp_path / "saved target"
    prefs.setValue(DEFAULT_ROOT_KEY, str(root))
    prefs.sync()
    originalBytes = Path(prefs.fileName()).read_bytes()
    monkeypatch.delenv("EMO_RUNTIME_DATA_DIR")
    monkeypatch.setattr(entry, "QSettings", lambda *_args: prefs)
    monkeypatch.setattr(entry, "ProductionRuntime", lambda *_args, **_kwargs: pytest.fail("--check created Runtime"))
    captured = []
    prepare = runtime_directory.prepareRuntimeProject

    def checked(*args, **kwargs):
        captured.append(kwargs["protectedPaths"])
        return prepare(*args, **kwargs)

    monkeypatch.setattr(runtime_directory, "prepareRuntimeProject", checked)
    assert entry.main([str(project), "--check"]) == 0
    assert captured[-1] == (root / "runtime.sqlite3", root / "logs")
    env = tmp_path / "environment"
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(env))
    assert entry.main([str(project), "--check"]) == 0
    assert captured[-1] == (env / "runtime.sqlite3", env / "logs")
    assert entry.main([str(project), "--check", "--data-root", "relative-cli"]) == 0
    assert captured[-1] == ((Path.cwd() / "relative-cli/runtime.sqlite3").resolve(), (Path.cwd() / "relative-cli/logs").resolve())
    assert not root.exists() and not env.exists() and not (project.parent / "outputs/result.png").exists()
    assert Path(prefs.fileName()).read_bytes() == originalBytes
