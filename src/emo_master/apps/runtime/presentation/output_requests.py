"""Small project-owned SQLite capture configuration and explicit RPC receipts."""
import json
import re

from emo_panel_protocol.canonical import canonicalJson, revisionOf
from emo_panel_protocol.output_requests import OutputRequestConfig
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.presentation.capture_limits import imageCaptureIdentity, normalCaptureProfile, outputCaptureLimits
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.workflow_view import presentationForWorkflow
from .output_capture import outputCatalog, requestDiagnostics


def captureQuotaDiagnostics(workflowId, workflowName, sources, scopes, externalSources, externalScopes, qtSources, qtScopes):
    """Explain merged demand with output addresses, without Web UI metadata.

    outputCaptureLimits remains the admission authority. Image aliases use its
    exact identity, so multiple consumers do not become extra physical lanes.
    """
    limits = normalCaptureProfile()
    groups = {}
    for sourceId, source in sorted(sources.items()):
        if source['expectedType'] == 'image':
            groups.setdefault(imageCaptureIdentity(source, scopes), []).append(sourceId)

    def origins(key, external, qt):
        return [label for label, values in (('external', external), ('qt', qt)) if key in values]

    checks = [('scopes', len(scopes), 'maxScopes', '结果作用域',
               'normal capture scope budget exceeded (16)'),
              ('sources', len(sources), 'maxSourceIds', '输出来源',
               'normal capture source budget exceeded (16)'),
              ('images', len(groups), 'maxImageSources', '不同图片输出',
               'normal capture supports at most two distinct image sources')]
    diagnostics = []
    for budget, count, limitKey, label, original in checks:
        if count <= limits[limitKey]:
            continue
        selected = {key for group in groups.values() for key in group} if budget == 'images' else set(sources)
        selectedScopes = {sources[key]['resultScopeId'] for key in selected} if budget == 'images' else set(scopes)
        details = [dict(sourceId=key, **sources[key], origins=origins(key, externalSources, qtSources))
                   for key in sorted(selected)]
        addresses = []
        for item in details:
            path = f"{item['workflowId']}/{item.get('nodeId') or '流程输出'}/{item['port']}"
            if item.get('fieldPath'):
                path += '/' + '/'.join(map(str, item['fieldPath']))
            if item.get('callPath'):
                path += f"（调用路径 {canonicalJson(item['callPath'])}）"
            owner = '+'.join('Qt' if value == 'qt' else '外部' for value in item['origins'])
            addresses.append(f"{owner} {item['sourceId']} → {path}")
        message = (f'工作流“{workflowName}”（{workflowId}）合并后的{label}为 {count}，上限 {limits[limitKey]}。'
                   'Qt 页面与外部请求共用此额度；等价图片按同一输出及调用作用域只计一路。'
                   '请核对所列绑定和 Qt 采集需求，调整后重新发布；未丢弃任何来源。'
                   f'来源：{"；".join(addresses) or "无输出来源"}。')
        if budget == 'scopes':
            message += f'结果作用域：{"、".join(sorted(scopes))}。'
        diagnostics.append(dict(code='CAPTURE_' + budget.upper() + '_LIMIT', workflowId=workflowId,
            workflowName=workflowName, budget=budget, count=count, limit=limits[limitKey],
            sourceIds=sorted(selected), scopeIds=sorted(selectedScopes), sources=details,
            scopes=[dict(scopeId=key, **scopes[key], origins=origins(key, externalScopes, qtScopes))
                    for key in sorted(selectedScopes)],
            imageGroups=[dict(sourceIds=group) for group in groups.values()] if budget == 'images' else [],
            message=message, reason=original))
    return diagnostics


