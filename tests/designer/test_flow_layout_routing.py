from itertools import combinations

import pytest

from emo_master.apps.designer.ui.flow_layout import LayoutNode, flowPositions
from emo_master.apps.designer.ui.flow_routing import OrthogonalRouter, Rect, RouteRequest, intersects


def assertDisjoint(nodes, positions):
    for a, b in combinations(nodes, 2):
        ax, ay = positions[a]
        bx, by = positions[b]
        assert (ax + nodes[a].width <= bx or bx + nodes[b].width <= ax
                or ay + nodes[a].height <= by or by + nodes[b].height <= ay)


@pytest.mark.parametrize('edges', [[], [('a', 'b'), ('b', 'c')],
    [('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd'), ('a', 'd')],
    [('a', 'b'), ('c', 'd')], [('a', 'b'), ('b', 'c'), ('c', 'a'), ('c', 'd')],
    [('a', 'a'), ('a', 'd')]])
def testLayersBranchesCyclesAndDeterminism(edges):
    nodes = {name: LayoutNode(260 + i * 40, 90 + i * 110) for i, name in enumerate('abcde')}
    positions = flowPositions(nodes, edges)
    assert positions == flowPositions(dict(reversed(list(nodes.items()))), list(reversed(edges)))
    assertDisjoint(nodes, positions)
    if ('c', 'a') not in edges:
        for source, target in edges:
            if source != target:
                assert positions[target][0] >= positions[source][0] + nodes[source].width + 120


def testBoundariesAndIsolatesBelowConnectedGroups():
    nodes = {key: LayoutNode(260, 100, kind) for key, kind in
             [('in', 'workflow_input'), ('a', 'operator'), ('b', 'operator'),
              ('out', 'workflow_output'), ('loose', 'workflow_input'), ('other', 'operator')]}
    positions = flowPositions(nodes, [('in', 'a'), ('a', 'b'), ('b', 'out')])
    assert positions['in'][0] < positions['a'][0] < positions['b'][0] < positions['out'][0]
    assert min(positions[key][1] for key in ('loose', 'other')) > max(positions[key][1] + 100 for key in ('in', 'a', 'b', 'out'))


def testLongChainDoesNotUsePythonRecursion():
    nodes = {str(i): LayoutNode(260, 100) for i in range(1100)}
    positions = flowPositions(nodes, [(str(i), str(i + 1)) for i in range(1099)])
    assert len(positions) == 1100
    assert positions['1099'][0] > positions['0'][0]


def assertSafe(nodes, request, route):
    assert not route.blocked, route.reason
    assert route.points[0] == request.start
    assert route.points[-1] == request.end
    assert route.points[1][0] > request.start[0]
    assert route.points[-2][0] < request.end[0]
    for a, b in zip(route.points, route.points[1:]):
        assert a[0] == b[0] or a[1] == b[1]
        for key, rect in nodes.items():
            if key not in (request.key[0], request.key[2]):
                assert not intersects(a, b, rect.expanded(12))
    for a, b, c in zip(route.points, route.points[1:], route.points[2:]):
        # No collinear reversal, including at port exit/ingress stubs.
        assert (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) >= 0


def testManyPortsExpandLayerChannel():
    nodes = {'a': LayoutNode(260, 100), 'b': LayoutNode(260, 100)}
    single = flowPositions(nodes, [('a', 'b')])
    many = flowPositions(nodes, [('a', 'b')] * 12)
    assert many['b'][0] > single['b'][0]


@pytest.mark.parametrize('target', [Rect(700, 0, 960, 130), Rect(-600, 0, -340, 130)])
def testAvoidsObstacleAndRoutesReturnEdges(target):
    nodes = {'source': Rect(0, 0, 260, 130), 'target': target, 'obstacle': Rect(350, -100, 600, 180)}
    request = RouteRequest(('source', 'out', 'target', 'in'), (248, 60), (target.left + 12, 60))
    route = OrthogonalRouter(nodes, [request]).routeAll()[request.key]
    assertSafe(nodes, request, route)
    if target.left < 0:
        assert any(p[1] <= -20 or p[1] >= 150 for p in route.points)


def testMultiportChannelsAndDeterministicRouting():
    nodes = {'s': Rect(0, 0, 260, 180), 't': Rect(600, 230, 860, 410)}
    requests = [RouteRequest(('s', f'o{i}', 't', f'i{i}'), (248, 50 + i * 30), (612, 280 + i * 30)) for i in range(4)]
    routes = OrthogonalRouter(nodes, requests).routeAll()
    assert routes == OrthogonalRouter(dict(reversed(list(nodes.items()))), list(reversed(requests))).routeAll()
    lanes = []
    for request in requests:
        route = routes[request.key]
        assertSafe(nodes, request, route)
        lanes.append({a[0] for a, b in zip(route.points, route.points[1:]) if a[0] == b[0]})
    assert len({tuple(sorted(lane)) for lane in lanes}) == 4


def testOverlappingObstructionIsExplicitAndSelfLoopCanRoute():
    nodes = {'s': Rect(0, 0, 260, 130), 't': Rect(200, 0, 460, 130)}
    request = RouteRequest(('s', 'out', 't', 'in'), (248, 60), (212, 60))
    route = OrthogonalRouter(nodes, [request]).routeAll()[request.key]
    assert route.blocked and '受阻' in route.reason
    nodes.pop('t')
    loop = RouteRequest(('s', 'out', 's', 'in'), (248, 60), (12, 90))
    assertSafe(nodes, loop, OrthogonalRouter(nodes, [loop]).routeAll()[loop.key])
