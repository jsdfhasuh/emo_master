"""Page content chooses its own title/navigation; shell retains a compact exit."""
import re

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.core.presentation.models import Component, Page, Placement, Presentation, Props
from emo_master.ui.presentation.renderer import RuntimePages


def pages(*components, count=2):
    result = Presentation(defaultPageId='a', pageOrder=['a'],
                          pages={'a': Page(name='检测总览', components=list(components))})
    if count > 1:
        result.pages['b'] = Page(name='历史记录')
        result.pageOrder.append('b')
    return result


def choose(navigation, pageId):
    picker = navigation.picker
    picker.setFocus()
    index = picker.findData(pageId)
    key = Qt.Key_Down if index > picker.currentIndex() else Qt.Key_Up
    QTest.keyClick(picker, key)


def testSinglePageHasNoAutomaticHeaderAndReloadUpdatesFallback(qtApp):
    window = RuntimePages(pages(count=1))
    window.show()
    qtApp.processEvents()
    assert window.pageNavigation.isHidden()
    assert not hasattr(window, 'navigationLayout')
    window.reload(pages())
    assert window.pageNavigation.isVisible()
    choose(window.pageNavigation, 'b')
    assert window.currentPageId == 'b'
    assert window.stack.currentWidget() is window.pages['b']
    window.reload(pages(count=1))
    assert window.currentPageId == 'a'
    assert window.pageNavigation.isHidden()
    window.close()


def testOptionalNavigationReplacesFallbackAndCanReturnFromUncustomizedPage(qtApp):
    window = RuntimePages(pages(Component(componentId='navigation', type='page_navigation')))
    window.show()
    qtApp.processEvents()
    custom = window.widgets['a']['navigation'][1]
    assert window.pageNavigation.isHidden()
    assert custom.picker.currentData() == 'a'
    choose(custom, 'b')
    assert window.currentPageId == 'b'
    assert window.pageNavigation.isVisible()
    choose(window.pageNavigation, 'a')
    assert window.currentPageId == 'a'
    assert window.pageNavigation.isHidden()
    assert custom.picker.currentData() == 'a'
    window.close()


def testTitleAndClockSurviveExamplesSimulationAndHiddenClockStops(qtApp):
    config = pages(
        Component(componentId='title', type='page_title'),
        Component(componentId='clock', type='clock', layout=Placement(row=1)),
    )
    window = RuntimePages(config)
    window.allowsDesignExamples = True
    window.show()
    qtApp.processEvents()
    title = window.widgets['a']['title'][1]
    clock = window.widgets['a']['clock'][1]
    assert title.text() == '检测总览'
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', clock.text())
    assert clock.timer.isActive()
    window.setDesignExamples(True)
    window.setDesignExamples(False)
    window.setSimulationState('OK')
    assert title.text() == '检测总览'
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', clock.text())
    window.navigate('b')
    qtApp.processEvents()
    assert not clock.timer.isActive()
    window.navigate('a')
    qtApp.processEvents()
    assert clock.timer.isActive()
    window.close()
    assert not clock.timer.isActive()


def testEditingNavigationStaysOnCurrentPageAndTitleUsesConfiguredText(qtApp):
    window = RuntimePages(pages(
        Component(componentId='title', type='page_title', props=Props(text='产线看板')),
        Component(componentId='navigation', type='page_navigation', layout=Placement(row=1)),
    ))
    window.editing = True
    window.setEditorHost()
    window.show()
    qtApp.processEvents()
    navigation = window.widgets['a']['navigation'][1]
    choose(navigation, 'b')
    assert window.currentPageId == 'a'
    assert navigation.picker.currentData() == 'a'
    assert window.pageNavigation.isHidden()
    assert window.widgets['a']['title'][1].text() == '产线看板'
    window.close()
