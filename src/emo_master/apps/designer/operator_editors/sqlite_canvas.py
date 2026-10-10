"""Small adapters from the shared draft to SQLite editors and canvas hints."""
from copy import deepcopy

from emo_master.apps.designer.operator_editors.sqlite_sources import sourceCatalog, validateDraftMappings
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID


def draftForEditor(window, key):
    if key.projectId != window._currentProjectId():
        raise ValueError('编辑器所属项目已关闭')
    store = deepcopy(window.workflowStore)
    store.captureActiveGraph(window.flowModel.toProjectGraph(), window.flowScene.getNodePositions())
    payload = store.toPayload()
    payload['project'] = deepcopy(store.project)
    workflow = payload['workflows'].get(key.workflowId)
    if workflow is None or not any(n['nodeId'] == key.nodeId for n in workflow['nodes']):
        raise ValueError('编辑器所属流程或节点已删除')
    sources = sourceCatalog(payload, key.workflowId, window.operatorCatalogController.getCatalog())
    return {'sources': sources, 'workflow': workflow,
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
        draft = draftForEditor(window, EditorKey(window._currentProjectId(), window.activeWorkflowId, writers[0].nodeId))
    except (ValueError, KeyError) as draftError:
        for node in writers:
            scene.setSqliteHints(node.nodeId, '来源目录不可用', str(draftError))
        scene.setBindingDependencies(*dependencies)
        return
    for node in writers:
        error = ''
        try:
            validateDraftMappings(node.params, draft['sources'], draft['workflow'], node.nodeId)
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
