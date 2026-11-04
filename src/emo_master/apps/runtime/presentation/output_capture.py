"""Trusted discovery and normal capture construction, independent of any editor."""
import json

from emo_panel_protocol.models import CallStep, DataSource, ResultScope
from emo_panel_protocol.output_requests import stableOutputIds
from emo_master.core.presentation.catalog import buildOutputCatalog, presentationType, resolveCallPath, sourceType
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields
from .normal_capture import freezeNormalCapture, freezeOutputCapture


def outputCatalog(document, registry):
    entries = buildOutputCatalog(document, {key: value.manifest for key, value in registry.items()})
    choices = []

    def visit(root, workflowId, path, ancestors):
        if workflowId in ancestors or len(path) > 32:
            return
        scope = ResultScope(entryWorkflowId=root, scopeWorkflowId=workflowId, callPath=path)
        for entry in entries:
            if entry.workflowId != workflowId:
                continue
            try:
                kind = presentationType(entry.spec)
            except ValueError:
                continue
            for field, valueType in [((), kind), *entry.fields.items()]:
                source = DataSource(kind='node_output' if entry.nodeId else 'workflow_output',
                    resultScopeId='scope', workflowId=workflowId, nodeId=entry.nodeId,
                    port=entry.port, fieldPath=list(field), callPath=path, expectedType=valueType)
                sourceId, scopeId, source = stableOutputIds(source, scope)
                location = ' / '.join(s.nodeId + ':' + s.relation for s in path) or '入口结束'
                choices.append(dict(sourceId=sourceId, scopeId=scopeId, source=source.model_dump(), scope=scope.model_dump(),
                    title=f'{document.workflows[root].name} / {document.workflows[workflowId].name} / '
                          f'{entry.displayName} [{entry.nodeId or "出口"}] / {entry.port}'
                          + ('.' + '.'.join(field) if field else '') + f' ({valueType}) · {location}',
                    hint=entry.hint, available=not entry.issues,
                    reason='; '.join(issue.message for issue in entry.issues)))
        for node in document.workflows[workflowId].nodes:
            calls = []
            if node.kind == 'subflow':
                calls.append(('subflow', node.targetWorkflowId))
            elif node.kind == 'loop':
                calls.extend(('loop_body' if field == 'bodyWorkflowId' else 'loop_condition', node.loop.get(field))
                             for field in loopWorkflowReferenceFields(node.loop))
            for relation, target in calls:
                if target in document.workflows:
                    visit(root, target, [*path, CallStep(nodeId=node.nodeId, relation=relation)], {*ancestors, workflowId})

    for root in document.workflowOrder:
        visit(root, root, [], set())
    return choices


def requestDiagnostics(document, registry, config):
    entries = buildOutputCatalog(document, {key: value.manifest for key, value in registry.items()})
    diagnostics = []
    invalidScopes = set()
    for key, scope in config.scopes.items():
        try:
            if resolveCallPath(document, scope.entryWorkflowId, scope.callPath) != scope.scopeWorkflowId:
                raise ValueError('scope workflow does not match call path')
        except ValueError as error:
            invalidScopes.add(key)
            diagnostics.append(dict(scopeId=key, code='SCOPE_UNAVAILABLE', message=str(error)))
    for key, source in config.sources.items():
        try:
            scope = config.scopes[source.resultScopeId]
            if source.resultScopeId in invalidScopes:
                raise ValueError('result scope is unavailable')
            if source.workflowId != scope.scopeWorkflowId or source.callPath != scope.callPath:
                raise ValueError('output must belong to its explicit invocation scope')
            if resolveCallPath(document, scope.entryWorkflowId, source.callPath) != source.workflowId:
                raise ValueError('output workflow does not match call path')
            actual = sourceType(source, entries, document.globalVariables)
            if actual != source.expectedType and (actual, source.expectedType) != ('integer', 'number'):
                raise ValueError(f'expected {source.expectedType}, output is {actual}')
        except ValueError as error:
            diagnostics.append(dict(sourceId=key, scopeId=source.resultScopeId, code='SOURCE_UNAVAILABLE', message=str(error)))
    return diagnostics


def freezeMergedCapture(document, registry, resourceRoot, workflowId, config, *, qt=False, **kwargs):
    """All normal launchers freeze the same saved external requests server-side."""
    sources, scopes, unavailable = {}, {}, {}
    if qt:
        frozen = freezeNormalCapture(document, registry, resourceRoot, workflowId, **kwargs)
        plan = json.loads(frozen.sourceJson)
        sources.update(plan['sources'])
        scopes.update(plan['scopes'])
    diagnostics = requestDiagnostics(document, registry, config)
    invalidScopes = {d['scopeId'] for d in diagnostics if d['code'] == 'SCOPE_UNAVAILABLE'}
    externalScopes = {key: scope.model_dump() for key, scope in config.scopes.items()
                      if scope.entryWorkflowId == workflowId and key not in invalidScopes}
    externalSources = {key: source.model_dump() for key, source in config.sources.items()
                       if source.resultScopeId in externalScopes}
    for target, extra in ((scopes, externalScopes), (sources, externalSources)):
        for key, value in extra.items():
            if key in target and target[key] != value:
                raise ValueError(f'capture identity conflict: {key}')
            target[key] = value
    unavailable.update({d['sourceId']: d['message'] for d in diagnostics
                        if d.get('sourceId') in externalSources})
    if not scopes:
        return None
    return freezeOutputCapture(document, registry, workflowId, sources, scopes, unavailable=unavailable)
