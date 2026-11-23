"""Small adapters from the shared draft to SQLite editors and canvas hints."""
from copy import deepcopy

from emo_master.apps.designer.operator_editors.sqlite_sources import sourceCatalog, validateDraftMappings
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID
from emo_master.core.workflow.compiler import WorkflowPortContext


def _draftSources(window, key):
    if key.projectId != window._currentProjectId():
        raise ValueError('编辑器所属项目已关闭')
    store = window.workflowStore
    state = store.workflows.get(key.workflowId)
    if state is None:
        raise ValueError('编辑器所属流程或节点已删除')
    if key.workflowId == store.activeWorkflowId:
        graph = window.flowModel.toProjectGraph()
    else:
        graph = {'nodes': state.nodes, 'edges': state.edges}
    if not any(node['nodeId'] == key.nodeId for node in graph['nodes']):
        raise ValueError('编辑器所属流程或节点已删除')
    # Calls, loops and dispatch targets use other workflows' interfaces only.
    workflows = {workflowId: {'name': flow.name, 'inputs': flow.inputs, 'outputs': flow.outputs}
                 for workflowId, flow in store.workflows.items()}
    workflow = workflows[key.workflowId]
    workflow.update(graph)
    context = WorkflowPortContext(schemaVersion=store.schemaVersion, project=store.project, workflows=workflows,
                                  globalVariables=store.projectExtensions.get('globalVariables', {}))
    return sourceCatalog(context, key.workflowId, window.operatorCatalogController.getCatalog()), workflow


def draftForEditor(window, key):
    sources, workflow = _draftSources(window, key)
    if key.workflowId == window.workflowStore.activeWorkflowId:
        layout = {'nodePositions': {nodeId: {'x': x, 'y': y}
                                    for nodeId, (x, y) in window.flowScene.getNodePositions().items()}}
    else:
        layout = window.workflowStore.get(key.workflowId).layout
    return {'sources': sources, 'workflow': deepcopy({**workflow, 'layout': layout}),
            'directory': str(window.currentProjectDir or window.loadedProjectPath or '')}


def refreshCanvasHints(window):
    from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
    scene = window.flowScene
    if not hasattr(scene, 'setSqliteHints'):
        return
    writers = [n for n in window.flowModel.nodes.values() if n.operatorId == OPERATOR_ID]
    selected = window.flowModel.selectedNodeId
    dependencies = (None, ())
    if not writers:
        scene.setBindingDependencies(*dependencies)
        return
    try:
        sources, workflow = _draftSources(window, EditorKey(window._currentProjectId(), window.activeWorkflowId, writers[0].nodeId))
    except (ValueError, KeyError) as draftError:
        for node in writers:
            scene.setSqliteHints(node.nodeId, '来源目录不可用', str(draftError))
        scene.setBindingDependencies(*dependencies)
        return
    for node in writers:
        error = ''
        try:
            validateDraftMappings(node.params, sources, workflow, node.nodeId)
        except (ValueError, KeyError) as problem:
            error = str(problem)
        rows = node.params.get('mappings', [])
        rows = rows if isinstance(rows, list) else []
        summary = str(node.params.get('table', '未配置目标表')) + ' · ' + str(len(rows)) + ' 个映射'
        scene.setSqliteHints(node.nodeId, summary, error)
        if node.nodeId == selected:
            ids = [row.get('source', {}).get('nodeId') for row in rows
                   if isinstance(row, dict) and isinstance(row.get('source'), dict)
                   and row['source'].get('kind') == 'node_output']
            dependencies = (node.nodeId, ids)
    scene.setBindingDependencies(*dependencies)
