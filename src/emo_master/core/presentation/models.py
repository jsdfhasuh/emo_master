from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Id = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^\S+$")]
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

    kind: Literal["node_output", "workflow_output", "global_counter", "runtime_status"]
    resultScopeId: Id
    workflowId: Id | None = None
    callPath: list[CallStep] = Field(default_factory=list, max_length=32)
    nodeId: Id | None = None
    port: Id | None = None
    fieldPath: list[Id] = Field(default_factory=list, max_length=16)
    expectedType: ValueType
    name: Id | None = None
    ruleVersion: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def checkAddress(self) -> DataSource:
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


class Placement(Model):
    row: int = Field(default=0, ge=0, le=4095)
    column: int = Field(default=0, ge=0, le=23)
    rowSpan: int = Field(default=1, ge=1, le=128)
    columnSpan: int = Field(default=1, ge=1, le=24)


class Props(Model):
    title: str = Field(default="", max_length=1024)
    text: str = Field(default="", max_length=4096)
    emptyText: str = Field(default="—", max_length=256)
    unit: str = Field(default="", max_length=64)
    decimals: int = Field(default=2, ge=0, le=12)


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


class Component(Model):
    componentId: Id
    type: Literal[
        "image", "text", "number", "indicator", "runtime_status", "table",
        "navigation_button", "container"
    ]
    version: Literal["1.0"] = "1.0"
    layout: Placement = Field(default_factory=Placement)
    props: Props = Field(default_factory=Props)
    bindings: dict[Id, Id] = Field(default_factory=dict)
    actions: dict[Literal["clicked"], Action] = Field(default_factory=dict)
    children: list[Component] = Field(default_factory=list, max_length=1024)
    grid: Grid = Field(default_factory=Grid)

    @model_validator(mode="after")
    def checkShape(self) -> Component:
        if self.type != "container" and self.children:
            raise ValueError("only container can have children")
        allowed = {"image": {"image"}, "table": {"rows"}, "container": set(),
                   "navigation_button": set()}.get(self.type, {"value"})
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
    layout: Grid = Field(default_factory=Grid)
    components: list[Component] = Field(default_factory=list, max_length=1024)
    resultScopeIds: list[Id] = Field(default_factory=list)


class Navigation(Model):
    mode: Literal["tabs", "buttons"] = "tabs"


class Presentation(Model):
    schemaVersion: Literal["1.0"] = "1.0"
    defaultPageId: Id | None = None
    pageOrder: list[Id] = Field(default_factory=list, max_length=128)
    navigation: Navigation = Field(default_factory=Navigation)
    resultScopes: dict[Id, ResultScope] = Field(default_factory=dict)
    dataSources: dict[Id, DataSource] = Field(default_factory=dict)
    pages: dict[Id, Page] = Field(default_factory=dict)

    @model_validator(mode="after")
    def checkStructure(self) -> Presentation:
        if len(set(self.pageOrder)) != len(self.pageOrder) or set(self.pageOrder) != set(self.pages):
            raise ValueError("pageOrder must contain every page exactly once")
        if self.pages and self.defaultPageId not in self.pages:
            raise ValueError("defaultPageId must reference a page")
        if not self.pages and self.defaultPageId is not None:
            raise ValueError("empty presentation cannot have defaultPageId")
        seen: set[str] = set()

        def visit(children: list[Component], columns: int, depth: int) -> None:
            if depth > 8:
                raise ValueError("component nesting exceeds 8")
            occupied: set[tuple[int, int]] = set()
            for child in children:
                if child.componentId in seen:
                    raise ValueError("duplicate componentId")
                seen.add(child.componentId)
                if len(seen) > 1024:
                    raise ValueError("component count exceeds 1024")
                box = child.layout
                if box.column + box.columnSpan > columns:
                    raise ValueError("component exceeds grid columns")
                cells = {(r, c) for r in range(box.row, box.row + box.rowSpan)
                         for c in range(box.column, box.column + box.columnSpan)}
                if cells & occupied:
                    raise ValueError("components overlap")
                occupied.update(cells)
                visit(child.children, child.grid.columns, depth + 1)

        for page in self.pages.values():
            visit(page.components, page.layout.columns, 1)
        return self
