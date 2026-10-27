"""Current-format, device-free loop fixture for the live node-result contract."""
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from tests.runtime.inspection_test_operator import InspectionTestOperator


def loopProject(root):
    root.mkdir(parents=True, exist_ok=True)
    payload = WorkflowStore().toPayload('Live Node Results')
    payload['workflows'] = {
        'main': {'name': 'Loop and Hold', 'inputs': {'items': 'list<integer>'}, 'nodes': [
            {'nodeId': 'input', 'kind': 'workflow_input'},
            {'nodeId': 'loop', 'kind': 'loop', 'inputPorts': {'items': 'list<integer>'},
             'outputPorts': {'count': 'list<integer>'}, 'loop': {'contractVersion': 2,
                'mode': 'foreach', 'bodyWorkflowId': 'body', 'itemInputPort': 'index', 'maxIterations': 100}},
            {'nodeId': 'gate', 'operatorId': 'test.inspection', 'params': {'block': True, 'cooperative': True}},
            {'nodeId': 'output', 'kind': 'workflow_output'}], 'edges': [
            {'fromNode': 'input', 'fromPort': 'items', 'toNode': 'loop', 'toPort': 'items'},
            {'fromNode': 'loop', 'fromPort': 'count', 'toNode': 'gate', 'toPort': 'indices'}]},
        'body': {'name': 'Images and Count', 'inputs': {'index': 'integer'}, 'outputs': {'count': 'integer'},
            'nodes': [{'nodeId': 'input', 'kind': 'workflow_input'},
                {'nodeId': 'image', 'operatorId': 'test.inspection', 'displayName': 'Loop Image',
                 'params': {'withCount': True}},
                {'nodeId': 'save', 'operatorId': 'vision.io.image_saver',
                 'params': {'outputPath': str(root / 'result.png')}},
                {'nodeId': 'output', 'kind': 'workflow_output'}], 'edges': [
                {'fromNode': 'input', 'fromPort': 'index', 'toNode': 'image', 'toPort': 'index'},
                {'fromNode': 'image', 'fromPort': 'image', 'toNode': 'save', 'toPort': 'image'},
                {'fromNode': 'image', 'fromPort': 'count', 'toNode': 'output', 'toPort': 'count'}]}}
    payload['workflowOrder'] = ['main', 'body']
    registry = {operator.meta.operatorId: operator.meta for operator in (InspectionTestOperator, ImageSaverOperator)}
    payload['dependencies']['operators'] = [{'operatorId': meta.operatorId, 'version': meta.version}
                                            for meta in registry.values()]
    for workflow in payload['workflows'].values():
        workflow['layout'] = {'nodePositions': {node['nodeId']: {'x': index * 340., 'y': 80.}
                                               for index, node in enumerate(workflow['nodes'])}}
        for node in workflow['nodes']:
            if node.get('operatorId') in registry:
                meta = registry[node['operatorId']]
                node['inputPorts'] = {name: normalizePortType(spec) for name, spec in meta.inputPorts.items()}
                node['outputPorts'] = {name: normalizePortType(spec) for name, spec in meta.outputPorts.items()}
                node['paramSchema'] = meta.paramSchema
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == '2.7' and document.devices.definitions == {}
    return document


def autonomousLoopProject(root):
    from emo_master.core.project.models import WorkflowNode, WorkflowEdge
    document = loopProject(root)
    main = document.workflows['main']
    main.inputs.clear()
    main.edges = [edge for edge in main.edges if edge.fromNode != 'input']
    source = next(node for node in document.workflows['body'].nodes if node.nodeId == 'image').model_copy(deep=True)
    source.nodeId, source.displayName = 'source', 'Sequence'
    source.params = {'withItems': True, 'items': [5, 2, 3] * 10}
    main.nodes.insert(1, WorkflowNode.model_validate(source.model_dump()))
    main.layout.nodePositions = {node.nodeId: {'x': index * 340., 'y': 80.}
                                 for index, node in enumerate(main.nodes)}
    main.edges.append(WorkflowEdge(fromNode='source', fromPort='items', toNode='loop', toPort='items'))
    next(node for node in document.workflows['body'].nodes if node.nodeId == 'image').params['delayMs'] = 80
    return ProjectDocument.model_validate(document.model_dump())
