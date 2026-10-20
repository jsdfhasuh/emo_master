from emo_master.apps.designer.state.navigation_history import NavigationHistory, NavigationLocation


def loc(workflow, node=None, view=None, session='session'):
    return NavigationLocation(session, workflow, node, view)


def testCaptureDoesNotCreateStepsOrDestroyForwardAndNewVisitsBranch():
    h = NavigationHistory()
    h.reset(loc('a'))
    for i in range(30):
        h.capture(loc('a', 'selected', (1, i, i)))
    assert len(h.entries) == 1
    h.visit(loc('b'))
    h.visit(loc('c'))
    h.index = h.destination(-1, lambda _: True)
    h.capture(loc('b', view=(.5, 12, 34)))
    assert h.destination(1, lambda _: True) == 2
    h.visit(loc('b', view=(.7, 22, 44)))
    assert len(h.entries) == 3
    h.visit(loc('d'))
    assert [x.workflowId for x in h.entries] == ['a', 'b', 'd']
    assert h.destination(1, lambda _: True) is None


def testLocationsIncludeNodeAndSessionAndDeletedWorkflowsAreSkipped():
    h = NavigationHistory()
    h.reset(loc('a', 'first'))
    h.visit(loc('a', 'second'))
    h.visit(loc('deleted'))
    h.visit(loc('b'))
    assert h.destination(-1, lambda p: p.workflowId != 'deleted') == 1
    h.reset(loc('a', session='new'))
    assert len(h.entries) == 1 and h.current.session == 'new'
    assert h.destination(-1, lambda _: True) is None


def testRepeatedLocationsCollapseAndHistoryIsBounded():
    h = NavigationHistory(limit=3)
    for workflow in ['a', 'a', 'b', 'c', 'd']:
        h.visit(loc(workflow))
    assert [x.workflowId for x in h.entries] == ['b', 'c', 'd']
    assert h.index == 2
