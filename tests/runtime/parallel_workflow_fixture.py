"""Ordinary While workflows over a synthetic image, with paced test operators."""
from copy import deepcopy

from emo_master.core.project.models import ProjectDocument
from tests.runtime.two_station_fixture import twoStationProject
from tests.runtime.production_fixture import saveDocument


def whileProject(root, count=2, limit=4):
    raw = twoStationProject(root).model_dump()
    raw['production']['mode'] = 'single'
    raw['runtime']['maxConcurrentJobs'] = limit
    raw['globalVariables']['keep-running'] = dict(name='循环使能', type='boolean',
        initialValue=True, kind='constant', lifetime='job')
    for index in range(2, count):
        wid = f'flow{index + 1}'
        raw['workflows'][wid] = deepcopy(raw['workflows']['main'])
        raw['workflowOrder'].append(wid)
        raw['presentation']['resultScopes'][wid] = dict(entryWorkflowId=wid, scopeWorkflowId=wid)
        raw['presentation']['pages'][wid] = deepcopy(raw['presentation']['pages']['main'])
        raw['presentation']['pageOrder'].append(wid)
        for key in ('count', 'image'):
            raw['presentation']['dataSources'][f'{key}-{wid}'] = dict(raw['presentation']['dataSources'][key],
                workflowId=wid, resultScopeId=wid)
        for component in raw['presentation']['pages'][wid]['components']:
            component['componentId'] += '-' + wid
            component['bindings'] = {key: value + '-' + wid for key, value in component['bindings'].items()}
        binding = deepcopy(raw['resources']['parameterBindings'][0])
        binding['target']['workflowId'] = wid
        raw['resources']['parameterBindings'].append(binding)
    roots = []
    for wid in list(raw['workflowOrder']):
        body = raw['workflows'][wid]
        body['nodes'].append(dict(nodeId='pace', operatorId='test.p2.pace'))
        next(node for node in body['nodes'] if node['nodeId'] == 'load')['params']['imagePath'] = str(root / 'input.png')
        entry = wid + '-run'
        roots.append(entry)
        raw['workflows'][entry] = dict(name=f'{wid} · 持续运行入口', nodes=[
            dict(nodeId='input', kind='workflow_input'),
            dict(nodeId='loop', kind='loop', loop=dict(mode='while', contractVersion=2,
                bodyWorkflowId=wid, conditionMode='globalVariable', conditionVariableId='keep-running',
                unlimited=True, maxIterations=2, timeoutMs=0)),
            dict(nodeId='output', kind='workflow_output')])
        raw['workflowOrder'].append(entry)
        path = [dict(nodeId='loop', relation='loop_body')]
        for scope in raw['presentation']['resultScopes'].values():
            if scope['scopeWorkflowId'] == wid:
                scope.update(entryWorkflowId=entry, callPath=path)
        for source in raw['presentation']['dataSources'].values():
            if source.get('workflowId') == wid:
                source['callPath'] = path
    raw['entryWorkflowId'] = roots[0]
    document = ProjectDocument.model_validate(raw)
    saveDocument(root, document)
    return document, roots
