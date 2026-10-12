"""Portable, device-free navigation stress fixture, not a field project."""
from copy import deepcopy

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.core.project.migration import migrateProjectPayload
from tests.runtime.test_workflow_debug_control import nestedProject


def navigationPayload():
    payload = migrateProjectPayload(nestedProject(), enableGlobalVariables=True)
    keys = [f'sample-{index:02d}' for index in range(29)]
    for index, key in {0: 'station1-run', 1: 'station2-run', 3: 'station1-first',
                       4: 'station1-frame', 12: 'station2-frame', 28: 'child'}.items():
        keys[index] = key
    template = payload['workflows']['main']
    workflows = {key: deepcopy(template) for key in keys}
    for index, (key, workflow) in enumerate(workflows.items()):
        workflow['name'] = f'Sample {index:02d} - navigation and coordinate processing'
    workflows['child'] = deepcopy(payload['workflows']['child'])
    workflows['child']['inputs'] = {'value': 'number'}
    for station in (1, 2):
        root, body = f'station{station}-run', f'station{station}-frame'
        workflows[root] = dict(name=f'Station {station} - While entry', nodes=[
            dict(nodeId='input', kind='workflow_input'),
            dict(nodeId='loop', kind='loop', loop=dict(mode='while', contractVersion=2,
                 bodyWorkflowId=body, conditionMode='globalVariable', conditionVariableId='enabled',
                 unlimited=True, maxIterations=2, timeoutMs=0)),
            dict(nodeId='output', kind='workflow_output')])
        chain = workflows[body]
        chain['name'] = f'Station {station} - frame processing'
        chain['nodes'] = [dict(nodeId='input', kind='workflow_input'),
            dict(nodeId='number', operatorId='vision.value.number', params={'value': 7},
                 outputPorts={'value': 'number'})]
        chain['edges'] = []
        previous = 'number'
        for index in range(8):
            node = f'call-{index}'
            chain['nodes'].append(dict(nodeId=node, kind='subflow', targetWorkflowId='child'))
            chain['edges'].append(dict(fromNode=previous, fromPort='value', toNode=node, toPort='value'))
            previous = node
        chain['nodes'].append(dict(nodeId='output', kind='workflow_output'))
        chain['edges'].append(dict(fromNode=previous, fromPort='value', toNode='output', toPort='value'))
    payload.update(workflows=workflows, workflowOrder=keys, entryWorkflowId='station1-run')
    payload['runtime'].update(startupWorkflowIds=['station1-run', 'station2-run'], maxConcurrentJobs=2)
    payload['globalVariables']['enabled'] = dict(name='Enabled', type='boolean', initialValue=True,
                                               kind='constant', lifetime='job')
    return payload


def saveNavigationProject(directory):
    path = directory / 'navigation-sample.emoproj'
    saveProject(path, navigationPayload())
    return path
