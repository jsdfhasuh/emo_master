from dataclasses import replace

import pytest

from emo_master.apps.designer.ui.flow_edge_presentation import (
    DIM_COLOR, ERROR_COLOR, FOCUS_COLOR, NORMAL_COLOR, SELECTED_COLOR,
    edgeStyle, focusEdges, splitLines, wireGeometry,
)
from emo_master.apps.designer.ui.flow_routing import Route

A = ("source", "value", "a", "in")
B = ("source", "value", "b", "in")
C = ("source", "other", "c", "in")
D = ("a", "out", "d", "in")


def testPartialOverlapIsSplitWithPreciseOwnersAndReversedLines():
    parts = splitLines([((0, 0), (30, 0), A), ((20, 0), (10, 0), B)])
    assert [(part.points, part.owners) for part in parts] == [
        (((0, 0), (10, 0)), {A}), (((10, 0), (20, 0)), {A, B}),
        (((20, 0), (30, 0)), {A}),
    ]


def testRepeatedOwnerAndZeroSegmentsDoNotLoseTheActiveInterval():
    parts = splitLines([((0, 0), (20, 0), A), ((10, 0), (30, 0), A),
                        ((10, 0), (10, 0), B)])
    assert all(part.owners == {A} for part in parts)
    assert parts[-1].points[-1] == (30, 0)
    with pytest.raises(ValueError, match="orthogonal"):
        splitLines([((0, 0), (1, 1), A)])


def testSameSignalPaintsCommonTrunkOnceWithTrueBranchDot():
    routes = {A: Route(((0, 0), (100, 0))), B: Route(((0, 0), (50, 0), (50, 80), (100, 80)))}
    geometry = wireGeometry(routes)
    assert any(part.points == ((0, 0), (50, 0)) and part.owners == {A, B} for part in geometry.parts)
    assert geometry.junctions == (((50, 0), frozenset((A, B))),)
    assert wireGeometry(dict(reversed(list(routes.items())))) == geometry


def testSharedRoundedElbowIsOneCurveAndDifferentSignalsNeverMerge():
    points = ((0, 0), (50, 0), (50, 80), (100, 80))
    geometry = wireGeometry({A: Route(points), B: Route(points), C: Route(points)})
    curves = [part for part in geometry.parts if len(part.points) == 3]
    assert len(curves) == 4
    assert sum(part.owners == {A, B} for part in curves) == 2
    assert all(not ({A, C} <= part.owners) for part in geometry.parts)
    assert not geometry.junctions


def testDiagnosticStatesNeverShareOrHideBlockedGeometry():
    good = Route(((0, 0), (100, 0)))
    geometry = wireGeometry({A: good, B: replace(good, blocked=True, reason="blocked")})
    assert len(geometry.parts) == 2
    assert all(len(part.owners) == 1 for part in geometry.parts)


def testDegenerateRoutesAndDuplicateCornersAreSafe():
    assert not wireGeometry({A: Route(())}).parts
    assert not wireGeometry({A: Route(((0, 0),))}).parts
    assert len(wireGeometry({A: Route(((0, 0), (0, 0), (10, 0)))}).parts) == 1


def testFocusIsDirectAdjacencyNotRecursiveAndPreservesExplicitSelection():
    keys = (A, B, C, D)
    assert focusEdges(keys, ("source",)) == {A, B, C}
    assert focusEdges(keys, ("source", "d")) == set(keys)
    assert focusEdges(keys, ("source",), (D,), port=("source", "output", "value")) == {A, B, D}
    assert focusEdges(keys, ("source",), port=("a", "input", "in")) == {A}
    assert focusEdges(keys, ("source",), (D,), preview=(B,)) == {B, D}
    assert not focusEdges(keys, (), preview=())


@pytest.mark.parametrize("values,color", [
    ({}, NORMAL_COLOR), ({"dimmed": True}, DIM_COLOR),
    ({"focused": True}, FOCUS_COLOR), ({"dimmed": True, "blocked": True}, ERROR_COLOR),
    ({"focused": True, "selected": True, "blocked": True}, SELECTED_COLOR),
])
def testStylePriorityKeepsErrorsVisible(values, color):
    assert edgeStyle(**values)[0] == color
