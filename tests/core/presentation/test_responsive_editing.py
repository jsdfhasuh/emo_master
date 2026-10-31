"""Ordered editing preserves identities, selection scope and project undo."""
import pytest

from emo_master.apps.designer.page_designer.responsive_editing import ResponsiveCommands
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Component, Page, Presentation, ResponsiveLayout, walkComponents
from emo_master.core.presentation.responsive import Measurement, solveResponsive


def text(key, **kwargs):
    return Component(componentId=key, type='text', props={'text': 'Value ' + key}, **kwargs)


def sessionFor(components, direction='vertical'):
    payload = WorkflowStore().toPayload('Responsive editing')
    payload['presentation'] = Presentation(defaultPageId='p', pageOrder=['p'], pages={
        'p': Page(name='Page', layout=ResponsiveLayout(direction=direction), components=components)
    }).model_dump(mode='json')
    session = ProjectEditSession(payload)
    assert session.document().schemaVersion == '2.9'
    assert session.document().presentation.schemaVersion == '1.2'
    return session, ResponsiveCommands(session)


def pageOf(session):
    return session.document().presentation.pages['p']


def ids(children):
    return [item.componentId for item in children]


def allItems(page):
    return {item.componentId: item for item in walkComponents(page.components)}


@pytest.mark.parametrize('keys,index,expected', [
    (['b'], 0, ['b', 'a', 'c', 'd']),
    (['b'], 3, ['a', 'c', 'b', 'd']),
    (['b'], 4, ['a', 'c', 'd', 'b']),
    (['d'], 0, ['d', 'a', 'b', 'c']),
    (['d', 'b'], 0, ['b', 'd', 'a', 'c']),
    (['b', 'c'], 3, ['a', 'b', 'c', 'd']),
])
def testReorderUsesOriginalInsertionBoundaryAndStableSelectionOrder(keys, index, expected):
    session, commands = sessionFor([text(key) for key in 'abcd'])
    before = session.payload()
    original = allItems(pageOf(session))

    selected = commands.place('p', keys=keys, index=index)

    after = session.payload()
    assert ids(pageOf(session).components) == expected
    assert selected == [key for key in 'abcd' if key in keys]
    assert allItems(pageOf(session)) == original
    changed = expected != list('abcd')
    assert len(session._undo) == int(changed)
    assert session.dirty == changed
    if changed:
        assert session.undo() and session.payload() == before
        assert not session.undo()
        assert session.redo() and session.payload() == after


def testCrossContainerInsertMovesOriginalComponentAndRetainsSourceSiblings():
    left = Component(componentId='left', type='container', children=[text('a'), text('b')])
    right = Component(componentId='right', type='container', children=[text('c'), text('d')])
    session, commands = sessionFor([left, right, text('outside')])
    before = session.payload()
    moved = allItems(pageOf(session))['b'].model_dump()

    assert commands.place('p', keys=['b'], parentId='right', index=1) == ['b']

    items = allItems(pageOf(session))
    assert ids(items['left'].children) == ['a']
    assert ids(items['right'].children) == ['c', 'b', 'd']
    assert items['b'].model_dump() == moved
    assert ids(pageOf(session).components) == ['left', 'right', 'outside']
    assert len(session._undo) == 1
    assert session.undo() and session.payload() == before


@pytest.mark.parametrize('target', [dict(parentId='outer'), dict(parentId='inner'),
                                    dict(besideId='inside', direction='horizontal')])
def testMovingContainerIntoItsOwnSubtreeRejectsWithoutLosingContent(target):
    outer = Component(componentId='outer', type='container', children=[
        Component(componentId='inner', type='container', children=[text('inside')])])
    session, commands = sessionFor([outer, text('outside')])
    before = session.payload()

    with pytest.raises(ValueError, match='自身或子组件'):
        commands.place('p', keys=['outer'], **target)

    assert session.payload() == before
    assert not session.dirty and not session._undo


@pytest.mark.parametrize('beforeTarget', [False, True])
def testBesideDropCreatesLocalGroupWithStableExistingIds(beforeTarget):
    session, commands = sessionFor([text('a'), text('b'), text('c')])
    original = allItems(pageOf(session))

    assert commands.place('p', keys=['c'], besideId='a', direction='horizontal',
                          before=beforeTarget) == ['c']

    page = pageOf(session)
    group = page.components[0]
    assert group.type == 'container' and group.grid.direction == 'horizontal'
    assert group.componentId not in original
    assert ids(group.children) == (['c', 'a'] if beforeTarget else ['a', 'c'])
    assert ids(page.components[1:]) == ['b']
    for key, item in original.items():
        assert allItems(page)[key] == item
    assert len(session._undo) == 1


def testGroupAndUngroupRetainLocalReferencesAndComponentProperties():
    table = Component(componentId='history', type='sqlite_table', props={
        'sqlite': {'databasePath': 'results.db', 'table': 'results', 'columns': ['id']}})
    image = Component(componentId='picture', type='image', props={
        'imagePath': {'kind': 'selected_row', 'componentId': 'history', 'field': 'image_path'}})
    session, commands = sessionFor([text('before'), table, image, text('after')])
    before = session.payload()

    groupId = commands.group('p', ['picture', 'history'], 'horizontal')

    grouped = session.payload()
    group = allItems(pageOf(session))[groupId]
    assert ids(pageOf(session).components) == ['before', groupId, 'after']
    assert group.grid.direction == 'horizontal'
    assert group.children == [table, image]
    assert allItems(pageOf(session))['picture'].props.imagePath.componentId == 'history'
    assert commands.ungroup('p', groupId) == ['history', 'picture']
    assert session.payload() == before
    assert len(session._undo) == 2
    assert session.undo() and session.payload() == grouped
    assert session.undo() and session.payload() == before
    assert session.redo() and session.redo() and session.payload() == before


