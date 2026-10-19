from copy import deepcopy
import json

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.store import ConfigurationStore
from tests.runtime.test_device_runtime import simulatedProject


def testCopyFailurePreservesCommitAndRestartReconstructs(tmp_path, monkeypatch):
    from emo_master.core.project import delivery_store
    sqlite = SqliteStore(tmp_path / 'runtime.db')
    path = tmp_path / 'online-project.json'
    store = ConfigurationStore(sqlite, exportPath=path)
    store.initialize(simulatedProject(), str(tmp_path / 'project.emoproj'))
    initial = json.loads(path.read_text(encoding='utf-8'))
    assert initial == store.snapshot()
    realWrite = delivery_store.atomicJson
    def fail(*_args):
        raise OSError('simulated export-only storage failure')
    monkeypatch.setattr(delivery_store, 'atomicJson', fail)
    payload = deepcopy(initial['payload'])
    payload['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
    result = store.commit(1, payload, 'request', 'fingerprint', 'runtime')
    assert result['state'] == 'COMMITTED' and result['documentRevision'] == 2 and result['exportError']
    assert store.receipt('request')[1]['state'] == 'COMMITTED'
    assert json.loads(path.read_text(encoding='utf-8')) == initial
    monkeypatch.setattr(delivery_store, 'atomicJson', realWrite)
    restored = ConfigurationStore(sqlite, exportPath=path)
    assert not restored.exportError
    assert json.loads(path.read_text(encoding='utf-8')) == restored.snapshot()
    assert restored.snapshot()['payload'] == payload
