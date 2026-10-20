import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.operator_editors.ui_loader import EditorAssetCache, UiLoadError
from emo_master.qt_environment import prepareQtEnvironment


def testDefaultCacheFallsBackPrivatelyWhenStandardCacheCannotBeWritten(monkeypatch, tmp_path):
    monkeypatch.delenv('EMO_DESIGNER_CACHE_DIR', raising=False)
    cache = EditorAssetCache()
    store = cache._store
    attempted = []
    def deniedOnce(content, sha):
        attempted.append(cache.root)
        if len(attempted) == 1:
            raise PermissionError('read-only home/cache')
        return store(content, sha)
    monkeypatch.setattr(cache, '_store', deniedOnce)
    raw = b'<ui version="4.0"></ui>'
    result = cache.store(raw, hashlib.sha256(raw).hexdigest())
    assert result.read_bytes() == raw and result.parent != attempted[0]
    assert cache._temporary is not None
    assert cache.store(raw, hashlib.sha256(raw).hexdigest()) == result


def testExplicitCacheFailureIsVisibleAndDoesNotSilentlyChangeConfiguredPath(tmp_path):
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('keep')
    raw = b'<ui/>'
    with pytest.raises(UiLoadError, match='缓存不可写'):
        EditorAssetCache(blocked).store(raw, hashlib.sha256(raw).hexdigest())
    assert blocked.read_text() == 'keep'


def testCacheDigestIsCheckedBeforeFallback(tmp_path):
    with pytest.raises(UiLoadError, match='SHA-256'):
        EditorAssetCache(tmp_path).store(b'bad', '0' * 64)
    assert not list(tmp_path.iterdir())


def testQtCleanupOnlyRemovesOpenCvPrivateEnvironment(monkeypatch, tmp_path):
    import sys
    import emo_master.qt_environment as environment
    fake = tmp_path / 'cv2'
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(__file__=str(fake / '__init__.py')))
    monkeypatch.setattr(environment.sys, 'platform', 'linux')
    monkeypatch.setenv('QT_QPA_PLATFORM_PLUGIN_PATH', str(fake / 'qt' / 'plugins'))
    monkeypatch.setenv('QT_QPA_FONTDIR', str(fake / 'qt' / 'fonts'))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    prepareQtEnvironment()
    assert 'QT_QPA_PLATFORM_PLUGIN_PATH' not in os.environ and 'QT_QPA_FONTDIR' not in os.environ
    assert os.environ['QT_QPA_PLATFORM'] == 'offscreen'
    custom = str(Path(tmp_path) / 'user-qt' / 'plugins')
    monkeypatch.setenv('QT_QPA_PLATFORM_PLUGIN_PATH', custom)
    prepareQtEnvironment()
    assert os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] == custom
