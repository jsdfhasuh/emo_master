"""Read-only draft source catalog and validation, sharing compiler port rules."""
from copy import deepcopy
from types import SimpleNamespace

from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, parseConfig
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


def sourceCatalog(payload, workflowId, catalog):
    document = ProjectDocument.model_validate(payload)
    registry = {d['operatorId']: SimpleNamespace(
        inputPorts=d.get('inputPortSpecs', d.get('inputPorts', {})),
        outputPorts=d.get('outputPortSpecs', d.get('outputPorts', {}))) for d in catalog}
    compiler = WorkflowCompiler(registry)
    result = []
    for node in document.workflows[workflowId].nodes:
        try:
            if node.kind == 'operator' and node.operatorId not in registry:
                raise ValueError('正式算子定义尚未加载或不可用，不能使用旧保存端口')
            if node.kind == 'subflow' and node.targetWorkflowId not in document.workflows:
                raise ValueError('目标流程已删除或不可用')
            _, ports = compiler.nodePortDefinitions(document, workflowId, node)
        except (ValueError, KeyError) as error:
            result.append({'nodeId': node.nodeId, 'name': node.displayName or node.nodeId,
                           'ports': {}, 'error': str(error), 'operatorId': node.operatorId})
            continue
        result.append({'nodeId': node.nodeId, 'name': node.displayName or node.nodeId,
                       'ports': ports, 'operatorId': node.operatorId, 'error': ''})
    return deepcopy(result)


def validateDraftMappings(config, sources, workflow, nodeId):
    """Validate sources and cycles locally; do not run or read result caches."""
    from emo_master.core.contracts.sqlite_writer import acceptsSource, SqliteWriterError
    config = parseConfig(config)
    nodes = {n['nodeId']: n for n in sources}
    edges = [(e['fromNode'], e['toNode']) for e in workflow['edges']]
    for node in workflow['nodes']:
        if node.get('operatorId') != OPERATOR_ID or node['nodeId'] == nodeId:
            continue
        rawRows = node.get('params', {}).get('mappings', [])
        for row in rawRows if isinstance(rawRows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get('source'), dict):
                continue
            source = row.get('source', {})
            if source.get('kind') == 'node_output':
                edges.append((source.get('nodeId'), node['nodeId']))
    for index, row in enumerate(config['mappings']):
        source = row['source']
        if source['kind'] != 'node_output':
            continue
        node = nodes.get(source['nodeId'])
        if node is None:
            raise SqliteWriterError('E_BINDING_NODE', '来源已删除或不在同一流程', index)
        if source['port'] not in node['ports']:
            raise SqliteWriterError('E_BINDING_PORT', '正式输出端口不存在', index)
        if not acceptsSource(node['ports'][source['port']], row['storageType']):
            raise SqliteWriterError('E_BINDING_TYPE', f"{normalizePortType(node['ports'][source['port']])} 与 {row['storageType']} 不兼容", index)
        if row['storageType'] == 'FILE_REFERENCE' and node['operatorId'] != 'vision.io.image_saver':
            raise SqliteWriterError('E_BINDING_TYPE', '文件引用仅支持 Image Saver 的持久输出', index)
        edges.append((source['nodeId'], nodeId))
    adjacency = {key: [] for key in nodes}
    for start, end in edges:
        if start in adjacency and end in adjacency:
            adjacency[start].append(end)
    active, visited = set(), set()
    def visit(key):
        if key in active:
            raise SqliteWriterError('E_WORKFLOW_CYCLE', '映射与流程连线形成循环依赖')
        if key in visited:
            return
        active.add(key)
        for end in adjacency[key]:
            visit(end)
        active.remove(key)
        visited.add(key)
    for key in adjacency:
        visit(key)
    return config
