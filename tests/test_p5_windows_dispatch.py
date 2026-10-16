import sys
from types import ModuleType

import pytest

from emo_master.apps import windows_entry


@pytest.mark.parametrize('flag,module', [('--runtime', 'emo_master.apps.runtime.release_host'),
    ('--operator-view', 'emo_master.apps.operator_view.main')])
def testExplicitSourceDispatchFreezesBeforeEntry(monkeypatch, flag, module):
    events = []
    fake = ModuleType(module)
    fake.main = lambda args: events.append(args) or 0
    monkeypatch.setitem(sys.modules, module, fake)
    monkeypatch.setattr(windows_entry.multiprocessing, 'freeze_support', lambda: events.append('freeze'))
    assert windows_entry.main([flag, '--ready-file', 'test.json']) == 0
    assert events == ['freeze', ['--ready-file', 'test.json']]
