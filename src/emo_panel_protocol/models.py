from __future__ import annotations

from typing import Annotated, Literal
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer


Id = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^\S+$")]
HexColor = Annotated[str, Field(min_length=7, max_length=7, pattern=r'^#[0-9a-fA-F]{6}$')]
BorderWidth = Annotated[int, Field(ge=0, le=16)]
BorderStyle = Literal['solid', 'dashed', 'dotted']
# An ungrouped Job has a real, explicitly empty group identity. Reuse Id for
# nonempty groups. The schema also forbids whitespace explicitly because JSON
# Schema regex engines may let '$' match before a trailing newline.
RunGroupId = Annotated[Literal[""] | Id, Field(json_schema_extra={"not": {"pattern": r"\s"}})]
ValueType = Literal[
    "integer", "number", "string", "boolean", "image", "collection", "geometry", "json"
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CallStep(Model):
    nodeId: Id
    relation: Literal["subflow", "loop_body", "loop_condition"] = "subflow"


class ResultScope(Model):
    entryWorkflowId: Id
    scopeWorkflowId: Id
    callPath: list[CallStep] = Field(default_factory=list, max_length=32)
    commitPolicy: Literal["workflow_terminal"] = "workflow_terminal"


class DataSource(Model):
    """Value object: stores return detached copies; rebind replaces the reference."""

    kind: Literal["node_output", "workflow_output", "global_counter", "global_variable", "runtime_status"]
    resultScopeId: Id
    workflowId: Id | None = None
    callPath: list[CallStep] = Field(default_factory=list, max_length=32)
    nodeId: Id | None = None
    port: Id | None = None
    fieldPath: list[Id] = Field(default_factory=list, max_length=16)
    expectedType: ValueType
    name: Id | None = None
    variableId: Id | None = None
    ruleVersion: Literal["1.0"] = "1.0"

    @model_serializer(mode="wrap")
    def serializeAddress(self, handler):
        payload = handler(self)
        if self.kind != "global_variable":
            payload.pop("variableId", None)
        return payload

    @model_validator(mode="after")
    def checkAddress(self) -> DataSource:
        if self.kind == "global_variable":
            if (not self.variableId or self.workflowId or self.nodeId or self.port or self.callPath
                    or self.fieldPath or self.name):
                raise ValueError("global variable requires only variableId, not an output/name address")
            return self
        if self.variableId is not None:
            raise ValueError("only global_variable accepts variableId")
        if self.kind in {"node_output", "workflow_output"}:
            if not self.workflowId or not self.port or self.name is not None:
                raise ValueError("output source requires workflowId/port, forbids name")
            if (self.nodeId is not None) != (self.kind == "node_output"):
                raise ValueError("only node_output requires nodeId")
        elif (self.workflowId or self.nodeId or self.port or self.callPath or self.fieldPath
              or not self.name):
            raise ValueError("counter/status requires name and forbids output address")
        return self


class Grid(Model):
    type: Literal["grid"] = "grid"
    columns: int = Field(default=1, ge=1, le=24)
    spacing: int = Field(default=8, ge=0, le=128)


class ResponsiveLayout(Model):
    """Ordered layout rules shared by native and future browser renderers."""
    type: Literal["responsive"] = "responsive"
    direction: Literal["vertical", "horizontal", "wrap"] = "vertical"
    spacing: int = Field(default=12, ge=0, le=128)
    alignment: Literal["start", "center", "end", "stretch"] = "stretch"
    justify: Literal["start", "center", "end", "space-between"] = "start"
    equalWidth: bool = False


class ResponsiveItem(Model):
    width: Literal["fill", "content"] = "fill"
    minWidth: int = Field(default=160, ge=0, le=4096)
    preferredWidth: int = Field(default=240, ge=0, le=8192)
    weight: float = Field(default=1, ge=0, le=100)
    minHeight: int = Field(default=64, ge=0, le=4096)
    preferredHeight: int = Field(default=0, ge=0, le=8192)
    alignSelf: Literal["auto", "start", "center", "end", "stretch"] = "auto"
    canvasOffsetY: int = Field(default=0, ge=-32768, le=0)

    @model_serializer(mode="wrap")
    def serializeCanvasOffset(self, handler):
        payload = handler(self)
        if not self.canvasOffsetY:
            payload.pop('canvasOffsetY', None)
        return payload


class Placement(Model):
    row: int = Field(default=0, ge=0, le=4095)
    column: int = Field(default=0, ge=0, le=23)
    rowSpan: int = Field(default=1, ge=1, le=128)
    columnSpan: int = Field(default=1, ge=1, le=24)


class IndicatorStyle(Model):
    text: str = Field(max_length=256)
    color: Literal['neutral', 'green', 'red', 'amber', 'blue'] | HexColor = 'neutral'
    background: Literal['none', 'soft'] | HexColor = 'none'
    borderColor: HexColor | None = None
    borderWidth: BorderWidth | None = None
    borderStyle: BorderStyle | None = None


class TableColumn(Model):
    title: str = Field(max_length=256)
    fieldPath: list[Id] = Field(default_factory=list, max_length=16)


class SqliteSource(Model):
    """Local, read-only table selection; never stores executable SQL."""

    databasePath: str = Field(default="", max_length=4096)
    table: str = Field(default="", max_length=256)
    columns: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32)
    orderBy: str = Field(default="", max_length=256)
    descending: bool = True
    aggregate: Literal["count", "sum", "avg", "min", "max"] = "count"
    valueColumn: str = Field(default="", max_length=256)
    location: Literal["display_local", "runtime"] = "display_local"
    recordKey: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @model_serializer(mode="wrap")
    def serializeLocation(self, handler):
        payload = handler(self)
        if self.location == "display_local":
            payload.pop("location", None)
        return payload

    @model_validator(mode="after")
    def checkSelection(self) -> SqliteSource:
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("SQLite columns must be unique")
        if any("\x00" in value for value in (
                self.databasePath, self.table, self.orderBy, self.valueColumn, self.recordKey or '', *self.columns)):
            raise ValueError("SQLite paths and identifiers cannot contain NUL")
        return self


