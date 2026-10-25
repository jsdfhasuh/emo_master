"""Simulated shared page; no camera connection or external model."""
from examples.dispatch_queue_demo import demoProject
from emo_master.core.project.models import ProjectDocument


def sharedPageProject(root):
    payload = demoProject(root).model_dump(mode='json')
    dispatch = next(node for node in payload['workflows']['capture']['nodes'] if node['kind'] == 'dispatch')
    dispatch['params']['queueFullPolicy'] = 'skip'
    presentation = payload['presentation']
    path = [dict(nodeId='loop', relation='loop_body')]
    presentation['resultScopes']['acquisition'] = dict(entryWorkflowId='main', scopeWorkflowId='capture', callPath=path)
    presentation['dataSources']['live'] = dict(kind='node_output', resultScopeId='acquisition',
        workflowId='capture', callPath=path, nodeId='flip', port='image', expectedType='image')
    page = presentation['pages']['consumer']
    page['layout'] = dict(columns=2)
    page['components'].append(dict(componentId='live', type='image', bindings={'image': 'live'},
                                   layout=dict(row=1, column=1)))
    return ProjectDocument.model_validate(payload)
