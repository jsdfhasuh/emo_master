"""Read-only graph inspection shared by publication and history views."""
from html import escape

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import QGraphicsView

from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowEdgeViewModel
from emo_master.core.workflow.loop_contracts import interfacePortTypes


def nodeName(node):
    name = node.get('displayName') or node['nodeId']
    return {'Workflow Input': '流程输入', 'Workflow Output': '流程输出'}.get(name, name)


def eventName(kind):
    return {'node.started': '节点开始', 'node.completed': '节点完成', 'node.failed': '节点失败',
            'workflow.started': '流程开始', 'workflow.completed': '流程完成', 'workflow.failed': '流程失败',
            'job.started': '任务开始', 'job.completed': '任务完成', 'job.failed': '任务失败'}.get(kind, kind)


def nodeConnections(flow, nodeId):
    return sorted((e['fromNode'], e['fromPort'], e['toNode'], e['toPort'])
                  for e in (flow or {}).get('edges', []) if nodeId in (e['fromNode'], e['toNode']))


def changedNodes(flow, other):
    old = {node['nodeId']: node for node in (other or {}).get('nodes', [])}
    positions = (flow or {}).get('layout', {}).get('nodePositions', {})
    previousPositions = (other or {}).get('layout', {}).get('nodePositions', {})
    return {node['nodeId'] for node in (flow or {}).get('nodes', []) if node['nodeId'] in old and (
        node != old[node['nodeId']] or positions.get(node['nodeId']) != previousPositions.get(node['nodeId'])
        or nodeConnections(flow, node['nodeId']) != nodeConnections(other, node['nodeId']))}


class ReadOnlyFlowView(QGraphicsView):
    nodeClicked = Signal(str)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setInteractive(False)
        self.setAcceptDrops(False)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self._press = None

    def mousePressEvent(self, event):
        self._press = event.pos() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press is not None and (event.pos() - self._press).manhattanLength() < 5:
            item = self.itemAt(event.pos())
            while item is not None:
                if getattr(item, 'isPortVisibilityButton', False):
                    self.scene().showAllPorts(item.node.model.nodeId)
                    break
                model = getattr(item, 'model', None)
                if model is not None and hasattr(model, 'nodeId'):
                    self.nodeClicked.emit(model.nodeId)
                    break
                item = item.parentItem()
        self._press = None

    def zoom(self, factor):
        scale = max(.25, min(2.5, self.transform().m11() * factor))
        self.resetTransform()
        self.scale(scale, scale)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def fitGraph(self, *, readable=False):
        if not self.scene().items():
            return
        self.fitInView(self.scene().itemsBoundingRect().adjusted(-25, -25, 25, 25), Qt.KeepAspectRatio)
        # Keep long workflows readable; scrolling is preferable to tiny text.
        scale = max(.7 if readable else .05, min(1.0, self.transform().m11()))
        self.resetTransform()
        self.scale(scale, scale)

    def focusNode(self, nodeId):
        self.scene().setNodeSelected(nodeId)
        center = self.scene().getNodeCenter(nodeId)
        if center:
            self.centerOn(*center)


