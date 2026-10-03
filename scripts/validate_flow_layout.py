"""Render the real Designer and measure graph layout without starting a Runtime.

Run from the repository with its Python 3.10 environment. Output is evidence,
not a runnable vision job: manifests/ports are real, inference is never invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['HUARAY_CAMERA_SMOKE'] = '0'

import emo_master  # noqa: E402,F401 - native DLL order
from PySide2 import __version__ as qtVersion  # noqa: E402
from PySide2.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowEdgeViewModel  # noqa: E402
from emo_master.apps.designer.ui.flow_layout import LayoutNode, flowPositions  # noqa: E402
from emo_master.apps.designer.ui.flow_routing import Rect, intersects  # noqa: E402
from emo_master.core.contracts.port_types import normalizePortType  # noqa: E402


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def populate(window):
    model = window.flowModel
    manifests = {}
    for key, folder in [('load', 'image_loader'), ('yolo', 'yolo_inference'), ('count', 'collection_count'),
                        ('resize', 'resize'), ('compare', 'number_compare'), ('save', 'image_saver')]:
        raw = json.loads((ROOT / 'src/emo_master/plugins/builtins' / folder / 'manifest.json').read_text(encoding='utf-8'))
        manifests[key] = raw
        node = model.addNode(raw['operatorId'], raw['operatorId'],
                            {p: normalizePortType(t) for p, t in raw['inputPorts'].items()},
                            {p: normalizePortType(t) for p, t in raw['outputPorts'].items()}, raw['paramSchema'])
        # Stable fixture identity makes before/after reproductions comparable.
        value = model.nodes.pop(node)
        value.nodeId = key
        model.nodes[key] = value
    for edge in [('load', 'image', 'yolo', 'image'), ('yolo', 'detections', 'count', 'detections'),
                 ('count', 'count', 'compare', 'left'), ('yolo', 'overlay', 'resize', 'image'),
                 ('yolo', 'frame', 'resize', 'frame'), ('resize', 'image', 'save', 'image')]:
        model.connectNodes(*edge)
    window.workflowController.refreshActiveWorkflow()
    scene = window.flowScene
    keys = list(manifests) + [key for key in model.nodes if model.nodes[key].kind != 'operator']
    # Reproduce the old insertion/grid order, including the backward count edge.
    for i, key in enumerate(keys):
        scene._nodeItems[key].setPos(20 + (i % 4) * 475, 20 + (i // 4) * 390)
    scene.refreshAllRoutes()
    window.pageCoordinator.sync()


def checkRoutes(scene):
    blocked = []
    for key, edge in scene._edgeItems.items():
        if edge.route.blocked:
            blocked.append(key)
            continue
        for nodeId, node in scene._nodeItems.items():
            if nodeId in (key[0], key[2]):
                continue
            rect = node.mapRectToScene(node.rect())
            obstacle = Rect(rect.left(), rect.top(), rect.right(), rect.bottom()).expanded(12)
            assert all(not intersects(a, b, obstacle) for a, b in zip(edge.route.points, edge.route.points[1:]))
    return blocked


def capture(window, app, path):
    window.focusGraphContent()
    deadline = perf_counter() + 3
    while window.flowView._pendingFit is not None and perf_counter() < deadline:
        app.processEvents()
    assert window.flowView._pendingFit is None, 'viewport did not settle'
    window.flowView.viewport().repaint()
    assert window.grab().save(str(path))


def benchmark(scene, app):
    scene.clearGraph()
    for i in range(40):
        scene.addFlowNode(FlowNodeViewModel(str(i), f'Synthetic {i} 长标题', 0, 0,
            {f'in{j}': 'number' for j in range(4)}, {f'out{j}': 'number' for j in range(1 + i % 4)}))
    edges = [(i, i + 4, 'in0') for i in range(36)] + [(i, i + 5, 'in1') for i in range(0, 35, 4)]
    for source, target, port in edges:
        scene.renderEdge(FlowEdgeViewModel(str(source), 'out0', str(target), port))
    nodes = {key: LayoutNode(item.geometry.width, item.geometry.height) for key, item in scene._nodeItems.items()}
    connections = [(key[0], key[2]) for key in scene._edgeItems]
    rows = []
    for iteration in range(6):
        start = perf_counter()
        positions = flowPositions(nodes, connections)
        laidOut = perf_counter()
        scene._applyPositions(positions)
        routed = perf_counter()
        view = scene.views()[0]
        view.fitContent(scene.getContentBounds())
        deadline = perf_counter() + 3
        while view._pendingFit is not None and perf_counter() < deadline:
            app.processEvents()
        assert view._pendingFit is None
        view.viewport().repaint()
        app.processEvents()
        painted = perf_counter()
        node = scene._nodeItems['18']
        node.grabMouse()
        moves = []
        for dy in [5, -5] * 10:
            before = perf_counter()
            node.moveBy(0, dy)
            app.processEvents()
            view.viewport().repaint()
            moves.append((perf_counter() - before) * 1000)
        node.ungrabMouse()
        before = perf_counter()
        scene.refreshAllRoutes()
        app.processEvents()
        view.viewport().repaint()
        rows.append({'iteration': iteration, 'warmup': iteration == 0,
                     'layoutMs': (laidOut - start) * 1000, 'applyAndRouteMs': (routed - laidOut) * 1000,
                     'eventPumpMs': (painted - routed) * 1000, 'connectedDragMs': moves,
                     'releaseRouteAndEventsMs': (perf_counter() - before) * 1000,
                     'blocked': checkRoutes(scene)})
    return {'nodes': 40, 'edges': len(edges), 'clock': 'time.perf_counter, milliseconds', 'runs': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new output directory (never overwrite earlier evidence)')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = [ROOT / path for path in git('ls-files', '-co', '--exclude-standard').splitlines()
             if path.endswith('.py') and ('flow_layout' in path or 'flow_routing' in path or 'flow_scene' in path or path.endswith('main_window.py'))]
    evidence = {'head': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain'),
                'python': sys.version, 'os': platform.platform(), 'pyside2': qtVersion,
                'platform': os.environ['QT_QPA_PLATFORM'],
                'sourceSha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    app = QApplication.instance() or QApplication([])
    from emo_master.apps.designer.main import applyDesignerStyle
    applyDesignerStyle(app)
    with TemporaryDirectory(prefix='emo-layout-') as data:
        os.environ['EMO_RUNTIME_DATA_DIR'] = data
        client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda path: SimpleNamespace(ok=True, message='ok'))
        settings = SimpleNamespace(value=lambda key, default=None: default, setValue=lambda *args: None)
        window = MainWindow(client, settingsStore=settings)
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.Discard
        try:
            window.resize(1920, 1080)
            window.show()
            app.processEvents()
            populate(window)
            window.appendRuntimeLog('INFO', '布局验证：真实算子端口，未启动 Runtime 或检测')
            before = window.flowScene.getNodePositions()
            capture(window, app, args.output / 'before.png')
            window.autoLayoutNodes()
            after = window.flowScene.getNodePositions()
            capture(window, app, args.output / 'after.png')
            assert after['compare'][0] > after['count'][0]
            assert not checkRoutes(window.flowScene)
            window.pageCoordinator.history()
            assert window.flowScene.getNodePositions() == before
            window.pageCoordinator.history(True)
            assert window.flowScene.getNodePositions() == after
            assert window.saveProjectToDirectory(str(args.output / 'project'))
            assert window.loadProjectDirectory(str(args.output / 'project'))
            assert window.flowScene.getNodePositions() == after
            evidence['sample'] = {'before': before, 'after': after, 'undoRedoSaveReopen': True,
                                  'blocked': checkRoutes(window.flowScene), 'runtimeStarted': False}
            evidence['benchmark'] = benchmark(window.flowScene, app)
            # Actual path hit testing at a different zoom uses the same Qt scene.
            window.flowView.setZoomFactor(0.75)
            app.processEvents()
            evidence['zoom'] = window.flowView.getZoomFactor()
            evidence['nodesPreserved'] = len(window.flowScene._nodeItems)
        finally:
            evidence['closeAccepted'] = window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        (args.output / 'validation.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()), 'head': evidence['head'], 'closeAccepted': evidence['closeAccepted']}))


if __name__ == '__main__':
    main()