class ImagePathSource(Model):
    """A page-local filename, independent of Runtime result capture."""

    kind: Literal["fixed", "selected_row"]
    path: str = Field(default="", max_length=4096)
    componentId: Id | None = None
    field: str = Field(default="", max_length=256)
    baseDirectory: str = Field(default="", max_length=4096)
    location: Literal["display_local", "runtime"] | None = None

    @model_serializer(mode="wrap")
    def serializeLocation(self, handler):
        payload = handler(self)
        if self.location in (None, "display_local"):
            payload.pop("location", None)
        return payload

    @model_validator(mode="after")
    def checkAddress(self) -> ImagePathSource:
        if self.kind == "fixed" and self.location is None:
            self.location = "display_local"
        if self.kind == "selected_row" and self.location is not None:
            raise ValueError("selected-row image location is inherited from its table")
        if any("\x00" in value for value in (self.path, self.field, self.baseDirectory)):
            raise ValueError("image paths and fields cannot contain NUL")
        if self.kind == "fixed" and (self.componentId is not None or self.field):
            raise ValueError("fixed image path forbids componentId and field")
        if self.kind == "selected_row" and (not self.componentId or not self.field or self.path):
            raise ValueError("selected_row image requires componentId and field, without fixed path")
        return self


class Padding(Model):
    top: int | None = Field(default=None, ge=0, le=64)
    right: int | None = Field(default=None, ge=0, le=64)
    bottom: int | None = Field(default=None, ge=0, le=64)
    left: int | None = Field(default=None, ge=0, le=64)


class Surface(Model):
    kind: Literal['none', 'card'] | None = None
    background: Literal['transparent', 'surface', 'subtle'] | HexColor | None = None
    border: Literal['none', 'subtle'] | None = None
    borderColor: HexColor | None = None
    borderWidth: BorderWidth | None = None
    borderStyle: BorderStyle | None = None
    radius: int | None = Field(default=None, ge=0, le=32)
    padding: Padding | None = None


class TextRole(Model):
    size: int | None = Field(default=None, ge=8, le=96)
    weight: Literal['normal', 'bold'] | None = None
    color: Literal['default', 'neutral', 'primary', 'green', 'red', 'amber'] | HexColor | None = None


class Typography(Model):
    title: TextRole | None = None
    value: TextRole | None = None
    helper: TextRole | None = None


