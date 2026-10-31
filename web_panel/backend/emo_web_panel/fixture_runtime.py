"""Read-only synthetic provider. Never creates a channel or sends fixture refs upstream."""
import base64
import json
from pathlib import Path


class FixtureRuntime:
    def __init__(self, context):
        if context.mode != 'fixtures' or context.runtime is not None:
            raise ValueError('Fixture provider requires explicit fixtures preview context')
        self.context = context
        self.directory = Path(__file__).parent / 'contract_data' / 'fixtures'

    def _fixture(self, name):
        return json.loads((self.directory / (name + '.json')).read_text())

    async def start(self):
        pass

    async def close(self):
        pass

    async def info(self):
        return {'runtimeInstanceId': 'demo-runtime-1', 'projectId': self.context.projectId,
                'configurationRevision': '1', 'historyDefinitionRevision': self.context.historyDefinitionRevision,
                'mode': 'fixtures', 'synthetic': True, 'contractStatus': '1.0',
                'capabilities': ['display', 'history']}

    async def runs(self):
        return [{'id': 'demo-j1', 'runId': 'demo-j1', 'jobId': 'demo-j1', 'runGroupId': 'demo-group-1',
                 'name': 'Synthetic contract example (not production)', 'runtimeInstanceId': 'demo-runtime-1',
                 'projectId': self.context.projectId, 'workflowId': 'main', 'state': 'completed',
                 'active': False, 'ownsResources': False, 'displayResourcesReleased': False,
                 'allowedActions': [], 'captureEnabled': True, 'synthetic': True}]

    async def workflows(self):
        return []

    async def snapshot(self, run_id):
        if run_id != 'demo-j1':
            raise ValueError('Unknown fixture Job')
        from emo_panel_protocol.results import ClosedResult
        from .runtime_client import browser_safe
        result = ClosedResult.model_validate_json((self.directory / 'closed-result.json').read_bytes())
        raw = result.model_dump(mode='json', exclude_none=True)
        sources = {source['sourceId']: {**source, 'state': source['state'].lower(),
                   **({'value': browser_safe(json.loads(source['valueJson']))} if 'valueJson' in source else {})}
                   for source in raw['sources']}
        return [{**raw['identity'], 'resultOrdinal': str(result.identity.resultOrdinal),
                 'projectId': self.context.projectId, 'cursor': '1', 'status': result.status.lower(),
                 'executionTerminal': result.executionTerminal, 'runState': 'completed',
                 'sources': sources, 'closedResult': raw, 'synthetic': True}]

    async def control(self, action, target, body):
        raise ValueError('Fixture previews never control production')

    async def source_problems(self, run_id, presentation):
        return await self.validate_coverage(presentation, run_id)

    async def validate_coverage(self, presentation, run_id):
        if run_id != 'demo-j1':
            raise ValueError('Unknown fixture Job')
        # Synthetic data is not evidence of capture coverage in a real Runtime.
        return {key: 'SOURCE_NOT_CAPTURED · No synthetic fixture for this source'
                for key in presentation.get('dataSources', {}) if key not in {'count', 'verdict'}}

    async def history(self, component_id, page, page_size, *, q='', sort='', order='asc', context=None,
                      kind='table', filter_column='', **kwargs):
        if not 1 <= page <= 10000 or not 1 <= page_size <= 100 or order not in {'asc', 'desc'}:
            raise ValueError('Invalid fixture history query')
        if context is not None and (context.get('projectId') != self.context.projectId or
                                   context.get('historyDefinitionRevision') != self.context.historyDefinitionRevision):
            raise ValueError('Fixture history context changed')
        if kind == 'aggregate':
            result = self._fixture('history-aggregate')
        else:
            result = self._fixture('history-page')
            names = [column['name'] for column in result['columns']]
            if sort and sort not in names or filter_column and filter_column not in names:
                raise ValueError('Unknown fixture column')
            rows = result['rows']
            if q:
                rows = [row for row in rows if any(q.lower() in str(value).lower()
                        for index, value in enumerate(row['values'])
                        if not filter_column or names[index] == filter_column)]
            if sort:
                rows.sort(key=lambda row: row['values'][names.index(sort)], reverse=order == 'desc')
            result['rows'] = rows[(page-1)*page_size:page*page_size]
            result.update(page=page-1, pageSize=page_size, hasNext=page*page_size < len(rows))
        result['historyDefinitionRevision'] = self.context.historyDefinitionRevision
        return result

    async def history_image(self, *, component_id=None, image_ref=None, context=None):
        if bool(component_id) == bool(image_ref):
            raise ValueError('Exactly one component or image reference required')
        if image_ref and image_ref != 'fixture-opaque-image-1':
            return b'', '', 'MISSING'
        data = self._fixture('history-image-available')
        return base64.b64decode(data['data']), data['mimeType'], data['status']

    async def counters(self):
        return {'source': 'synthetic-fixture', 'total': '12', 'ok': '12', 'ng': '0'}

    async def task_events(self, after='0'):
        return {'events': [], 'nextSequence': '0', 'resetRequired': False, 'source': 'synthetic-fixture'}
