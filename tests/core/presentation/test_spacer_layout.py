"""Spacer sizes are shared layout semantics, not visible placeholder content."""
import pytest
from pydantic import ValidationError

from emo_master.core.presentation.models import Component, ResponsiveItem, ResponsiveLayout
from emo_master.core.presentation.responsive import Measurement, solveResponsive
from emo_panel_protocol.published import PublishedComponent


def spacer(key='s', mode='fixed', weight=1):
    component = Component(componentId=key, type='spacer', props={'spacerMode': mode})
    item = component.responsive.model_copy(update={'weight': weight})
    return Measurement(key, item, 24, 24, spacerMode=mode)


def testSpacerDefaultsSerializeAndRejectDataOrActions():
    component = Component(componentId='s', type='spacer')
    assert component.responsive.preferredWidth == component.responsive.preferredHeight == 24
    assert 'spacerMode' not in component.model_dump()['props']
    flex = Component(componentId='s', type='spacer', props={'spacerMode': 'flex'})
    assert flex.model_dump()['props']['spacerMode'] == 'flex'
    assert Component.model_validate(flex.model_dump()) == flex
    assert PublishedComponent.model_validate(flex.model_dump()).props.spacerMode == 'flex'
    assert 'spacerMode' not in PublishedComponent.model_validate(component.model_dump()).model_dump()['props']
    for fields in ({'bindings': {'value': 'data'}}, {'children': [component.model_dump()]},
                   {'actions': {'clicked': {'type': 'resume_live'}}}):
        with pytest.raises(ValidationError):
            Component(componentId='bad', type='spacer', **fields)


@pytest.mark.parametrize('direction', ['vertical', 'horizontal', 'wrap'])
def testFixedSpacerDoesNotStretchOrAdoptEqualWidth(direction):
    normal = Measurement('normal', ResponsiveItem(minWidth=80, preferredWidth=80), 80, 64)
    boxes, _ = solveResponsive(ResponsiveLayout(direction=direction, equalWidth=True),
                               [normal, spacer()], 400, 300)
    assert boxes[1].width == boxes[1].height == 24
    narrow, _ = solveResponsive(ResponsiveLayout(direction=direction), [spacer()], 12, 100)
    assert narrow[0].width == 12


def testVerticalFlexOnlyConsumesFiniteRemainingHeightByWeight():
    items = [spacer('a', 'flex', 1), spacer('b', 'flex', 3)]
    rules = ResponsiveLayout(spacing=0)
    boxes, minimum = solveResponsive(rules, items, 300, 248)
    assert [box.height for box in boxes] == [74, 174]
    assert minimum == 48
    boxes, _ = solveResponsive(rules, items, 300)
    assert [box.height for box in boxes] == [24, 24]
    boxes, _ = solveResponsive(rules, items, 300, 20)
    assert [box.height for box in boxes] == [24, 24]


@pytest.mark.parametrize('direction', ['horizontal', 'wrap'])
def testRowFlexSharesRemainderAndWrapDoesNotConsumeOtherRows(direction):
    rules = ResponsiveLayout(direction=direction, spacing=0)
    items = [spacer('a', 'flex', 1), spacer('b', 'flex', 3)]
    boxes, _ = solveResponsive(rules, items, 248)
    assert [box.width for box in boxes] == [74, 174]
    assert [box.height for box in boxes] == [24, 24]
    boxes, _ = solveResponsive(rules, items, 30)
    assert [box.width for box in boxes] == [30, 30]
    assert boxes[0].y == 0 and boxes[1].y == 24


def testPublishedSpacersKeepModeWithoutAddingCaptureSources():
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.core.presentation.models import Page, Presentation
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.project.web_panel import publishedSnapshot
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    document.presentation = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Web spacers', targets=['web'], components=[
            Component(componentId='s', type='spacer')])})
    fixed, capture, requirements, _history = publishedSnapshot(document, manifests={})
    assert 'component.spacer@1.0' in requirements
    assert 'spacerMode' not in fixed['pages']['p']['components'][0]['props']
    document.presentation.pages['p'].components[0].props.spacerMode = 'flex'
    flexible, after, _requirements, _history = publishedSnapshot(document, manifests={})
    assert flexible['pages']['p']['components'][0]['props']['spacerMode'] == 'flex'
    assert after == capture
    assert flexible['dataSources'] == {} and flexible['resultScopes'] == {}
