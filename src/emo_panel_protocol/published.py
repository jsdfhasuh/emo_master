"""Path-free derived rendering snapshot. This is not an editable project format."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_serializer

from .models import (Action, ComponentType, DataSource, Grid, HISTORY_ACTIONS, Id, Model,
                     Navigation, PageAppearance, PageShell, Placement, VisualProps, ResponsiveItem,
                     ResponsiveLayout, ResultScope)


class HistoryReference(Model):
    componentId: Id = Field(description='Component ID of the declared Runtime history source')
    kind: Literal['table', 'aggregate', 'fixed_image', 'selected_row_image']
    columns: list[str] = Field(default_factory=list)
    orderBy: str = ''
    descending: bool = True
    aggregate: Literal['count', 'sum', 'avg', 'min', 'max'] = 'count'
    sourceComponentId: Id | None = Field(default=None, description='Same-page table instance followed by a selected-row image')


class PublishedProps(VisualProps):
    """All ordinary rendering props; local sources belong to the authoring model."""


class PublishedComponent(Model):
    componentId: Id
    type: ComponentType
    version: Literal['1.0'] = '1.0'
    layout: Placement = Field(default_factory=Placement)
    responsive: ResponsiveItem = Field(default_factory=ResponsiveItem)
    props: PublishedProps = Field(default_factory=PublishedProps)
    bindings: dict[Id, Id] = Field(default_factory=dict)
    actions: dict[Literal['clicked'], Action] = Field(default_factory=dict)
    children: list[PublishedComponent] = Field(default_factory=list)
    grid: Grid | ResponsiveLayout = Field(default_factory=ResponsiveLayout)
    history: HistoryReference | None = None


class PublishedPage(Model):
    name: str
    layout: Grid | ResponsiveLayout
    canvasTop: int = Field(default=0, ge=-32768, le=0)
    components: list[PublishedComponent]
    resultScopeIds: list[Id] = Field(default_factory=list)

    @model_serializer(mode='wrap')
    def serializeCanvas(self, handler):
        payload = handler(self)
        if not self.canvasTop:
            payload.pop('canvasTop', None)
        return payload


class PublishedPresentation(Model):
    schemaVersion: Literal['published-1'] = 'published-1'
    defaultPageId: Id
    pageOrder: list[Id]
    navigation: Navigation
    appearance: PageAppearance | None = None
    shell: PageShell | None = None
    resultScopes: dict[Id, ResultScope]
    dataSources: dict[Id, DataSource]
    pages: dict[Id, PublishedPage]


class PublishedAsset(Model):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    byteSize: int = Field(ge=0, le=8 * 1024 * 1024)
    mimeType: Literal['image/png', 'image/jpeg', 'image/bmp']


class PanelManifest(Model):
    format: Literal['emo-web-panel-1'] = 'emo-web-panel-1'
    projectId: Id
    packageRevision: str = Field(pattern=r'^[0-9a-f]{64}$')
    panelContractVersion: Literal['1.0'] = '1.0'
    rendererRequirements: list[str]
    presentation: Literal['presentation.json'] = 'presentation.json'
    sourcePresentationVersion: Literal['1.3']
    historyDefinitionRevision: str = Field(pattern=r'^[0-9a-f]{64}$')
    assets: dict[Id, PublishedAsset]
    captureRequirements: dict


def compositionRequirements(presentation):
    """Renderer capabilities for the configured properties of this projection."""
    required = set()
    if presentation.get('appearance'):
        required.add('presentation.composable-v1')
    if presentation.get('shell'):
        required.add('shell.operational-v1')

    def visit(components):
        for component in components:
            props = component['props']
            if (any(props.get(key) is not None for key in ('surface', 'typography', 'imageView',
                                                          'collapse', 'showMarker'))
                    or props.get('helperText') or props.get('valueLayout', 'vertical') != 'vertical'
                    or props.get('indicatorView', 'text') != 'text'
                    or props.get('buttonStyle', 'secondary') != 'secondary'
                    or any(state.get('background') == 'soft' or state.get('color') == 'blue'
                           for state in props.get('indicatorStates', {}).values())):
                required.add('presentation.composable-v1')
            if (props.get('recordValue') or props.get('historyView') or props.get('historySource')
                    or props.get('sqlite', {}).get('recordKey')
                    or any(action['type'] in HISTORY_ACTIONS for action in component['actions'].values())):
                required.add('history.record-selection-v1')
            if props.get('statusField'):
                required.add('shell.operational-v1')
            visit(component['children'])

    for page in presentation['pages'].values():
        visit(page['components'])
    return required