class OutputRequestsStore:
    def __init__(self, sqlite):
        self.sqlite = sqlite
        with sqlite.connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS externalOutputRequests (
                projectId TEXT PRIMARY KEY, configRevision TEXT NOT NULL, config TEXT NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS externalOutputReceipts (
                requestId TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)''')

    def read(self, projectId):
        with self.sqlite.connection() as connection:
            row = connection.execute('SELECT config FROM externalOutputRequests WHERE projectId=?', (projectId,)).fetchone()
        return OutputRequestConfig.model_validate_json(row[0]) if row else OutputRequestConfig()

    def receipt(self, requestId):
        with self.sqlite.connection() as connection:
            row = connection.execute('SELECT fingerprint, receipt FROM externalOutputReceipts WHERE requestId=?', (requestId,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save(self, projectId, config, requestId, fingerprint, receipt):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            connection.execute('INSERT OR REPLACE INTO externalOutputRequests VALUES (?,?,?)',
                               (projectId, config.revision, canonicalJson(config.model_dump())))
            connection.execute('INSERT INTO externalOutputReceipts VALUES (?,?,?)',
                               (requestId, fingerprint, canonicalJson(receipt)))
            # Same authority and commit boundary for config and acknowledged receipt.


class OutputRequestsRpcMixin:
    def _outputStore(self):
        if not hasattr(self, 'outputRequestsStore'):
            self.outputRequestsStore = OutputRequestsStore(self.sqliteStore)
        return self.outputRequestsStore

    def _outputReply(self, ok=True, code='OK', message='', **state):
        return pb.OutputRequestsReply(ok=ok, code=code, message=message, state_json=canonicalJson(state))

    def _outputTarget(self, request, *, write=False):
        if (request.runtime_instance_id and request.runtime_instance_id != self.runtimeInstanceId) or (write and not request.runtime_instance_id):
            return 'RESET_REQUIRED', 'Runtime instance changed; query the target again'
        if self.loadedDocument is None or request.project_id != self.loadedDocument.project.projectId:
            return 'PROJECT_CHANGED', 'Target project is not the currently loaded project'
        if write:
            current = self.projectConfiguration.current
            if current is None or request.expected_document_revision != current['documentRevision']:
                return 'DOCUMENT_CONFLICT', 'Project revision changed; refresh and validate before publishing'
            if self._closing or self.projectConfiguration.closed or self.onlineRecoveryError:
                return 'NOT_READY', 'Runtime configuration is not ready'
        return None

    def _outputState(self):
        document = self.loadedDocument
        config = self._outputStore().read(document.project.projectId)
        return dict(runtimeInstanceId=self.runtimeInstanceId, projectId=document.project.projectId,
            projectName=document.project.name,
            documentRevision=str((self.projectConfiguration.current or {}).get('documentRevision', 0)),
            configRevision=config.revision, config=config.model_dump(),
            capabilities=normalCaptureProfile(),
            diagnostics=requestDiagnostics(document, self.pluginScanResult.activeOperators, config))

    def GetOutputCatalog(self, request, context):
        with self._projectStateLock:
            error = self._outputTarget(request)
            if error:
                return self._outputReply(False, *error, runtimeInstanceId=self.runtimeInstanceId)
            state = self._outputState()
            state.update(choices=outputCatalog(self.loadedDocument, self.pluginScanResult.activeOperators),
                workflows=[dict(workflowId=key, name=self.loadedDocument.workflows[key].name)
                           for key in self.loadedDocument.workflowOrder])
            return self._outputReply(**state)

    def GetOutputRequests(self, request, context):
        with self._projectStateLock:
            error = self._outputTarget(request)
            return self._outputReply(False, *error, runtimeInstanceId=self.runtimeInstanceId) if error else self._outputReply(**self._outputState())

    def _validateOutputRequest(self, request):
        config = OutputRequestConfig.model_validate_json(request.config_json)
        diagnostics = requestDiagnostics(self.loadedDocument, self.pluginScanResult.activeOperators, config)
        if diagnostics:
            return config, diagnostics
        # Publication checks the final demand alongside Qt for each possible Job.
        document = self.loadedDocument
        for workflowId in document.workflows:
            scopes = {key: scope.model_dump() for key, scope in config.scopes.items() if scope.entryWorkflowId == workflowId}
            sources = {key: source.model_dump() for key, source in config.sources.items() if source.resultScopeId in scopes}
            externalSources, externalScopes = set(sources), set(scopes)
            used, qtScopes = set(), set()
            if document.presentation:
                qt = presentationForWorkflow(document, workflowId, capture=True)
                used = {key for page in qt.pages.values() for component in walkComponents(page.components)
                        for key in component.bindings.values()}
                qtScopes = {qt.dataSources[key].resultScopeId for key in used}
                qtScopes.update(key for page in qt.pages.values() for key in page.resultScopeIds)
                for target, extra in ((sources, {key: qt.dataSources[key].model_dump() for key in used}),
                                      (scopes, {key: qt.resultScopes[key].model_dump() for key in qtScopes})):
                    for key, value in extra.items():
                        if key in target and target[key] != value:
                            raise ValueError(f'capture identity conflict: {key}')
                        target[key] = value
            try:
                outputCaptureLimits(sources, scopes)
            except ValueError:
                quota = captureQuotaDiagnostics(workflowId, document.workflows[workflowId].name,
                    sources, scopes, externalSources, externalScopes, used, qtScopes)
                if not quota:
                    raise
                diagnostics.extend(quota)
        return config, diagnostics

    def ValidateOutputRequests(self, request, context):
        with self._projectStateLock:
            error = self._outputTarget(request, write=True)
            if error:
                return self._outputReply(False, *error, runtimeInstanceId=self.runtimeInstanceId)
            try:
                config, diagnostics = self._validateOutputRequest(request)
                state = self._outputState()
                if request.expected_config_revision != state['configRevision']:
                    return self._outputReply(False, 'CONFIG_CONFLICT', 'Capture requests changed; refresh before publishing', **state)
                state.update(candidateConfigRevision=config.revision, diagnostics=diagnostics, state='VALIDATED' if not diagnostics else 'REJECTED')
                quota = any(item['code'].startswith('CAPTURE_') for item in diagnostics)
                message = '; '.join(d['message'] for d in diagnostics)
                if quota:
                    message += ' (' + '; '.join(dict.fromkeys(d['reason'] for d in diagnostics)) + ')'
                return self._outputReply(not diagnostics, 'OK' if not diagnostics else 'INVALID_ARGUMENT' if quota else 'INVALID_SOURCES',
                                         message, **state)
            except (ValueError, KeyError, TypeError) as error:
                return self._outputReply(False, 'INVALID_ARGUMENT', str(error), **self._outputState())

    def SubmitOutputRequests(self, request, context):
        self.jobSupervisor.assertMutationAllowed()
        with self._projectStateLock:
            error = self._outputTarget(request, write=True)
            if error:
                return self._outputReply(False, *error, runtimeInstanceId=self.runtimeInstanceId)
            if not re.fullmatch(r'\S{1,160}', request.request_id):
                return self._outputReply(False, 'INVALID_ARGUMENT', 'A stable request ID is required')
            fingerprint = revisionOf(dict(runtimeInstanceId=request.runtime_instance_id,
                projectId=request.project_id, documentRevision=request.expected_document_revision,
                configRevision=request.expected_config_revision, configJson=request.config_json))
            saved = self._outputStore().receipt(request.request_id)
            if saved:
                if saved[0] != fingerprint:
                    return self._outputReply(False, 'REQUEST_ID_CONFLICT', 'Request ID already belongs to another submission')
                return self._outputReply(**saved[1])
            validation = self.ValidateOutputRequests(request, context)
            if not validation.ok:
                return validation
            if context is not None and not context.is_active():
                return self._outputReply(False, 'CANCELLED', 'Submission cancelled before saving')
            config = OutputRequestConfig.model_validate_json(request.config_json)
            state = self._outputState()
            state.update(configRevision=config.revision, config=config.model_dump(), diagnostics=[], state='SAVED', requestId=request.request_id)
            self._outputStore().save(request.project_id, config, request.request_id, fingerprint, state)
            return self._outputReply(**state)

    def GetOutputRequestStatus(self, request, context):
        with self._projectStateLock:
            error = self._outputTarget(request)
            if error:
                return self._outputReply(False, *error, runtimeInstanceId=self.runtimeInstanceId)
            saved = self._outputStore().receipt(request.request_id)
            if saved is None:
                return self._outputReply(False, 'NOT_FOUND', 'No committed receipt for this request', state='NOT_FOUND', **self._outputState())
            if saved[1]['projectId'] != request.project_id:
                return self._outputReply(False, 'PROJECT_CHANGED', 'Receipt belongs to another project')
            return self._outputReply(**dict(saved[1], runtimeInstanceId=self.runtimeInstanceId))
