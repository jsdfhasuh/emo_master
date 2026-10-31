"""Panel Contract v1 transport. Runtime is the only execution/history owner.

The explicit address is never replaced by localhost or demonstration data. SDK
imports are lazy so the explicit mock mode does not require a Runtime channel.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol


class ContractUnavailable(RuntimeError):
    pass


class RuntimeRejected(ValueError):
    def __init__(self, code, message):
        self.code = code or 'RUNTIME_REJECTED'
        super().__init__(f'{self.code}: {message}')


class RuntimePort(Protocol):
    async def info(self) -> dict: ...
    async def runs(self) -> list: ...
    async def snapshot(self, run_id: str) -> list: ...


def browser_safe(value):
    """Keep arbitrary precision integers exact at the JSON/browser boundary."""
    if type(value) is int and abs(value) > 9007199254740991:
        return str(value)
    if isinstance(value, dict):
        return {k: browser_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [browser_safe(v) for v in value]
    return value


class GrpcRuntimeClient:
    def __init__(self, address, *, preview_context=None, timeout=5, max_receive_bytes=9 * 1024 * 1024):
        self.address = address
        self.channel = None
        self.preview_context = preview_context
        self.timeout = timeout
        self.max_receive_bytes = max_receive_bytes
        self.coverage = {}
        self._identity = None
        self._streams = set()

    def configure_preview(self, context):
        if self.channel is not None:
            raise ValueError("Configure preview before opening channel")
        self.preview_context = context

    async def start(self):
        try:
            from emo_panel_protocol.connection import ConnectionProfile, createChannel
            from emo_panel_protocol.generated import runtime_pb2, runtime_pb2_grpc
        except ImportError as exc:
            raise ContractUnavailable('Install the supplied emo_panel_protocol 1.1.0 SDK and its declared dependencies') from exc
        self.pb = runtime_pb2
        profile = ConnectionProfile(self.address, self.timeout, self.max_receive_bytes)
        if self.preview_context is not None:
            profile = self.preview_context.runtime.connectionProfile()
        self.timeout = profile.timeoutSeconds
        self.channel = createChannel(profile, asynchronous=True)
        self.runtime = runtime_pb2_grpc.RuntimeServiceStub(self.channel)
        self.display = runtime_pb2_grpc.DisplayServiceStub(self.channel)

    async def close(self):
        for stream in list(self._streams):
            stream.cancel()
        self._streams.clear()
        if self.channel is not None:
            await self.channel.close()
            self.channel = None
        self.coverage.clear()

    @staticmethod
    def _ok(reply):
        if not reply.ok:
            raise RuntimeRejected(getattr(reply, 'code', ''), reply.message)
        return reply

    async def _info(self):
        reply = self._ok(await self.runtime.GetRuntimeInfo(self.pb.RuntimeInfoRequest(), timeout=self.timeout))
        if self.preview_context is not None:
            self.preview_context.validateRuntimeInfo(reply)
        if not reply.runtime_instance_id:
            raise RuntimeRejected('IDENTITY_MISSING', 'Runtime instance is required')
        if reply.protocol_version != '1.0' or 'runtime_info_v1' not in reply.capabilities:
            raise RuntimeRejected('CAPABILITY_UNAVAILABLE', 'Runtime does not expose Panel Contract v1 runtime info')
        identity = (reply.runtime_instance_id, reply.project_id)
        if identity != self._identity:
            self.coverage.clear()
            self._identity = identity
        return reply

    async def info(self):
        info = await self._info()
        return {'runtimeInstanceId': info.runtime_instance_id, 'projectId': info.project_id,
                'mode': 'grpc', 'protocolVersion': info.protocol_version,
                'configurationRevision': str(info.document_revision), 'documentRevision': str(info.document_revision),
                'historyDefinitionRevision': info.history_definition_revision,
                'productionMode': info.production_mode, 'productionExecutionMode': info.production_execution_mode,
                'startupWorkflowIds': list(info.startup_workflow_ids), 'capabilities': list(info.capabilities),
                'contractStatus': 'panel-contract-1.0-sdk-1.1.0'}

    @staticmethod
    def _run(run):
        return {'id': run.job_id, 'runId': run.job_id, 'jobId': run.job_id,
                'runtimeInstanceId': run.runtime_instance_id, 'projectId': run.project_id,
                'workflowId': run.workflow_id, 'name': run.workflow_name or run.workflow_id,
                'runGroupId': run.run_group_id, 'sourceJobId': run.source_job_id,
                'targetSlotId': run.target_slot_id, 'state': run.status.lower(), 'status': run.status,
                'active': run.active, 'ownsResources': run.owns_resources,
                'captureEnabled': run.capture_enabled, 'displayResourcesReleased': run.display_resources_released,
                'allowedActions': list(run.allowed_actions), 'startRequestId': run.start_request_id,
                'acceptedAtMs': str(run.accepted_at_ms), 'startedAtMs': str(run.started_at_ms),
                'endedAtMs': str(run.ended_at_ms), 'errorCode': run.error_code, 'message': run.message}

    async def runs(self):
        info = await self._info()
        reply = self._ok(await self.runtime.ListRuns(self.pb.ListRunsRequest(
            runtime_instance_id=info.runtime_instance_id, project_id=info.project_id,
            include_terminal=True), timeout=self.timeout))
        if reply.runtime_instance_id != info.runtime_instance_id:
            raise RuntimeRejected('IDENTITY_CHANGED', 'Runtime instance changed; refresh selection')
        if any(r.runtime_instance_id != info.runtime_instance_id or r.project_id != info.project_id for r in reply.runs):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Run directory identity does not match Runtime')
        return [self._run(run) for run in reply.runs]

    async def run_state(self, run_id):
        info = await self._info()
        run = self._ok(await self.runtime.GetRunState(self.pb.GetRunStateRequest(
            runtime_instance_id=info.runtime_instance_id, project_id=info.project_id,
            job_id=run_id), timeout=self.timeout)).run
        if (run.runtime_instance_id, run.project_id, run.job_id) != (info.runtime_instance_id, info.project_id, run_id):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Run state does not match selected identity')
        return self._run(run)

    async def job_events(self, run_id, after='0'):
        if not isinstance(after, str) or not after.isdecimal() or int(after) > 2**63-1:
            raise ValueError('Invalid event cursor')
        run = await self.run_state(run_id)
        stream = self.runtime.StreamJobEvents(self.pb.StreamJobEventsRequest(
            job_id=run_id, after_sequence=int(after), follow=False), timeout=self.timeout)
        events = []
        try:
            async for event in stream:
                if event.job_id != run_id or event.project_id != run['projectId']:
                    raise RuntimeRejected('IDENTITY_MISMATCH', 'Job event identity mismatch')
                events.append({'sequence':str(event.sequence),'timestampMs':str(event.timestamp_ms),
                    'runId':event.job_id,'kind':event.event_type,'level':event.level,
                    'code':event.code,'message':event.message})
                if len(events) >= 100:
                    break
        finally:
            stream.cancel()
        return {'events':events,'nextSequence':events[-1]['sequence'] if events else after,
                'resetRequired':False,'source':'runtime-per-job-events'}

    async def workflows(self):
        info = await self._info()
        reply = await self.runtime.ListWorkflows(self.pb.ListWorkflowsRequest(project_id=info.project_id), timeout=self.timeout)
        result = []
        for item in reply.workflows:
            inputs = json.loads(item.default_inputs_json or '{}')
            declarations = json.loads(item.inputs_json or '{}')
            result.append({'id': item.workflow_id, 'name': item.name, 'isEntry': item.is_entry,
                'inputs': browser_safe(inputs), 'inputsJson': item.default_inputs_json or '{}',
                'inputSchema': declarations if isinstance(declarations, dict) and declarations.get('type') == 'object' else None,
                'inputDeclarations': browser_safe(declarations), 'canStart': item.can_start,
                'startBlockReason': item.start_block_reason, 'configurationRevision': str(info.document_revision)})
        return result

    async def _job(self, run_id):
        from emo_panel_protocol.coverage import CaptureCoverage
        info = await self._info()
        run = self._ok(await self.runtime.GetRunState(self.pb.GetRunStateRequest(
            runtime_instance_id=info.runtime_instance_id, project_id=info.project_id, job_id=run_id), timeout=self.timeout)).run
        if (run.runtime_instance_id, run.project_id, run.job_id) != (info.runtime_instance_id, info.project_id, run_id):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Selected run identity changed')
        job = await self.display.GetJob(self.pb.DisplayJobRequest(runtime_instance_id=info.runtime_instance_id,
            project_id=info.project_id, job_id=run_id), timeout=self.timeout)
        if (job.runtime_instance_id, job.project_id, job.job_id) != (info.runtime_instance_id, info.project_id, run_id):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Display identity does not match selected run')
        # GetJob is intentionally a lightweight status lookup in the owning
        # Runtime. ListJobs carries frozen source/scope definitions and revisions.
        if job.capture_enabled:
            metadata = await self.display.ListJobs(self.pb.DisplayEmpty(project_id=info.project_id), timeout=self.timeout)
            matches = [item for item in metadata.jobs if (item.runtime_instance_id, item.project_id, item.job_id)
                       == (info.runtime_instance_id, info.project_id, run_id)]
            if len(matches) != 1 or not matches[0].capture_enabled:
                raise RuntimeRejected('CAPTURE_UNAVAILABLE', 'Selected capture metadata was retired; reselect a Job')
            job = matches[0]
        coverage = CaptureCoverage.fromMetadata(job)
        self.coverage[run_id] = coverage
        return info, run, coverage

    def _decode_snapshot(self, snapshot, info, run, coverage):
        from emo_panel_protocol.decoding import decodeResult
        if (snapshot.runtime_instance_id, snapshot.job_id) != (info.runtime_instance_id, run.job_id):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Snapshot does not match selected run')
        if snapshot.reset_required:
            raise RuntimeRejected('RESET_REQUIRED', 'Display cursor expired; request a new snapshot')
        # Gateway extension: a snapshot may retire/update scopes without any
        # newly closed result. Never drop these owner-authoritative high-water
        # marks merely because the official BrowserResult union has no slot.
        decoded = [{'type': 'scope_status', 'event': 'scope_status',
            'runtimeInstanceId': info.runtime_instance_id, 'projectId': info.project_id,
            'jobId': run.job_id, 'cursor': str(snapshot.cursor),
            'latestStartedOrdinals': {key: str(value) for key, value in snapshot.latest_started_ordinals.items()},
            'expiredScopeOrdinals': {key: str(value) for key, value in snapshot.expired_scope_ordinals.items()}}]
        for wire in snapshot.results:
            result = decodeResult(wire)
            if not coverage.matches(result.identity) or result.identity.resultScopeId not in coverage.scopes:
                raise RuntimeRejected('CAPTURE_MISMATCH', 'Result does not match frozen Job capture metadata')
            expected = {sid for sid, definition in coverage.sources.items()
                        if json.loads(definition)['resultScopeId'] == result.identity.resultScopeId}
            if set(result.expectedSourceIds) != expected:
                raise RuntimeRejected('CAPTURE_MISMATCH', 'Result sources do not match frozen scope capture definition')
            raw = result.model_dump(mode='json', exclude_none=True)
            sources = {}
            for source in raw['sources']:
                item = {**source, 'state': source['state'].lower()}
                if 'valueJson' in source:
                    item['value'] = browser_safe(json.loads(source['valueJson']))
                sources[source['sourceId']] = item
            decoded.append({**raw['identity'], 'resultOrdinal': str(result.identity.resultOrdinal),
                'projectId': info.project_id, 'cursor': str(snapshot.cursor),
                'latestStartedOrdinals': {k: str(v) for k, v in snapshot.latest_started_ordinals.items()},
                'expiredScopeOrdinals': {k: str(v) for k, v in snapshot.expired_scope_ordinals.items()},
                'status': result.status.lower(), 'executionTerminal': result.executionTerminal,
                'runState': run.status.lower(), 'sources': sources, 'closedResult': raw,
                'receivedAt': datetime.now(timezone.utc).isoformat()})
        return decoded

    async def snapshot(self, run_id):
        info, run, coverage = await self._job(run_id)
        if not coverage.enabled:
            raise RuntimeRejected('CAPTURE_UNAVAILABLE', 'Selected Job did not enable page capture; no automatic restart')
        snapshot = await self.display.Snapshot(self.pb.DisplayRequest(runtime_instance_id=info.runtime_instance_id,
            job_id=run_id), timeout=self.timeout)
        return self._decode_snapshot(snapshot, info, run, coverage)

    async def subscribe(self, run_id):
        """One caller per shared Views stream; reconnect must re-check identity."""
        info, run, coverage = await self._job(run_id)
        if not coverage.enabled:
            raise RuntimeRejected('CAPTURE_UNAVAILABLE', 'Selected Job did not enable page capture')
        snapshot = await self.display.Snapshot(self.pb.DisplayRequest(runtime_instance_id=info.runtime_instance_id,
            job_id=run_id), timeout=self.timeout)
        for result in self._decode_snapshot(snapshot, info, run, coverage):
            yield result
        stream = self.display.Subscribe(self.pb.DisplayRequest(runtime_instance_id=info.runtime_instance_id,
            job_id=run_id, after_cursor=snapshot.cursor))
        self._streams.add(stream)
        try:
            async for snapshot in stream:
                for result in self._decode_snapshot(snapshot, info, run, coverage):
                    yield result
        finally:
            stream.cancel()
            self._streams.discard(stream)

    async def source_problems(self, run_id, presentation):
        from emo_panel_protocol.published import PublishedPresentation
        _, _, coverage = await self._job(run_id)
        model = presentation if isinstance(presentation, PublishedPresentation) else PublishedPresentation.model_validate(presentation)
        return {source_id: problem for source_id in model.dataSources
                if (problem := coverage.sourceProblem(model, source_id))}

    async def _group_coverage(self, run_id):
        from emo_panel_protocol.coverage import CaptureCoverage, DispatchCoverage
        root = await self.run_state(run_id)
        # Selecting a consumer never silently switches to its producer or siblings.
        if not root['runGroupId'] or root['sourceJobId'] != run_id or root['targetSlotId']:
            _, _, coverage = await self._job(run_id)
            return [run_id], [coverage]
        runs = await self.runs()
        members = [root] + sorted([item for item in runs
            if item['id'] != run_id and item['runtimeInstanceId'] == root['runtimeInstanceId']
            and item['projectId'] == root['projectId'] and item['runGroupId'] == root['runGroupId']
            and item['sourceJobId'] == run_id and item['targetSlotId']], key=lambda item: item['id'])
        metadata = await self.display.ListJobs(self.pb.DisplayEmpty(project_id=root['projectId']), timeout=self.timeout)
        selected = []
        for member in members:
            matches = [item for item in metadata.jobs if (item.runtime_instance_id, item.project_id, item.job_id)
                == (root['runtimeInstanceId'], root['projectId'], member['id'])]
            if len(matches) != 1:
                raise RuntimeRejected('GROUP_METADATA_UNAVAILABLE', 'Dispatch member capture metadata is unavailable')
            item = matches[0]
            if (item.run_group_id, item.source_job_id, item.target_slot_id) != (
                    member['runGroupId'], member['sourceJobId'], member['targetSlotId']):
                raise RuntimeRejected('GROUP_IDENTITY_CHANGED', 'Dispatch membership changed; reselect the root Job')
            selected.append(item)
        coverages = [CaptureCoverage.fromMetadata(selected[0])]
        # The SDK validates each direct producer/consumer relationship. Multiple
        # consumers additionally need disjoint sources/scopes across all members.
        for consumer in selected[1:]:
            pair = DispatchCoverage.fromMetadata(selected[0], consumer)
            coverage = pair.members[1]
            if any(set(previous.sources) & set(coverage.sources) or set(previous.scopes) & set(coverage.scopes)
                   for previous in coverages):
                raise RuntimeRejected('GROUP_SCOPE_OVERLAP', 'Dispatch members have overlapping capture scopes')
            coverages.append(coverage)
        return [member['id'] for member in members], coverages

    async def group_members(self, run_id):
        run_ids, _ = await self._group_coverage(run_id)
        return run_ids

    async def validate_group(self, run_id, presentation):
        from emo_panel_protocol.published import PublishedPresentation
        run_ids, coverages = await self._group_coverage(run_id)
        model = presentation if isinstance(presentation, PublishedPresentation) else PublishedPresentation.model_validate(presentation)
        problems = {}
        for source_id in model.dataSources:
            owner = next((coverage for coverage in coverages if source_id in coverage.sources), coverages[0])
            problem = owner.sourceProblem(model, source_id)
            if problem:
                problems[source_id] = problem
        return {'runIds': run_ids, 'sourceProblems': problems}

    async def validate_coverage(self, presentation, run_id):
        return await self.source_problems(run_id, presentation)

    async def control(self, action, target, body):
        if self.preview_context is not None:
            raise RuntimeRejected('READ_ONLY_PREVIEW', 'Preview never starts or stops production')
        info = await self._info()
        if (body.get('runtimeInstanceId'), body.get('projectId')) != (info.runtime_instance_id, info.project_id):
            raise RuntimeRejected('IDENTITY_MISMATCH', 'Refresh Runtime selection before control')
        if action == 'start':
            request_id = body.get('requestId')
            revision = body.get('configurationRevision')
            if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
                raise ValueError('Stable start requestId required')
            if not isinstance(revision, str) or not revision.isdecimal() or int(revision) != info.document_revision or int(revision) <= 0:
                raise RuntimeRejected('DOCUMENT_REVISION_CHANGED', 'Refresh production inputs before starting')
            inputs = body.get('inputs', {})
            if 'inputsJson' in body:
                inputs = json.loads(body['inputsJson'])
            if not isinstance(inputs, dict):
                raise ValueError('Workflow inputs must be a JSON object')
            request = self.pb.StartJobRequest(project_id=info.project_id, workflow_id=body['workflowId'],
                inputs_json=json.dumps(inputs, allow_nan=False, ensure_ascii=True, separators=(',', ':')),
                capture_presentation=True, start_request_id=request_id, expected_runtime_instance_id=info.runtime_instance_id,
                expected_document_revision=int(revision))
            try:
                reply = await self.runtime.StartJob(request, timeout=self.timeout)
            except Exception as exc:
                import grpc
                if not isinstance(exc, grpc.RpcError):
                    raise
                # A timeout/transport error cannot establish whether Start executed.
                return {'state': 'unknown', 'outcome': 'UNKNOWN', 'startRequestId': request_id,
                        'message': 'Start outcome unknown; query the same request ID, never replay'}
            return self._start_result(reply, request_id)
        if action == 'stop':
            if not isinstance(body.get('runGroupId'), str):
                raise ValueError('Exact selected runGroupId string is required (empty for ungrouped Jobs)')
            reply = await self.runtime.StopJob(self.pb.StopJobRequest(job_id=target, mode='graceful',
                expected_runtime_instance_id=info.runtime_instance_id, expected_project_id=info.project_id,
                expected_run_group_id=body['runGroupId']), timeout=self.timeout)
            return {'state': 'accepted' if reply.ok else 'rejected', 'outcome': 'ACCEPTED' if reply.ok else 'REJECTED',
                    'runId': target, 'message': reply.message, 'status': reply.status}
        raise ValueError('Unsupported control action')

    @staticmethod
    def _start_result(reply, request_id):
        status = reply.status.upper()
        outcome = 'ACCEPTED' if reply.ok else ('UNKNOWN' if status in {'UNKNOWN', 'PENDING', 'NOT_FOUND'} else 'REJECTED')
        return {'state': outcome.lower(), 'outcome': outcome, 'runId': reply.job_id or None,
                'startRequestId': request_id, 'message': reply.message, 'status': reply.status,
                'configurationRevision': str(reply.project_revision)}

    async def lookup_start(self, request_id, runtime_instance_id):
        info = await self._info()
        if info.runtime_instance_id != runtime_instance_id:
            raise RuntimeRejected('IDENTITY_CHANGED', 'Cannot query old Start intent on another Runtime instance')
        reply = await self.runtime.GetStartRequest(self.pb.StartRequestLookup(start_request_id=request_id,
            runtime_instance_id=runtime_instance_id), timeout=self.timeout)
        return self._start_result(reply, request_id)

    async def _history_context(self, context=None):
        info = await self._info()
        context = context or {'runtimeInstanceId': info.runtime_instance_id, 'projectId': info.project_id,
                              'historyDefinitionRevision': info.history_definition_revision}
        if (context.get('runtimeInstanceId'), context.get('projectId'), context.get('historyDefinitionRevision')) != (
                info.runtime_instance_id, info.project_id, info.history_definition_revision):
            raise RuntimeRejected('E_HISTORY_REVISION_CHANGED', 'History identity/declaration changed; refresh context')
        return self.pb.HistoryContext(runtime_instance_id=info.runtime_instance_id, project_id=info.project_id,
                                      history_definition_revision=info.history_definition_revision)

    async def history(self, component_id, page, page_size, *, q='', sort='', order='asc', context=None,
                      kind='table', filter_column='', aggregation='count', field='', follow_filter=False):
        from emo_panel_protocol.messages import HistoryPage, HistoryAggregate
        ctx = await self._history_context(context)
        if not 1 <= page <= 10000 or not 1 <= page_size <= 100 or order not in {'asc', 'desc'}:
            raise ValueError('Invalid history page/sort')
        if kind == 'aggregate':
            reply = self._ok(await self.runtime.QueryHistoryAggregate(self.pb.QueryHistoryAggregateRequest(
                context=ctx, component_id=component_id, filter_column=filter_column, filter_text=q), timeout=self.timeout))
            value = HistoryAggregate.model_validate_json(reply.result_json).model_dump(mode='json')
        else:
            reply = self._ok(await self.runtime.QueryHistoryPage(self.pb.QueryHistoryPageRequest(context=ctx,
                component_id=component_id, page=page-1, page_size=page_size, filter_column=filter_column,
                filter_text=q, order_by=sort, descending=order == 'desc'), timeout=self.timeout))
            value = HistoryPage.model_validate_json(reply.result_json).model_dump(mode='json')
        if value['historyDefinitionRevision'] != ctx.history_definition_revision:
            raise RuntimeRejected('E_HISTORY_REVISION_CHANGED', 'History reply revision changed')
        return value

    async def history_image(self, *, component_id=None, image_ref=None, context=None):
        if bool(component_id) == bool(image_ref):
            raise ValueError('Exactly one declared component or opaque reference is required')
        ctx = await self._history_context(context)
        target = {'component_id': component_id} if component_id else {'image_ref': image_ref}
        reply = self._ok(await self.runtime.ReadHistoryImage(self.pb.ReadHistoryImageRequest(context=ctx, **target), timeout=self.timeout))
        if reply.status != 'AVAILABLE':
            if reply.status not in {'EMPTY', 'MISSING', 'UNSUPPORTED', 'TOO_LARGE'}:
                raise RuntimeRejected('INVALID_IMAGE_STATUS', 'Unsupported history image status')
            return b'', '', reply.status
        if len(reply.data) > 8 * 1024 * 1024 or reply.width * reply.height > 32_000_000 or reply.mime_type not in {'image/png', 'image/jpeg', 'image/bmp'}:
            raise RuntimeRejected('IMAGE_BUDGET_EXCEEDED', 'History image exceeds contract limits')
        return reply.data, reply.mime_type, reply.status

    async def read_asset(self, run_id, resource, *, runtime_instance_id=None):
        info = await self._info()
        if runtime_instance_id is not None and runtime_instance_id != info.runtime_instance_id:
            raise RuntimeRejected('IDENTITY_CHANGED', 'Image belongs to an old Runtime instance')
        request = self.pb.DisplayAssetRequest(runtime_instance_id=info.runtime_instance_id, job_id=run_id,
            resource_id=resource['resourceId'], ttl_ms=5000)
        lease = await self.display.AcquireLease(request, timeout=self.timeout)
        try:
            reply = await self.display.ReadAsset(request, timeout=self.timeout)
            data = bytes(reply.content)
            if len(data) > 8 * 1024 * 1024 or len(data) != resource['byteSize']:
                raise RuntimeRejected('ASSET_SIZE_MISMATCH', 'Result asset byte size mismatch')
            digest = hashlib.sha256(data).hexdigest()
            if digest != reply.sha256 or digest != resource['sha256']:
                raise RuntimeRejected('ASSET_DIGEST_MISMATCH', 'Result asset digest mismatch')
            if resource['mimeType'] not in {'image/png', 'image/jpeg'}:
                raise RuntimeRejected('ASSET_TYPE_MISMATCH', 'Unsupported result image type')
            return data, resource['mimeType']
        finally:
            await self.display.ReleaseLease(lease, timeout=self.timeout)

    async def counters(self):
        info = await self._info()
        reply = self._ok(await self.runtime.ListGlobalCounters(self.pb.ListGlobalCountersRequest(project_id=info.project_id), timeout=self.timeout))
        return {'source': 'runtime-authoritative', 'counters': [
            {'name': c.name, 'value': str(c.value), 'updatedAtMs': str(c.updated_at_ms)} for c in reply.counters]}

    async def task_events(self, after='0'):
        # The official log stream is per Job, not a global sequence. Do not invent
        # global ordering, or equate received frame count with production output.
        return {'events': [], 'nextSequence': '0', 'resetRequired': False,
                'source': 'runtime-per-job-events', 'message': 'Select a Job to inspect its authoritative event stream'}
