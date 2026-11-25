"""Prepare the files referenced by the selected configuration changes."""
from copy import deepcopy

from emo_master.core.project.configuration import projectChanges, selectedCandidate
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.node_input_files import (
    NODE_INPUT_FILES, alignNodeInputFiles, nodeInputPathKey, nodeInputResource,
)


def filePublicationPayload(payload, root, *, only=None, bestEffort=False, refreshExisting=False):
    """Preserve the draft's exact representation outside its input-file fields."""
    if not payload.get('resources') or not any(
            node.get('operatorId') in NODE_INPUT_FILES
            for flow in payload.get('workflows', {}).values() for node in flow['nodes']):
        return payload
    try:
        document = ProjectDocument.model_validate(payload)
    except ValueError:
        if bestEffort:
            return payload  # Partial comparison may include unselected invalid draft changes.
        raise
    aligned = alignNodeInputFiles(document, root, only=only, bestEffort=bestEffort, refreshExisting=refreshExisting)
    result = deepcopy(payload)
    plan = aligned.resources.model_dump(mode='json')
    result['resources'].update(items=plan['items'], parameterBindings=plan['parameterBindings'])
    for wid, workflow in aligned.workflows.items():
        for row, node in zip(result['workflows'][wid]['nodes'], workflow.nodes):
            field = NODE_INPUT_FILES.get(node.operatorId)
            if field and field in node.params:
                row['params'][field] = node.params[field]
    return result


def _videoPathConnections(flow):
    return {edge['toNode'] for edge in flow.get('edges', ()) if edge.get('toPort') == 'videoPath'}


def comparisonFilePayload(online, local, root, *, refreshExisting=False):
    """Compare managed file content while keeping an unchanged unmanaged local project runnable."""
    if not online or online['project']['projectId'] != local['project']['projectId']:
        return filePublicationPayload(local, root, bestEffort=True, refreshExisting=refreshExisting)
    plans = [payload.get('resources') or {} for payload in (local, online)]
    managed = {nodeInputPathKey(item['path']) for plan in plans for item in plan.get('items', {}).values()}
    bound = {(t.get('workflowId'), t.get('nodeId'), tuple(t.get('parameterPath', ())))
             for plan in plans for binding in plan.get('parameterBindings', ()) if (t := binding.get('target'))}
    previous = {(wid, row['nodeId']): row for wid, flow in online['workflows'].items() for row in flow['nodes']}
    selected = set()
    for wid, flow in local['workflows'].items():
        changedPorts = _videoPathConnections(flow) ^ _videoPathConnections(online['workflows'].get(wid, {}))
        for row in flow['nodes']:
            field = NODE_INPUT_FILES.get(row.get('operatorId'))
            if not field:
                continue
            key = (wid, row['nodeId'])
            path = row.get('params', {}).get(field)
            old = previous.get(key, {})
            if (nodeInputPathKey(path, root) in managed or (*key, (field,)) in bound
                    or (field == 'videoPath' and row['nodeId'] in changedPorts)
                    or old.get('operatorId') != row['operatorId'] or old.get('params', {}).get(field) != path):
                selected.add(key)
    return (filePublicationPayload(local, root, only=selected, bestEffort=True, refreshExisting=refreshExisting)
            if selected else local)


def selectedFilePublication(online, local, paths, root):
    candidate = selectedCandidate(online, local, paths) if paths else deepcopy(online)
    changes = projectChanges(online, candidate)
    parts = {change['parts'] for change in changes}
    selected = set()
    localItems = {nodeInputPathKey(item['path']): (key, item)
                  for key, item in (local.get('resources') or {}).get('items', {}).items()}
    oldBindings = (online.get('resources') or {}).get('parameterBindings', ())
    remainingBindings = (candidate.get('resources') or {}).get('parameterBindings', ())
    for wid, flow in candidate.get('workflows', {}).items():
        changedPorts = _videoPathConnections(flow) ^ _videoPathConnections(online['workflows'].get(wid, {}))
        for node in flow['nodes']:
            field = NODE_INPUT_FILES.get(node.get('operatorId'))
            if not field:
                continue
            nid = node['nodeId']
            target = ('workflows', wid, 'nodes', nid, 'params', field)
            item = localItems.get(nodeInputPathKey(node.get('params', {}).get(field), root))
            pathSelected = (any(target[:len(path)] == path for path in parts)
                            or ('workflows', wid, 'nodes', nid, 'operatorId') in parts
                            or (field == 'videoPath' and nid in changedPorts))
            fileSelected = item and any(path[:3] == ('resources', 'items', item[0]) for path in parts)
            wasBound = ('resources', 'parameterBindings') in parts and any(
                binding not in remainingBindings and binding['target'] == dict(workflowId=wid, nodeId=nid, parameterPath=[field])
                for binding in oldBindings)
            if pathSelected or fileSelected or wasBound:
                selected.add((wid, nid))
                if item:
                    descriptor = candidate['resources']['items'].setdefault(item[0], deepcopy(item[1]))
                    descriptor.update(sha256=item[1]['sha256'], size=item[1]['size'])
    if selected:
        candidate = filePublicationPayload(candidate, root, only=selected)
    return candidate, [change['path'] for change in projectChanges(online, candidate)]


def stageNodeFiles(client, snapshot, candidate, root, cancelled):
    document = ProjectDocument.model_validate(candidate)
    resourceIds = {match[0] for flow in document.workflows.values() for node in flow.nodes
                   if (match := nodeInputResource(document, flow, node))}
    if not snapshot.get('importId') and snapshot['payload']['project']['projectId'] == document.project.projectId:
        online = (snapshot['payload'].get('resources') or {}).get('items', {})
        resourceIds = {key for key in resourceIds if online.get(key) != candidate['resources']['items'][key]}
    if resourceIds:
        result = client.uploadProjectResources(snapshot, candidate, root, cancelled, resourceIds=resourceIds)
        if result.get('aborted') or result['errors'] or result['pending'] or result['cancelled']:
            from emo_master.apps.designer.services.runtime_client import RuntimeClientError
            error = RuntimeClientError('E_RESOURCE_UPLOAD', '节点输入文件传输未完成')
            error.resourceResult = result
            raise error
    if cancelled is not None and cancelled.is_set():
        raise ValueError('已取消节点输入文件传输')
