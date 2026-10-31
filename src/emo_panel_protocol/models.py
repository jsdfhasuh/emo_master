from __future__ import annotations

from typing import Annotated, Literal
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer


Id = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^\S+$")]
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


class Placement(Model):
    row: int = Field(default=0, ge=0, le=4095)
    column: int = Field(default=0, ge=0, le=23)
    rowSpan: int = Field(default=1, ge=1, le=128)
    columnSpan: int = Field(default=1, ge=1, le=24)


class IndicatorStyle(Model):
    text: str = Field(max_length=256)
    color: Literal['neutral', 'green', 'red', 'amber'] = 'neutral'


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
                self.databasePath, self.table, self.orderBy, self.valueColumn, *self.columns)):
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


class VisualProps(Model):
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
    fontFamily: Literal["system", "sans", "serif", "monospace"] = "system"
    fontSize: int = Field(default=0, ge=0, le=48)
    fontWeight: Literal["normal", "bold"] = "normal"
    textColor: Literal["default", "neutral", "green", "red", "amber"] = "default"
    cardStyle: Literal["plain", "soft", "outlined"] = "plain"

    @field_validator('fontSize')
    @classmethod
    def checkFontSize(cls, value: int) -> int:
        if 0 < value < 8:
            raise ValueError('fontSize must be 0 (automatic) or 8–48 pixels')
        return value

    @model_validator(mode='after')
    def checkStates(self) -> VisualProps:
        for key in self.indicatorStates:
            if len(key) > 256:
                raise ValueError('indicator key too long')
            value = json.loads(key)
            if type(value) not in (bool, str) or json.dumps(value, ensure_ascii=False) != key:
                raise ValueError('indicator keys must be canonical JSON boolean/string literals')
        return self


class Props(VisualProps):
    imagePath: ImagePathSource | None = None
    sqlite: SqliteSource = Field(default_factory=SqliteSource)

    @model_serializer(mode="wrap")
    def serializeResources(self, handler):
        payload = handler(self)
        for field in ("imageResourceId", "iconResourceId", "imagePath"):
            if payload.get(field) is None:
                payload.pop(field, None)
        if self.sqlite == SqliteSource():
            payload.pop("sqlite", None)
        return payload

class Action(Model):
    type: Literal["navigate", "freeze", "resume_live"]
    pageId: Id | None = None
    context: Literal["live", "displayed_result"] = "live"
    resultScopeId: Id | None = None

    @model_validator(mode="after")
    def checkTarget(self) -> Action:
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
        "sqlite_table", "sqlite_value"
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

    @model_validator(mode="after")
    def checkShape(self) -> Component:
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
        allowed = {"image": {"image"}, "table": {"rows"}, "container": set(),
                   "navigation_button": set(), "page_title": set(), "page_navigation": set(),
                   "clock": set(), "sqlite_table": set(), "sqlite_value": set()}.get(self.type, {"value"})
        if set(self.bindings) - allowed:
            raise ValueError(f"unsupported binding property for {self.type}")
        if self.actions and self.type != "navigation_button":
            raise ValueError("actions require navigation_button")
        return self


def walkComponents(components: list[Component]):
    for component in components:
        yield component
        yield from walkComponents(component.children)


class Page(Model):
    name: str = Field(min_length=1, max_length=256)
    layout: Grid | ResponsiveLayout = Field(default_factory=ResponsiveLayout)
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
        return payload


class Navigation(Model):
    mode: Literal["tabs", "buttons"] = "tabs"


class Presentation(Model):
    schemaVersion: Literal["1.0", "1.1", "1.2"] = "1.2"
    defaultPageId: Id | None = None
    pageOrder: list[Id] = Field(default_factory=list, max_length=128)
    navigation: Navigation = Field(default_factory=Navigation)
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
            if self.schemaVersion != '1.2':
                if page.targets != ['desktop']:
                    raise ValueError('page targets require presentation schemaVersion 1.2')
                for component in walkComponents(page.components):
                    if component.props.sqlite.location == 'runtime' or (
                            component.props.imagePath is not None and component.props.imagePath.location == 'runtime'):
                        raise ValueError('Runtime data location requires presentation schemaVersion 1.2')
            visit(page.components, page.layout, 1)
        return self
