"""Port-aware layout and signal-aware routing remain purely visual decisions."""
from itertools import combinations

import pytest

from emo_master.apps.designer.ui.flow_layout import LayoutEdge, LayoutNode, flowPositions
from emo_master.apps.designer.ui.flow_routing import OccupiedSegment, OrthogonalRouter, Rect, RouteRequest
from tests.designer.test_flow_layout_routing import assertDisjoint, assertSafe


def _length(route):
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route.points, route.points[1:]))


def _crosses(a, b, c, d):
    horizontal = a[1] == b[1]
    if horizontal == (c[1] == d[1]):
        return False
    h1, h2, v1, v2 = (a, b, c, d) if horizontal else (c, d, a, b)
    return (min(h1[0], h2[0]) < v1[0] < max(h1[0], h2[0])
            and min(v1[1], v2[1]) < h1[1] < max(v1[1], v2[1]))


def testLayoutUsesActualPortOrderRatherThanNodeNames():
    nodes = {'source': LayoutNode(260, 300), 'a-bottom': LayoutNode(260, 100),
             'z-top': LayoutNode(260, 100), 'm-middle': LayoutNode(260, 100)}
    edges = [LayoutEdge('source', 'a-bottom', 240, 50), LayoutEdge('source', 'z-top', 60, 50),
             LayoutEdge('source', 'm-middle', 150, 50)]
    positions = flowPositions(nodes, edges)
    assert positions['z-top'][1] < positions['m-middle'][1] < positions['a-bottom'][1]
    assertDisjoint(nodes, positions)
    assert positions == flowPositions(dict(reversed(list(nodes.items()))), reversed(edges))


def testLayoutAlignsPortsNotNodeCentresAndSupportsPairCallers():
    nodes = {'a': LayoutNode(260, 180), 'b': LayoutNode(340, 280), 'c': LayoutNode(260, 100)}
    edges = [LayoutEdge('a', 'b', 60, 120), LayoutEdge('b', 'c', 180, 50)]
    positions = flowPositions(nodes, edges)
    assert positions['a'][1] + 60 == positions['b'][1] + 120
    assert positions['b'][1] + 180 == positions['c'][1] + 50
    assertDisjoint(nodes, positions)
    assertDisjoint(nodes, flowPositions(nodes, [('a', 'b'), ('b', 'c')]))
    assert flowPositions({}, []) == {}
    assert flowPositions(nodes, edges + [LayoutEdge('missing', 'c', 0, 0)]) == positions


@pytest.mark.parametrize('delta', [-220, 220])
def testParallelChannelsUsePortGeometryWithoutCrossingsOrDoglegs(delta):
    nodes = {'s': Rect(0, 0, 260, 200), 't': Rect(600, delta, 860, delta + 200)}
    # Intentionally reverse alphabetical port order relative to visible rows.
    requests = [RouteRequest(('s', name, 't', name), (248, y), (612, y + delta))
                for name, y in [('z', 50), ('m', 90), ('a', 130)]]
    router = OrthogonalRouter(nodes, requests)
    routes = router.routeAll()
    for request in requests:
        route = routes[request.key]
        assertSafe(nodes, request, route)
        assert len(route.points) == 4
        assert _length(route) == abs(request.end[0] - request.start[0]) + abs(delta)
    for first, second in combinations(routes.values(), 2):
        assert not any(_crosses(a, b, c, d) for a, b in zip(first.points, first.points[1:])
                       for c, d in zip(second.points, second.points[1:]))
    assert routes == OrthogonalRouter(dict(reversed(list(nodes.items()))), list(reversed(requests))).routeAll()


def testFanoutSharesOneSourceExitAndDoesNotChargeItsOwnTrunk():
    nodes = {'s': Rect(0, 0, 260, 180), 'near': Rect(600, 0, 860, 180),
             'far': Rect(600, 260, 860, 440)}
    requests = [RouteRequest(('s', 'image', 'near', 'image'), (248, 50), (612, 50)),
                RouteRequest(('s', 'image', 'far', 'image'), (248, 50), (612, 310))]
    router = OrthogonalRouter(nodes, requests)
    assert router.stubs[requests[0].key][0] == router.stubs[requests[1].key][0]
    routes = router.routeAll()
    for request in requests:
        assertSafe(nodes, request, routes[request.key])
        assert routes[request.key] == router.route(request)
    # An unrelated port on the same node is NOT the same signal.
    far = requests[1]
    original = routes[far.key]
    occupied = [OccupiedSegment(a, b, ('s', 'frame')) for a, b in zip(original.points, original.points[1:])]
    assert router.route(far, occupied) != original
    own = [OccupiedSegment(a, b, ('s', 'image')) for a, b in zip(original.points, original.points[1:])]
    assert router.route(far, own) == original


@pytest.mark.parametrize('fraction', [0, .123456789])
def testSignalCacheAndCooperativeStepsMatchFreshCacheForMixedFanout(fraction):
    nodes = {'s': Rect(0, 0, 260, 200), 'a': Rect(620, -200, 880, 0),
             'b': Rect(620, 120, 880, 320), 'c': Rect(1080, 0, 1340, 200)}
    requests = [RouteRequest(('s', port, target, port), (248, 50 + index * 32 + fraction),
                             (rect.left + 12, rect.top + 50 + index * 32 + fraction))
                for target, rect in nodes.items() if target != 's'
                for index, port in enumerate(('image', 'frame', 'sample'))]
    router = OrthogonalRouter(nodes, requests)
    occupied, expected = [], {}
    for request in router.requests:
        route = router.route(request, occupied)
        assertSafe(nodes, request, route)
        expected[request.key] = route
        occupied.extend(OccupiedSegment(a, b, request.key[:2]) for a, b in zip(route.points, route.points[1:]))
    assert router.routeAll() == expected == router.routeAll()
    steps = list(router.iterRoutes(stepNodes=8))
    assert None in steps
    assert dict(item for item in steps if item is not None) == expected


def testNarrowManualGapCompressesLanesInsteadOfBlockingValidPorts():
    nodes = {'s': Rect(0, 0, 260, 420), 't': Rect(360, 0, 620, 440)}
    requests = [RouteRequest(('s', f'o{i}', 't', f'i{i}'), (248, 50 + i * 28), (372, 60 + i * 28))
                for i in range(12)]
    router = OrthogonalRouter(nodes, requests)
    routes = router.routeAll()
    for request in requests:
        assertSafe(nodes, request, routes[request.key])
        start, end = router.stubs[request.key]
        assert start[0] < end[0]


def testShortConnectionsAreRoutedBeforeLongBypasses():
    nodes = {'z': Rect(0, 0, 260, 130), 'a': Rect(600, 0, 860, 130), 'b': Rect(1200, 0, 1460, 130)}
    short = RouteRequest(('z', 'z-out', 'a', 'in'), (248, 60), (612, 60))
    long = RouteRequest(('z', 'a-out', 'b', 'in'), (248, 90), (1212, 60))
    router = OrthogonalRouter(nodes, [long, short])
    assert router.requests[0] == short
    assert len(router.routeAll()[short.key].points) == 2
