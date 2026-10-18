"""Schema commands and static catalog. No Qt, execution or device access."""
from dataclasses import dataclass
from uuid import uuid4

from emo_master.core.plugin.models import PluginManifest
from emo_master.core.presentation.catalog import buildOutputCatalog, presentationType, resolveCallPath, sourceType
from emo_master.core.presentation.models import Component, DataSource, ResultScope, Presentation, walkComponents
from emo_master.apps.designer.state.presentation_store import _component
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields
from emo_master.ui.workflow_labels import workflowDisplayNames


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


def _workflowCalls(workflow):
    for node in workflow.nodes:
        if node.kind == 'subflow':
            yield node.nodeId, 'subflow', node.targetWorkflowId
        elif node.kind == 'loop':
            for field in loopWorkflowReferenceFields(node.loop):
                yield node.nodeId, 'loop_body' if field == 'bodyWorkflowId' else 'loop_condition', node.loop.get(field)


def scopeTitle(document, scope):
    names = workflowDisplayNames(document.workflows)
    path = ' / '.join(step.nodeId + ':' + step.relation for step in scope.callPath) or '入口结束'
    return f'入口 {names[scope.entryWorkflowId]} · 快照 {names[scope.scopeWorkflowId]} · {path}'


def outputChoices(document, manifests, *, onUnsupported=None, entryWorkflowIds=()):
    entries = buildOutputCatalog(document, manifests)
    choices = []
    scopes = []
    seen = set()
    names = workflowDisplayNames(document.workflows)
    presentation = document.presentation
    declared = list(presentation.resultScopes.values()) if presentation else []
    referenced = {target for workflow in document.workflows.values() for _, _, target in _workflowCalls(workflow)}
    # Discovery only: never persist a new entry/station configuration. Include
    # fresh independent roots as well as explicitly selected/previously bound Jobs.
    roots = dict.fromkeys([document.entryWorkflowId, *entryWorkflowIds,
        *(scope.entryWorkflowId for scope in declared),
        *(key for key in document.workflowOrder if key not in referenced)])

    def add(choice):
        identity = (choice.source.model_dump_json(exclude={'resultScopeId'}), choice.scope.model_dump_json())
        if identity not in seen:
            seen.add(identity)
            choices.append(choice)

    def visit(root, workflowId, path, ancestors):
        if workflowId in ancestors or len(path) > 32:
            return
        scope = ResultScope(entryWorkflowId=root, scopeWorkflowId=workflowId, callPath=path)
        scopes.append(scope)
        for entry in entries:
            if entry.workflowId != workflowId:
                continue
            try:
                outputType = presentationType(entry.spec)
            except ValueError as error:
                if onUnsupported is not None:
                    location = '/'.join(step.nodeId + ':' + step.relation for step in path) or '入口'
                    title = (f'入口 {names[root]} / {names[workflowId]} / {entry.displayName} '
                             f'[{entry.nodeId or "出口"}] / {entry.port} · {location}')
                    onUnsupported(title, str(error))
                continue
            for field, kind in [((), outputType), *entry.fields.items()]:
                source = DataSource(kind='node_output' if entry.nodeId else 'workflow_output',
                    resultScopeId='scope', workflowId=workflowId, nodeId=entry.nodeId, port=entry.port,
                    callPath=path, fieldPath=list(field), expectedType=kind)
                location = '/'.join(step.nodeId + ':' + step.relation for step in source.callPath) or '入口'
                add(Choice(f'入口 {names[root]} / {names[workflowId]} / {entry.displayName} '
                    f'[{entry.nodeId or "出口"}] / {entry.port}{"." + ".".join(field) if field else ""} '
                    f'({kind}) · {location}', source,
                    scope,
                    '; '.join([entry.hint, *(issue.message for issue in entry.issues)]).strip('; ')))
        from emo_master.core.presentation.models import CallStep
        for nodeId, relation, target in _workflowCalls(document.workflows[workflowId]):
            if target in document.workflows:
                visit(root, target, [*path, CallStep(nodeId=nodeId, relation=relation)], {*ancestors, workflowId})
    for root in roots:
        if root in document.workflows:
            visit(root, root, [], set())
    validDeclared = []
    for scope in declared:
        try:
            if resolveCallPath(document, scope.entryWorkflowId, scope.callPath) == scope.scopeWorkflowId:
                validDeclared.append(scope)
        except ValueError:
            pass  # Stale bindings remain visible to validateBindings, not new choices.
    # Variables have no workflow output address. The scope supplies both Job
    # identity and snapshot timing, including a frame inside a long-running loop.
    for scope in [*scopes, *validDeclared]:
        for key, variable in document.globalVariables.items():
            add(Choice(f"全局变量 / {variable.name} [{key}] ({variable.type}) · {scopeTitle(document, scope)}",
                DataSource(kind="global_variable", resultScopeId="scope", variableId=key, expectedType=variable.type),
                scope, "所选入口 Job 在此工作流结束时采集的变量快照；不是节点实时值"))
    # A saved output may legitimately use an ancestor subflow scope or numeric
    # widening. Keep that exact choice rather than silently moving its snapshot.
    for source in presentation.dataSources.values() if presentation else []:
        scope = presentation.resultScopes.get(source.resultScopeId)
        if scope not in validDeclared or source.kind not in {'node_output', 'workflow_output', 'global_variable'}:
            continue
        try:
            if source.kind != 'global_variable':
                if resolveCallPath(document, scope.entryWorkflowId, source.callPath) != source.workflowId:
                    continue
                prefix = scope.callPath
                if source.callPath[:len(prefix)] != prefix or any(
                        step.relation != 'subflow' for step in source.callPath[len(prefix):]):
                    continue
            actual = sourceType(source, entries, document.globalVariables)
            if actual != source.expectedType and (actual, source.expectedType) != ('integer', 'number'):
                continue
        except ValueError:
            continue
        label = (f'全局变量 / {document.globalVariables[source.variableId].name} [{source.variableId}]'
                 if source.kind == 'global_variable' else
                 f'{names[source.workflowId]} / {source.nodeId or "出口"} / {source.port}'
                 + ('.' + '.'.join(source.fieldPath) if source.fieldPath else ''))
        add(Choice(f'{label} ({source.expectedType}) · {scopeTitle(document, scope)}', source, scope,
                   '保留已有绑定的结果作用域和快照时机'))
    return choices