class ImageView(Model):
    sizing: Literal['fixed', 'aspect'] = 'aspect'
    aspectRatio: float | None = Field(default=None, gt=0)
    background: Literal['subtle', 'dark', 'transparent'] | HexColor = 'subtle'
    horizontalAlign: Literal['start', 'center', 'end'] = 'center'
    verticalAlign: Literal['start', 'center', 'end'] = 'center'


class Collapse(Model):
    initiallyCollapsed: bool = False


class HistoryView(Model):
    mode: Literal['table', 'list'] = 'table'
    titleField: str | None = Field(default=None, min_length=1, max_length=256)
    subtitleField: str | None = Field(default=None, min_length=1, max_length=256)
    trailingField: str | None = Field(default=None, min_length=1, max_length=256)
    showFilter: bool = True
    showSort: bool = True
    showPagination: bool = True


class RecordField(Model):
    componentId: Id
    kind: Literal['field']
    field: str = Field(min_length=1, max_length=256)


class RecordMeta(Model):
    componentId: Id
    kind: Literal['meta']
    meta: Literal['position', 'count']


RecordValue = Annotated[RecordField | RecordMeta, Field(discriminator='kind')]
HISTORY_ACTIONS = frozenset({'history_previous', 'history_next', 'history_first'})


class NativeStyle(Model):
    enabled: bool = False
    code: str = ''


class CustomStyle(Model):
    qtQss: NativeStyle | None = None
    webCss: NativeStyle | None = None


class ButtonStateAppearance(Model):
    background: Literal['transparent'] | HexColor | None = None
    color: HexColor | None = None
    borderColor: HexColor | None = None
    borderWidth: BorderWidth | None = None
    borderStyle: BorderStyle | None = None


class ButtonStates(Model):
    default: ButtonStateAppearance | None = None
    hover: ButtonStateAppearance | None = None
    pressed: ButtonStateAppearance | None = None
    disabled: ButtonStateAppearance | None = None


class VisualProps(Model):
    spacerMode: Literal["fixed", "flex"] = "fixed"
    imageResourceId: Id | None = None
    iconResourceId: Id | None = None
    title: str = Field(default="", max_length=1024)
    text: str = Field(default="", max_length=4096)
    emptyText: str = Field(default="—", max_length=256)
    unit: str = Field(default="", max_length=64)
    decimals: int = Field(default=2, ge=0, le=12)
    indicatorStates: dict[str, IndicatorStyle] = Field(default_factory=dict, max_length=16)
    columns: list[TableColumn] = Field(default_factory=list, max_length=16)
    pageSize: int = Field(default=20, ge=1, le=100)
    surface: Surface | None = None
    typography: Typography | None = None
    valueLayout: Literal['vertical', 'horizontal'] = 'vertical'
    helperText: str = Field(default='', max_length=1024)
    indicatorView: Literal['text', 'badge', 'row'] = 'text'
    showMarker: bool | None = None
    titleFollowsState: bool = False
    markerPosition: Literal['value', 'title'] = 'value'
    imageView: ImageView | None = None
    buttonStyle: Literal['primary', 'secondary', 'link'] = 'secondary'
    iconPosition: Literal['left', 'right'] | None = None
    iconSize: int | None = Field(default=None, ge=8, le=64)
    iconGap: int | None = Field(default=None, ge=0, le=32)
    buttonStates: ButtonStates | None = None
    customStyle: CustomStyle | None = None
    collapse: Collapse | None = None
    statusField: Literal['connection', 'job_state'] | None = None
    historyView: HistoryView | None = None
    recordValue: RecordValue | None = None

    @model_serializer(mode="wrap")
    def serializeVisual(self, handler):
        payload = handler(self)
        if self.spacerMode == "fixed":
            payload.pop("spacerMode", None)
        return payload

    @model_validator(mode='after')
    def checkStates(self) -> VisualProps:
        for key in self.indicatorStates:
            if len(key) > 256:
                raise ValueError('indicator key too long')
            value = json.loads(key)
            if type(value) not in (int, bool, str) or json.dumps(value, ensure_ascii=False) != key:
                raise ValueError('indicator keys must be canonical JSON integer/boolean/string literals')
        return self


class HistorySource(Model):
    """A Web-authored reference to an existing Runtime history declaration."""

    componentId: Id
    sourceComponentId: Id | None = None