def drawGraph(scene, flow, ports, other=None, *, baseline=False):
    scene.clearGraph()
    if flow is None:
        return
    otherNodes = {node['nodeId']: node for node in (other or {}).get('nodes', [])}
    nodes = flow['nodes']
    for index, node in enumerate(nodes):
        position = flow.get('layout', {}).get('nodePositions', {}).get(node['nodeId'], {})
        nodePorts = ports.get(node['nodeId'], {})
        kind = node.get('kind', 'operator')
        inputs = interfacePortTypes(nodePorts.get('inputs', node.get('inputPorts', flow.get('outputs', {}) if kind == 'workflow_output' else {})))
        outputs = interfacePortTypes(nodePorts.get('outputs', node.get('outputPorts', flow.get('inputs', {}) if kind == 'workflow_input' else {})))
        for edge in flow.get('edges', []):
            if edge['fromNode'] == node['nodeId']:
                outputs.setdefault(edge['fromPort'], 'any')
            if edge['toNode'] == node['nodeId']:
                inputs.setdefault(edge['toPort'], 'any')
        scene.addFlowNode(FlowNodeViewModel(node['nodeId'], nodeName(node),
            float(position.get('x', index % 3 * 280)), float(position.get('y', index // 3 * 180)),
            inputs, outputs, node.get('operatorId') or '', kind))
    for edge in flow.get('edges', []):
        scene.renderEdge(FlowEdgeViewModel(edge['fromNode'], edge['fromPort'], edge['toNode'], edge['toPort']))
    if any(n['nodeId'] not in flow.get('layout', {}).get('nodePositions', {}) for n in nodes):
        scene.layoutNodesFlow()
    else:
        scene.refreshAllRoutes()
    if other is None:
        return
    modified = changedNodes(flow, other)
    for node in nodes:
        previous = otherNodes.get(node['nodeId'])
        if previous is not None and node['nodeId'] not in modified:
            continue
        color = '#b77900' if previous else '#b3261e' if baseline else '#16803c'
        scene.setNodeComparisonColor(node['nodeId'], color)


def displayValue(value):
    if value is None:
        return '未设置'
    if value is True:
        return '是'
    if value is False:
        return '否'
    if isinstance(value, dict):
        return '；'.join(str(k) + '：' + displayValue(v) for k, v in value.items()) or '无'
    if isinstance(value, list):
        return '、'.join(displayValue(v) for v in value) or '空列表'
    return str(value)


def nodeDetails(node, previous=None, event=None, *, comparing=False):
    if node is None and previous is None:
        return '<p>此流程没有节点。</p>'
    selected = node or previous
    html = '<h3>' + escape(nodeName(selected)) + '</h3>'
    if comparing:
        state = '仅在对照版本中存在' if node is None else '所选版本新增的节点' if previous is None else '节点配置已修改' if node != previous else '节点配置相同'
        html += '<p>' + state + '</p>'
    schema = selected.get('paramSchema', {}).get('properties', {})
    params, old = (node or {}).get('params', {}), (previous or {}).get('params', {})
    keys = list(dict.fromkeys([*params, *old]))
    html += '<h4>节点参数</h4>'
    if not keys:
        html += '<p>此节点没有可填写参数。</p>'
    else:
        html += '<table cellspacing="6"><tr><th>参数</th>' + ('<th>对照版本</th>' if comparing else '') + '<th>所选版本</th></tr>'
        unchanged = 0
        for key in keys:
            changed = comparing and (key not in params or key not in old or params[key] != old[key])
            if comparing and not changed:
                unchanged += 1
                continue
            html += '<tr' + (' bgcolor="#fff1c9"' if changed else '') + '><td>' + escape(schema.get(key, {}).get('title') or key) + '</td>'
            if comparing:
                html += '<td>' + escape(displayValue(old.get(key))) + '</td>'
            html += '<td>' + escape(displayValue(params.get(key))) + '</td></tr>'
        html += '</table>'
        if unchanged:
            html += f'<p>另有 {unchanged} 项参数未改变。</p>'
    for key, label in [('targetWorkflowId', '调用流程'), ('loop', '循环设置'), ('deviceBindings', '使用设备')]:
        value = selected.get(key)
        if value:
            html += '<p><b>' + label + '</b>：' + escape(displayValue(value)) + '</p>'
    html += '<h4>运行结果</h4>'
    if not event:
        html += '<p>当前查看配置，未选择运行记录。</p>'
    else:
        payload = event.get('payload', {})
        html += '<p>' + escape(eventName(event.get('eventType', '运行记录'))) + '</p>'
        for key, label in [('status', '状态'), ('message', '说明'), ('error', '错误'), ('outputs', '输出'), ('durationMs', '耗时（毫秒）')]:
            if key in payload:
                html += '<p><b>' + label + '</b>：' + escape(displayValue(payload[key])) + '</p>'
    return html
