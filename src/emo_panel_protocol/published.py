"""Path-free derived rendering snapshot. This is not an editable project format."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import (Action, ComponentType, DataSource, Grid, Id, Model, Navigation, Placement,
                     VisualProps, ResponsiveItem, ResponsiveLayout, ResultScope)


class HistoryReference(Model):
    componentId: Id
    kind: Literal['table', 'aggregate', 'fixed_image', 'selected_row_image']
    columns: list[str] = Field(default_factory=list)
    orderBy: str = ''
    descending: bool = True
    aggregate: Literal['count', 'sum', 'avg', 'min', 'max'] = 'count'
    sourceComponentId: Id | None = None


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
    components: list[PublishedComponent]
    resultScopeIds: list[Id] = Field(default_factory=list)


class PublishedPresentation(Model):
    schemaVersion: Literal['published-1'] = 'published-1'
    defaultPageId: Id
    pageOrder: list[Id]
    navigation: Navigation
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
    sourcePresentationVersion: Literal['1.0', '1.1', '1.2']
    historyDefinitionRevision: str = Field(pattern=r'^[0-9a-f]{64}$')
    assets: dict[Id, PublishedAsset]
    captureRequirements: dict
