"""Pure adapters for bounded page properties; no Qt or Runtime interaction."""
from __future__ import annotations

import json
from collections.abc import Iterable

from emo_master.core.contracts.port_types import normalizePortSpec, normalizePortType
from emo_master.core.presentation.catalog import OutputEntry, sourceType
from emo_master.core.presentation.models import (
    Action, DataSource, IndicatorStyle, Presentation, Props, TableColumn,
)
from emo_master.core.presentation.validation import pageScopes


def encodeIndicatorKey(kind: str, value: bool | str) -> str:
    """Encode a typed editor value, never treating plain string input as JSON.

    Boolean editors may supply a bool or the exact text ``true``/``false``.
    Strings, including empty strings and strings containing quotes, are literal.
    """
    if kind == 'boolean':
        if type(value) is str and value in ('true', 'false'):
            value = value == 'true'
        if type(value) is not bool:
            raise ValueError('boolean indicator value must be true or false')
    elif kind == 'string':
        if type(value) is not str:
            raise ValueError('string indicator value must be text')
    else:
        raise ValueError('indicator type must be boolean or string')
    key = json.dumps(value, ensure_ascii=False)
    if len(key) > 256:
        raise ValueError('indicator key too long')
    return key


def decodeIndicatorKey(key: str) -> tuple[str, bool | str]:
    """Return the editor type and value for an existing canonical model key."""
    if type(key) is not str or len(key) > 256:
        raise ValueError('indicator key must be canonical JSON boolean/string literal')
    try:
        value = json.loads(key)
    except (TypeError, ValueError) as error:
        raise ValueError('indicator key must be canonical JSON boolean/string literal') from error
    if type(value) not in (bool, str) or json.dumps(value, ensure_ascii=False) != key:
        raise ValueError('indicator key must be canonical JSON boolean/string literal')
    return ('boolean' if type(value) is bool else 'string', value)


def indicatorStatesFromRows(
    rows: Iterable[tuple[str, bool | str, str, str]],
) -> dict[str, IndicatorStyle]:
    """Validate typed (kind, value, display text, color) rows without data loss.

    Empty display text and empty string values are meaningful. Callers should
    remove explicitly deleted rows rather than filter values by truthiness.
    """
    states: dict[str, IndicatorStyle] = {}
    for kind, value, label, color in rows:
        if len(states) >= 16:
            raise ValueError('indicator mappings cannot exceed 16 rows')
        key = encodeIndicatorKey(kind, value)
        if key in states:
            raise ValueError(f'duplicate indicator value: {key}')
        states[key] = IndicatorStyle.model_validate({'text': label, 'color': color})
    return Props(indicatorStates=states).indicatorStates


def tableFieldChoices(source: DataSource, entries: list[OutputEntry]) -> list[TableColumn]:
    """Derive row-relative fields solely from a trusted static output catalog.

    Only the catalog's known Blob/Detection collection fields are supported.
    Generic JSON, list item schemas, and runtime examples are never inferred.
    Already-projected collections contain scalar rows, so their column path is
    empty: the renderer must not apply the server's projection a second time.
    """
    if source.expectedType != 'collection' or sourceType(source, entries) != 'collection':
        raise ValueError('table fields require a collection source')
    entry = next((item for item in entries if (item.workflowId, item.nodeId, item.port) ==
                 (source.workflowId, source.nodeId, source.port)), None)
    if entry is None:
        raise ValueError('table source is absent from the trusted output catalog')
    kind = normalizePortType(normalizePortSpec(entry.spec))
    if kind not in {'blobCollection', 'detectionCollection'}:
        raise ValueError(f'automatic table fields are unsupported for schema: {kind}')
    fields = [path for path, valueType in entry.fields.items()
              if valueType == 'collection' and len(path) > 2 and path[:2] == ('items', '*')]
    if not fields:
        raise ValueError('automatic table fields are unsupported for this collection schema')
    if source.fieldPath:
        path = tuple(source.fieldPath)
        if path not in fields:
            raise ValueError('automatic table fields are unsupported for this projection')
        return [TableColumn(title='.'.join(path[2:]), fieldPath=[])]
    return [TableColumn(title='.'.join(path[2:]), fieldPath=list(path[2:])) for path in fields]


def actionFromFields(
    presentation: Presentation,
    pageId: str,
    mode: str,
    targetPageId: str | None = None,
    resultScopeId: str | None = None,
) -> Action | None:
    """Build a read-only Action from UI fields with explicit target/scope checks.

    This only edits configuration. Selecting detail/freeze does not fetch a
    result: existing runtime navigation resolves the GUI's displayed resultKey.
    """
    if pageId not in presentation.pages:
        raise ValueError('current page is missing')
    if mode not in {'none', 'navigate', 'detail', 'freeze', 'resume_live'}:
        raise ValueError('unsupported page action')
    if mode not in {'navigate', 'detail'} and targetPageId is not None:
        raise ValueError('only navigation/detail actions accept a target page')
    if mode not in {'detail', 'freeze'} and resultScopeId is not None:
        raise ValueError('only detail/freeze actions accept a result scope')
    if mode == 'none':
        return None
    if mode == 'resume_live':
        return Action(type='resume_live')
    if mode in {'navigate', 'detail'} and targetPageId not in presentation.pages:
        raise ValueError('navigation target page is missing')
    if mode == 'navigate':
        return Action(type='navigate', pageId=targetPageId)
    if resultScopeId not in presentation.resultScopes:
        raise ValueError('detail/freeze requires an existing result scope')
    if resultScopeId not in pageScopes(presentation, pageId):
        raise ValueError('current page does not display selected result scope')
    if mode == 'detail':
        assert targetPageId is not None
        if resultScopeId not in pageScopes(presentation, targetPageId):
            raise ValueError('target page does not support selected result scope')
        return Action(type='navigate', pageId=targetPageId, context='displayed_result',
                      resultScopeId=resultScopeId)
    return Action(type='freeze', context='displayed_result', resultScopeId=resultScopeId)