def testGroupAcrossContainersCollectsSelectedObjectsOnceAndPreservesTheirOrder():
    nested = Component(componentId='nested', type='container', children=[text('inside')])
    left = Component(componentId='left', type='container', children=[text('a'), nested])
    right = Component(componentId='right', type='container', children=[text('b'), text('c')])
    session, commands = sessionFor([left, right])

    groupId = commands.group('p', ['c', 'inside', 'nested'], 'vertical')

    items = allItems(pageOf(session))
    assert ids(items[groupId].children) == ['nested', 'c']
    assert ids(items['nested'].children) == ['inside']
    assert ids(items['left'].children) == ['a']
    assert ids(items['right'].children) == ['b']
    assert len(list(walkComponents(pageOf(session).components))) == len(items)
    assert len(session._undo) == 1


@pytest.mark.parametrize('direction,alignment,value', [
    ('vertical', 'left', 'start'), ('vertical', 'hcenter', 'center'),
    ('vertical', 'right', 'end'), ('horizontal', 'top', 'start'),
    ('horizontal', 'vcenter', 'center'), ('horizontal', 'bottom', 'end'),
])
def testCrossAxisAlignmentChangesOnlySelectedItemRules(direction, alignment, value):
    session, commands = sessionFor([text(key) for key in 'abc'], direction)
    original = pageOf(session)

    commands.arrange('p', ['a', 'c'], alignment=alignment)

    page = pageOf(session)
    assert page.components[1] == original.components[1]
    assert page.layout == original.layout
    assert ids(page.components) == ['a', 'b', 'c']
    for index in (0, 2):
        assert page.components[index].responsive.alignSelf == value
        assert page.components[index].props == original.components[index].props
        assert page.components[index].componentId == original.components[index].componentId
        if direction == 'vertical':
            assert page.components[index].responsive.width == 'content'
    assert len(session._undo) == 1
    commands.arrange('p', ['a', 'c'], alignment=alignment)
    assert len(session._undo) == 1  # Already satisfied; no extra undo step.
    assert session.undo() and pageOf(session) == original


def testMainAxisAlignmentRequiresWholeGroupAndPreservesChildren():
    session, commands = sessionFor([text(key) for key in 'abc'], 'horizontal')
    before = session.payload()
    original = pageOf(session)
    with pytest.raises(ValueError, match='整个排列组'):
        commands.arrange('p', ['a', 'b'], alignment='hcenter')
    assert session.payload() == before and not session._undo

    commands.arrange('p', ['a', 'b', 'c'], alignment='hcenter')

    assert pageOf(session).layout.justify == 'center'
    assert pageOf(session).components == original.components
    assert len(session._undo) == 1
    assert session.undo() and session.payload() == before


@pytest.mark.parametrize('direction,axis', [('vertical', 'vertical'), ('horizontal', 'horizontal')])
def testWholeGroupDistributionPersistsEqualGapsAtDifferentAvailableSizes(direction, axis):
    cards = [text(key, responsive={'width': 'content', 'minWidth': 0,
        'preferredWidth': width, 'minHeight': 0, 'preferredHeight': height})
        for key, width, height in [('a', 100, 70), ('b', 140, 90), ('c', 80, 110)]]
    session, commands = sessionFor(cards, direction)
    before = session.payload()
    with pytest.raises(ValueError):
        commands.arrange('p', ['a', 'b'], distribute=axis)
    assert session.payload() == before and not session._undo

    commands.arrange('p', ['a', 'b', 'c'], distribute=axis)

    page = pageOf(session)
    assert page.layout.justify == 'space-between'
    assert page.components == cards
    measurements = [Measurement(c.componentId, c.responsive) for c in cards]
    for size in (600, 900):
        boxes, _height = solveResponsive(page.layout, measurements, size, size)
        if axis == 'horizontal':
            gaps = [right.x - (left.x + left.width) for left, right in zip(boxes, boxes[1:])]
            assert boxes[0].x == 0
            assert boxes[-1].x + boxes[-1].width == pytest.approx(size)
        else:
            gaps = [right.y - (left.y + left.height) for left, right in zip(boxes, boxes[1:])]
            assert boxes[0].y == 0
            assert boxes[-1].y + boxes[-1].height == pytest.approx(size)
        assert gaps[0] == pytest.approx(gaps[1])
    assert len(session._undo) == 1
    commands.arrange('p', ['a', 'b', 'c'], distribute=axis)
    assert len(session._undo) == 1
    assert session.undo() and session.payload() == before


def testPartialAndWrongAxisDistributionRejectWithoutChangingAnyRules():
    session, commands = sessionFor([text(key) for key in 'abcd'], 'horizontal')
    before = session.payload()
    for keys, axis in [(['a', 'b', 'c'], 'horizontal'), (list('abcd'), 'vertical')]:
        with pytest.raises(ValueError):
            commands.arrange('p', keys, distribute=axis)
        assert session.payload() == before and not session._undo
