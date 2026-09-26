from __future__ import annotations

from collections.abc import Mapping

from emo_master.core.plugin.models import PluginManifest
from emo_master.core.project.models import ProjectDocument
from emo_master.core.presentation.catalog import (
    Issue, buildOutputCatalog, resolveCallPath, sourceType,
)
from emo_master.core.presentation.models import Presentation, walkComponents


def pageScopes(presentation: Presentation, pageId: str) -> set[str]:
    page = presentation.pages[pageId]
    result = set(page.resultScopeIds)
    for component in walkComponents(page.components):
        for sourceId in component.bindings.values():
            source = presentation.dataSources.get(sourceId)
            if source:
                result.add(source.resultScopeId)
    return result


def validateBindings(
    project: ProjectDocument, manifests: Mapping[str, PluginManifest], *,
    publish: bool = False, counterNames: frozenset[str] = frozenset(),
) -> list[Issue]:
    """Draft syntax is checked by models; invalid nonempty bindings block capture too."""
    presentation = project.presentation
    if presentation is None:
        return []
    issues: list[Issue] = []
    entries = buildOutputCatalog(project, manifests)

    def report(path: str, message: str, code: str = "binding_invalid") -> None:
        issues.append(Issue(path, code, message))

    for scopeId, scopeDefinition in presentation.resultScopes.items():
        try:
            if scopeDefinition.entryWorkflowId != project.entryWorkflowId:
                raise ValueError("scope must start at project entry workflow")
            if resolveCallPath(project, scopeDefinition.entryWorkflowId,
                               scopeDefinition.callPath) != scopeDefinition.scopeWorkflowId:
                raise ValueError("scope workflow does not match call path")
        except ValueError as error:
            report(f"presentation.resultScopes.{scopeId}", str(error), "scope_invalid")

    # Only referenced sources form the capture plan; unused historical sources are not collected.
    used = {sourceId for page in presentation.pages.values()
            for component in walkComponents(page.components) for sourceId in component.bindings.values()}
    for sourceId in sorted(used):
        source = presentation.dataSources.get(sourceId)
        path = f"presentation.dataSources.{sourceId}"
        if source is None:
            report(path, "data source missing")
            continue
        try:
            scope = presentation.resultScopes.get(source.resultScopeId)
            if scope is None:
                raise ValueError("result scope missing")
            if source.kind in {"node_output", "workflow_output"}:
                if resolveCallPath(project, scope.entryWorkflowId, source.callPath) != source.workflowId:
                    raise ValueError("output workflow does not match call path")
                prefix = scope.callPath
                if source.callPath[:len(prefix)] != prefix:
                    raise ValueError("source is outside result scope")
                if any(step.relation != "subflow" for step in source.callPath[len(prefix):]):
                    raise ValueError("repeated loop output requires its own iteration scope")
            if source.kind == "global_counter" and source.name not in counterNames:
                raise ValueError("counter name is not declared by Runtime")
            actual = sourceType(source, entries)
            if actual != source.expectedType and (actual, source.expectedType) != ("integer", "number"):
                raise ValueError(f"expected {source.expectedType}, output is {actual}")
        except ValueError as error:
            report(path, str(error))

    accepted = {"image": {"image"}, "number": {"integer", "number"},
                "text": {"integer", "number", "string", "boolean", "json"},
                "indicator": {"boolean", "string"}, "table": {"collection"},
                "runtime_status": {"string"}}
    for pageId, page in presentation.pages.items():
        for scopeId in page.resultScopeIds:
            if scopeId not in presentation.resultScopes:
                report(f"presentation.pages.{pageId}.resultScopeIds", "result scope missing")
        for component in walkComponents(page.components):
            path = f"presentation.pages.{pageId}.components.{component.componentId}"
            if publish and component.type in accepted and not component.bindings:
                report(path, "unbound component cannot be published", "unbound")
            for prop, sourceId in component.bindings.items():
                source = presentation.dataSources.get(sourceId)
                if source and source.expectedType not in accepted.get(component.type, set()):
                    report(f"{path}.bindings.{prop}", "source is incompatible with component")
                if source and component.type == "runtime_status" and source.kind != "runtime_status":
                    report(path, "runtime status must use the platform status source")
            for event, action in component.actions.items():
                actionPath = f"{path}.actions.{event}"
                if action.pageId and action.pageId not in presentation.pages:
                    report(actionPath, "navigation target missing", "navigation_invalid")
                if action.resultScopeId:
                    if action.resultScopeId not in pageScopes(presentation, pageId):
                        report(actionPath, "current page does not display selected result scope")
                    if action.pageId in presentation.pages and action.resultScopeId not in pageScopes(
                        presentation, action.pageId
                    ):
                        report(actionPath, "target page does not support selected result scope")
    return issues
