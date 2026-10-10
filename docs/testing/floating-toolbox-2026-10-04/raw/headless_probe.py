"""Exercise the unchanged non-Qt bubble branch by blocking Qt imports."""
import importlib.abc
import sys

import emo_master  # noqa: F401 - preload normal Windows dependencies


class WithoutQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('PySide2'):
            raise ImportError('controlled unavailable-Qt probe')


sys.meta_path.insert(0, WithoutQt())
from emo_master.apps.designer.ui.operator_bubble import OperatorBubble  # noqa: E402

bubble = OperatorBubble()
bubble.setOperators([{'operatorId': 'vision.resize', 'displayName': 'Resize'},
                     {'operatorId': 'system.repeat', 'displayName': 'Repeat', 'systemNodeKind': 'loop:repeat'}])
assert bubble.getVisibleOperatorIds() == ['system.repeat', 'vision.resize']
bubble.setRecentOperatorIds(['vision.resize'])
assert bubble.getVisibleOperatorIds() == ['vision.resize', 'system.repeat']
bubble.setSearchKeyword('repeat')
assert bubble.getVisibleOperatorIds() == ['system.repeat']
print('PASS: non-Qt fallback keeps legacy sorting, search and recent semantics')
