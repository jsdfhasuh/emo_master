"""Schema commands and static catalog. No Qt, execution or device access."""
from dataclasses import dataclass
from uuid import uuid4

from emo_master.core.plugin.models import PluginManifest
from emo_master.core.presentation.catalog import buildOutputCatalog, presentationType, sourceType
from emo_master.core.presentation.models import Component, DataSource, ResultScope, walkComponents
from emo_master.apps.designer.state.presentation_store import _component


ACCEPTED = {'number': {'integer', 'number'}, 'text': {'integer', 'number', 'boolean', 'string', 'json'},
            'image': {'image'}, 'indicator': {'boolean', 'string'}, 'table': {'collection'}}


def manifestsFromCatalog(catalog):
    return {item['operatorId']: PluginManifest(operatorId=item['operatorId'],
        displayName=item['displayName'], version=item['version'], entry='', category=item['category'],
        iconKey=item['iconKey'], summary=item['summary'],
        inputPorts=item.get('inputPortSpecs') or item['inputPorts'],
        outputPorts=item.get('outputPortSpecs') or item['outputPorts'], paramSchema=item['paramSchema'],
        minCoreVersion='', maxCoreVersion='') for item in catalog}


@dataclass(frozen=True)
class Choice:
    title: str
    source: DataSource
    scope: ResultScope
    hint: str


def outputChoices(document, manifests):
    entries = buildOutputCatalog(document, manifests)
    choices = []

    def visit(workflowId, path, ancestors):
        if workflowId in ancestors or len(path) > 16:
            return
        for entry in entries:
            if entry.workflowId != workflowId:
                continue
            for field, kind in [((), presentationType(entry.spec)), *entry.fields.items()]:
                source = DataSource(kind='node_output' if entry.nodeId else 'workflow_output',
                    resultScopeId='scope', workflowId=workflowId, nodeId=entry.nodeId, port=entry.port,
                    callPath=path, fieldPath=list(field), expectedType=kind)
                location = '/'.join(step.nodeId + ':' + step.relation for step in source.callPath) or '入口'
                choices.append(Choice(f'{document.workflows[workflowId].name} / {entry.displayName} '
                    f'[{entry.nodeId or "出口"}] / {entry.port}{"." + ".".join(field) if field else ""} '
                    f'({kind}) · {location}', source,
                    ResultScope(entryWorkflowId=document.entryWorkflowId, scopeWorkflowId=workflowId, callPath=path),
                    '; '.join([entry.hint, *(issue.message for issue in entry.issues)]).strip('; ')))
        from emo_master.core.presentation.models import CallStep
        for node in document.workflows[workflowId].nodes:
            targets = [('subflow', node.targetWorkflowId)] if node.kind == 'subflow' else (
                [('loop_body', node.loop.get('bodyWorkflowId')), ('loop_condition', node.loop.get('conditionWorkflowId'))]
                if node.kind == 'loop' else [])
            for relation, target in targets:
                if target in document.workflows:
                    visit(target, [*path, CallStep(nodeId=node.nodeId, relation=relation)], {*ancestors, workflowId})
    visit(document.entryWorkflowId, [], set())
    return choices


class PageCommands:
    def __init__(self, session, manifests):
        self.session = session
        self.manifests = manifests

    def children(self, p, pageId, parentId):
        if parentId is None:
            return p.pages[pageId].components
        parent = _component(p, pageId, parentId)
        if parent.type != 'container':
            raise ValueError('目标必须是容器')
        return parent.children

    def add(self, pageId, kind, row, column, parentId=None):
        component = Component(componentId=str(uuid4()), type=kind,
                              layout={'row': row, 'column': column})
        self.session.editPresentation(lambda p: self.children(p, pageId, parentId).append(component))
        return component.componentId

    def update(self, pageId, componentId, *, props, layout, actions=None, columns=None):
        def edit(p):
            item = _component(p, pageId, componentId)
            item.props = props
            item.layout = layout
            if actions is not None:
                item.actions = actions
            if columns is not None:
                if item.type == 'container':
                    item.grid.columns = columns
                else:
                    p.pages[pageId].layout.columns = columns
        self.session.editPresentation(edit)

    def move(self, pageId, componentId, row, column, parentId=None):
        def edit(p):
            item = _component(p, pageId, componentId)
            if parentId in {c.componentId for c in walkComponents([item])}:
                raise ValueError('容器不能移入自身或子树')
            self._remove(p.pages[pageId].components, componentId)
            item.layout.row, item.layout.column = row, column
            self.children(p, pageId, parentId).append(item)
        self.session.editPresentation(edit)

    def _remove(self, items, key):
        for index, item in enumerate(items):
            if item.componentId == key:
                items.pop(index)
                return True
            if self._remove(item.children, key):
                return True
        return False

    def delete(self, pageId, componentId):
        self.session.editPresentation(lambda p: self._remove(p.pages[pageId].components, componentId))

    def copy(self, pageId, componentId):
        copied = _component(self.session.presentation.snapshot(), pageId, componentId).model_copy(deep=True)
        for item in walkComponents([copied]):
            item.componentId = str(uuid4())
        # Copy into an explicit free root row, keeping all original bindings immutable.
        def edit(p):
            children = p.pages[pageId].components
            copied.layout.row = max((c.layout.row + c.layout.rowSpan for c in children), default=0)
            copied.layout.column = 0
            children.append(copied)
        self.session.editPresentation(edit)
        return copied.componentId

    def bind(self, pageId, componentId, choice):
        document = self.session.document()
        actual = sourceType(choice.source, buildOutputCatalog(document, self.manifests))
        item = _component(document.presentation, pageId, componentId)
        if actual not in ACCEPTED.get(item.type, set()):
            raise ValueError(f'类型不兼容：{actual} → {item.type}')
        p = document.presentation
        if actual == 'image':
            otherImages = {p.dataSources[key].model_dump_json(exclude={'resultScopeId'})
                for currentPageId, page in p.pages.items() for component in walkComponents(page.components)
                if not (currentPageId == pageId and component.componentId == componentId)
                for key in component.bindings.values() if key in p.dataSources
                and p.dataSources[key].expectedType == 'image'}
            if otherImages - {choice.source.model_dump_json(exclude={'resultScopeId'})}:
                raise ValueError('当前运行采集仅支持一个图像来源；两页可复用同一来源，第二张图尚未支持')
        used = {key for page in p.pages.values() for component in walkComponents(page.components)
                for key in component.bindings.values()}
        scopes = {p.dataSources[key].resultScopeId for key in used if key in p.dataSources}
        key = next((k for k, v in p.resultScopes.items() if v == choice.scope), None)
        if scopes and (key is None or scopes != {key}):
            raise ValueError('当前 P2/P3 仅支持一个调用作用域，请清除其他作用域绑定后再选择')
        key = key or str(uuid4())
        source = choice.source.model_copy(deep=True)
        source.resultScopeId = key
        # One action, one transaction, including creating the scope and source.
        def edit(p):
            p.resultScopes[key] = choice.scope
            target = _component(p, pageId, componentId)
            prop = {'image': 'image', 'table': 'rows'}.get(target.type, 'value')
            sourceId = next((k for k, value in p.dataSources.items() if value == source), None) or str(uuid4())
            p.dataSources[sourceId] = source
            target.bindings[prop] = sourceId
        self.session.editPresentation(edit)