class PageCommands:
    def __init__(self, session, manifests):
        self.session = session
        self.manifests = manifests

    def preview(self):
        """Run the identical commands on an isolated, validated draft, without history."""
        return PageCommands(_PreviewSession(self.session.document()), self.manifests)

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
        actual = sourceType(choice.source, buildOutputCatalog(document, self.manifests), document.globalVariables)
        item = _component(document.presentation, pageId, componentId)
        if actual not in ACCEPTED.get(item.type, set()):
            raise ValueError(f'类型不兼容：{actual} → {item.type}')
        p = document.presentation
        key = next((k for k, v in p.resultScopes.items() if v == choice.scope), None)
        key = key or str(uuid4())
        source = choice.source.model_copy(deep=True)
        source.resultScopeId = key
        # One action, one transaction, including creating the scope and source.
        def edit(p):
            p.resultScopes[key] = choice.scope
            target = _component(p, pageId, componentId)
            target.props.imageResourceId = None
            prop = {'image': 'image', 'table': 'rows'}.get(target.type, 'value')
            sourceId = next((k for k, value in p.dataSources.items() if value == source), None) or str(uuid4())
            p.dataSources[sourceId] = source
            target.bindings[prop] = sourceId
            # Validate the complete proposed plan inside the same transaction.
            # A rejected third image/scope budget must roll back scope/source IDs.
            from emo_master.core.presentation.capture_limits import normalCaptureLimits
            normalCaptureLimits(p)
        self.session.editPresentation(edit)
        from emo_master.core.presentation.capture_limits import normalCaptureLimits
        return normalCaptureLimits(self.session.presentation.snapshot())


class _PreviewSession:
    def __init__(self, document):
        self._document = document.model_copy(deep=True)
        self.presentation = self

    def document(self):
        return self._document.model_copy(deep=True)

    def snapshot(self):
        return self._document.presentation.model_copy(deep=True)

    def editPresentation(self, edit):
        proposed = self.snapshot()
        edit(proposed)
        self._document.presentation = Presentation.model_validate(proposed.model_dump())