class Props(VisualProps):
    imagePath: ImagePathSource | None = None
    sqlite: SqliteSource = Field(default_factory=SqliteSource)
    historySource: HistorySource | None = None

    @model_serializer(mode="wrap")
    def serializeResources(self, handler):
        payload = handler(self)
        for field in ("imageResourceId", "iconResourceId", "imagePath", "historySource"):
            if payload.get(field) is None:
                payload.pop(field, None)
        if self.spacerMode == "fixed":
            payload.pop("spacerMode", None)
        if self.sqlite == SqliteSource():
            payload.pop("sqlite", None)
        return payload

class Action(Model):
    type: Literal["navigate", "freeze", "resume_live", 'history_previous', 'history_next', 'history_first']
    pageId: Id | None = None
    context: Literal["live", "displayed_result"] = "live"
    resultScopeId: Id | None = None
    componentId: Id | None = None

    @model_validator(mode="after")
    def checkTarget(self) -> Action:
        if self.type in HISTORY_ACTIONS:
            if not self.componentId or self.pageId or self.resultScopeId or self.context != 'live':
                raise ValueError('history action requires componentId without page or result context')
            return self
        if self.componentId is not None:
            raise ValueError('only history actions accept componentId')
        if (self.pageId is not None) != (self.type == "navigate"):
            raise ValueError("navigate requires pageId; other actions forbid pageId")
        if self.context == "displayed_result" and not self.resultScopeId:
            raise ValueError("details require explicit resultScopeId")
        if self.type == "freeze" and not self.resultScopeId:
            raise ValueError("freeze requires explicit resultScopeId")
        return self


ComponentType = Literal[
        "image", "text", "number", "indicator", "runtime_status", "table",
        "navigation_button", "container", "page_title", "page_navigation", "clock",
        "sqlite_table", "sqlite_value", "spacer"
    ]


class Component(Model):
    componentId: Id
    type: ComponentType
    version: Literal["1.0"] = "1.0"
    layout: Placement = Field(default_factory=Placement)
    responsive: ResponsiveItem = Field(default_factory=ResponsiveItem)
    props: Props = Field(default_factory=Props)
    bindings: dict[Id, Id] = Field(default_factory=dict)
    actions: dict[Literal["clicked"], Action] = Field(default_factory=dict)
    children: list[Component] = Field(default_factory=list, max_length=1024)
    grid: Grid | ResponsiveLayout = Field(default_factory=lambda: ResponsiveLayout(direction='wrap'))

    @model_validator(mode="before")
    @classmethod
    def spacerDefaults(cls, value):
        if isinstance(value, dict) and value.get("type") == "spacer" and "responsive" not in value:
            value = dict(value)
            value["responsive"] = {"width": "content", "minWidth": 24, "preferredWidth": 24,
                                   "minHeight": 24, "preferredHeight": 24, "alignSelf": "start"}
        elif (isinstance(value, dict) and isinstance(value.get('responsive', {}), dict)
              and value.get('type') in {'text', 'number', 'indicator', 'runtime_status',
                                        'page_title', 'clock', 'container'}):
            value = {**value, 'responsive': {'minWidth': 0, 'preferredWidth': 0, 'minHeight': 0,
                                               **value.get('responsive', {})}}
        return value

    @model_validator(mode="after")
    def checkShape(self) -> Component:
        if self.type != "spacer" and self.props.spacerMode != "fixed":
            raise ValueError("spacer mode requires spacer component")
        if self.props.imagePath is not None and (
                self.type != "image" or self.props.imageResourceId or self.bindings):
            raise ValueError("image path requires image component without static resource or result bindings")
        if self.props.imageResourceId and (self.type != "image" or self.bindings):
            raise ValueError("static image requires image component without result bindings")
        if self.props.iconResourceId and self.type != "navigation_button":
            raise ValueError("static icon requires navigation_button")
        if self.type != "container" and self.children:
            raise ValueError("only container can have children")
        if self.props.sqlite != SqliteSource() and self.type not in {"sqlite_table", "sqlite_value"}:
            raise ValueError("SQLite source requires a SQLite component")
        history = self.props.historySource
        if history is not None:
            if self.type not in {'image', 'sqlite_table', 'sqlite_value'}:
                raise ValueError('historySource requires image, sqlite_table or sqlite_value')
            if history.sourceComponentId is not None and self.type != 'image':
                raise ValueError('only a selected-record image accepts a history table instance')
            if (self.bindings or self.props.imageResourceId or self.props.imagePath is not None
                    or self.props.sqlite != SqliteSource()):
                raise ValueError('historySource is mutually exclusive with an owned, bound or static source')
        allowed = {"image": {"image"}, "table": {"rows"}, "container": set(),
                   "navigation_button": set(), "page_title": set(), "page_navigation": set(),
                   "clock": set(), "sqlite_table": set(), "sqlite_value": set(), "spacer": set()}.get(self.type, {"value"})
        if set(self.bindings) - allowed:
            raise ValueError(f"unsupported binding property for {self.type}")
        if self.actions and self.type != "navigation_button":
            raise ValueError("actions require navigation_button")
        if self.props.recordValue is not None and (self.type not in {'text', 'number', 'indicator'} or self.bindings):
            raise ValueError('recordValue requires a text, number or indicator without output bindings')
        if self.props.historyView is not None and self.type != 'sqlite_table':
            raise ValueError('historyView requires sqlite_table')
        if self.props.collapse is not None and self.type != 'container':
            raise ValueError('collapse requires a container')
        if self.props.statusField is not None and (self.type != 'runtime_status' or self.bindings):
            raise ValueError('statusField requires an unbound runtime_status')
        if self.props.imageView is not None and self.type != 'image':
            raise ValueError('imageView requires an image')
        return self


def walkComponents(components: list[Component]):
    for component in components:
        yield component
        yield from walkComponents(component.children)


class Page(Model):
    name: str = Field(min_length=1, max_length=256)
    layout: Grid | ResponsiveLayout = Field(default_factory=ResponsiveLayout)
    canvasTop: int = Field(default=0, ge=-32768, le=0)
    components: list[Component] = Field(default_factory=list, max_length=1024)
    resultScopeIds: list[Id] = Field(default_factory=list)
    targets: list[Literal["desktop", "web"]] = Field(default=["desktop"], min_length=1, max_length=2)

    @field_validator("targets")
    @classmethod
    def checkTargets(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("page targets must be unique")
        return [target for target in ("desktop", "web") if target in value]

    @model_serializer(mode="wrap")
    def serializeTargets(self, handler):
        payload = handler(self)
        if self.targets == ["desktop"]:
            payload.pop("targets", None)
        if not self.canvasTop:
            payload.pop('canvasTop', None)
        return payload


class Navigation(Model):
    mode: Literal["tabs", "buttons"] = "tabs"


class PageAppearance(Model):
    theme: Literal['light'] = 'light'
    contentMaxWidth: int = Field(default=0, ge=0, le=4096)
    pagePadding: int = Field(default=24, ge=0, le=64)


class ControlAppearance(Model):
    style: Literal['primary', 'secondary', 'link'] | None = None
    text: TextRole | None = None
    radius: int | None = Field(default=None, ge=0, le=32)
    padding: Padding | None = None
    minWidth: int | None = Field(default=None, ge=0, le=512)
    minHeight: int | None = Field(default=None, ge=0, le=128)
    showIcon: bool = False
    background: Literal['transparent'] | HexColor | None = None
    borderColor: HexColor | None = None
    borderWidth: BorderWidth | None = None
    borderStyle: BorderStyle | None = None
    iconPosition: Literal['left', 'right'] | None = None
    iconSize: int | None = Field(default=None, ge=8, le=64)
    iconGap: int | None = Field(default=None, ge=0, le=32)
    buttonStates: ButtonStates | None = None
    customStyle: CustomStyle | None = None


class HostControl(Model):
    label: str | None = Field(default=None, min_length=1, max_length=256)
    appearance: ControlAppearance | None = None


class PageShell(Model):
    title: str = Field(default='', max_length=1024)
    subtitle: str = Field(default='', max_length=1024)
    logoResourceId: Id | None = None
    logoWidth: int = Field(default=40, ge=1, le=512)
    logoHeight: int = Field(default=40, ge=1, le=256)
    headerSurface: Surface | None = None
    headerTypography: Typography | None = None
    startControl: HostControl | None = None
    stopControl: HostControl | None = None
    diagnosticsInitiallyExpanded: bool = False
    showConnection: bool = True
    showJobState: bool = True
    showControls: bool = True


class Presentation(Model):
    schemaVersion: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    defaultPageId: Id | None = None
    pageOrder: list[Id] = Field(default_factory=list, max_length=128)
    navigation: Navigation = Field(default_factory=Navigation)
    appearance: PageAppearance | None = None
    shell: PageShell | None = None
    resultScopes: dict[Id, ResultScope] = Field(default_factory=dict)
    dataSources: dict[Id, DataSource] = Field(default_factory=dict)
    pages: dict[Id, Page] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def legacyLayouts(cls, value):
        if not isinstance(value, dict) or value.get('schemaVersion', '1.1') != '1.0':
            return value
        from copy import deepcopy
        value = deepcopy(value)
        seen = set()
        def visit(children):
            for child in children:
                if not isinstance(child, dict) or id(child) in seen:
                    continue
                seen.add(id(child))
                child.setdefault('grid', {'type': 'grid'})
                visit(child.get('children', []))
        for page in value.get('pages', {}).values():
            if isinstance(page, dict):
                page.setdefault('layout', {'type': 'grid'})
                visit(page.get('components', []))
        return value

    @model_validator(mode="after")
    def checkStructure(self) -> Presentation:
        if len(set(self.pageOrder)) != len(self.pageOrder) or set(self.pageOrder) != set(self.pages):
            raise ValueError("pageOrder must contain every page exactly once")
        if self.pages and self.defaultPageId not in self.pages:
            raise ValueError("defaultPageId must reference a page")
        if not self.pages and self.defaultPageId is not None:
            raise ValueError("empty presentation cannot have defaultPageId")
        seen: set[str] = set()

        def visit(children: list[Component], layout: Grid | ResponsiveLayout, depth: int) -> None:
            if depth > 8:
                raise ValueError("component nesting exceeds 8")
            if self.schemaVersion == '1.0' and isinstance(layout, ResponsiveLayout):
                raise ValueError('responsive layout requires presentation schemaVersion 1.1')
            occupied: set[tuple[int, int]] = set()
            for child in children:
                if child.responsive.canvasOffsetY and (depth > 1 or isinstance(layout, Grid)):
                    raise ValueError('canvas offset requires a root responsive component')
                if child.componentId in seen:
                    raise ValueError("duplicate componentId")
                seen.add(child.componentId)
                if len(seen) > 1024:
                    raise ValueError("component count exceeds 1024")
                box = child.layout
                if isinstance(layout, Grid):
                    if box.column + box.columnSpan > layout.columns:
                        raise ValueError("component exceeds grid columns")
                    cells = {(r, c) for r in range(box.row, box.row + box.rowSpan)
                             for c in range(box.column, box.column + box.columnSpan)}
                    if cells & occupied:
                        raise ValueError("components overlap")
                    occupied.update(cells)
                if child.type == 'container':
                    visit(child.children, child.grid, depth + 1)

        for page in self.pages.values():
            if page.canvasTop and (self.schemaVersion not in {'1.2', '1.3'} or not isinstance(page.layout, ResponsiveLayout)):
                raise ValueError('expanded canvas requires a responsive page')
            if any(item.responsive.canvasOffsetY < page.canvasTop for item in page.components):
                raise ValueError('component canvas offset exceeds page canvasTop')
            if self.schemaVersion not in {'1.2', '1.3'}:
                if page.targets != ['desktop']:
                    raise ValueError('page targets require presentation schemaVersion 1.2')
                for component in walkComponents(page.components):
                    if component.props.sqlite.location == 'runtime' or (
                            component.props.imagePath is not None and component.props.imagePath.location == 'runtime'):
                        raise ValueError('Runtime data location requires presentation schemaVersion 1.2')
            visit(page.components, page.layout, 1)
            instances = {component.componentId: component for component in walkComponents(page.components)}
            for component in instances.values():
                history = component.props.historySource
                if history is None or history.sourceComponentId is None:
                    continue
                table = instances.get(history.sourceComponentId)
                if table is None or table.type != 'sqlite_table' or table.props.historySource is None:
                    raise ValueError('historySource.sourceComponentId requires a same-page referenced history table')
        return self
