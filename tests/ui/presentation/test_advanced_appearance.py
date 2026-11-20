"""Native stylesheet rendering and ownership at the common page entry point."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide2.QtTest import QTest

from emo_master.core.presentation.models import Component, Page, PageShell, Presentation
from emo_master.ui.presentation.native_style import selectorHints
from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.ui.presentation.shell import AppearanceButton, configureHostButton
from emo_panel_protocol.theme import LIGHT_THEME


def _custom(code, *, enabled=True):
    return {'qtQss': {'enabled': enabled, 'code': code}}


def _pages(*components):
    return RuntimePages(Presentation(defaultPageId='p', pageOrder=['p'],
                                    pages={'p': Page(name='测试', components=list(components))}))


def testNativeStylesStayWithinTheOwningCardAndInstance(qtApp):
    nested = Component(componentId='nested', type='text', props={
        'text': 'nested text', 'typography': {'value': {'size': 18, 'color': '#214D85'}},
    })
    parent = Component(componentId='parent', type='container', children=[nested], props={
        'title': 'container',
        'customStyle': _custom(
            'QFrame[emoPart="root"], QLabel[emoPart="title"], *[emoPart="value"] '
            '{color:#995305; font-size:24px;}'),
    })
    adjacent = Component(componentId='adjacent', type='text', layout={'row': 1}, props={
        'text': 'adjacent text', 'typography': {'value': {'size': 16, 'color': '#147044'}},
    })
    first, second = _pages(parent, adjacent), _pages(parent)
    for window in (first, second):
        window.show()
    qtApp.processEvents()
    try:
        firstCard, secondCard = (window.cards['p']['parent'] for window in (first, second))
        assert firstCard.property('emoAppearanceOwner') != secondCard.property('emoAppearanceOwner')
        assert firstCard.containerTitle.label.palette().color(QPalette.WindowText).name() == '#995305'
        nestedValue = first.widgets['p']['nested'][1].value
        assert nestedValue.font().pixelSize() == 18
        assert nestedValue.palette().color(QPalette.Text).name() == '#214d85'
        adjacentValue = first.widgets['p']['adjacent'][1].value
        assert adjacentValue.font().pixelSize() == 16
        assert adjacentValue.palette().color(QPalette.Text).name() == '#147044'
        assert '*[emoPart="value"]' not in selectorHints(firstCard)
        assert '*[emoPart="value"]' in selectorHints(first.cards['p']['nested'])
    finally:
        first.close()
        second.close()


def testIndicatorNativePartsSurviveStateUpdatesAndDisableReturnsToStructuredStyle(qtApp):
    code = ('QFrame[emoPart="root"] {border-left-width:4px;}'
            'QLabel[emoPart="value"] {font-weight:bold;}'
            'QLabel[emoPart="marker"] {color:#214D85;}')
    component = Component(componentId='direction', type='indicator', props={
        'indicatorView': 'row', 'showMarker': True, 'surface': {'padding': {'left': 12}},
        'typography': {'value': {'size': 16, 'weight': 'normal'}},
        'indicatorStates': {
            'true': {'text': '符合', 'color': '#11734C', 'background': '#F0F9F5',
                     'borderColor': '#72B39C', 'borderWidth': 1, 'borderStyle': 'solid'},
            'false': {'text': '偏离', 'color': 'red', 'background': 'soft',
                      'borderColor': '#995305', 'borderWidth': 2, 'borderStyle': 'dashed'},
        },
        'customStyle': _custom(code),
    })
    enabled = _pages(component)
    disabledProps = component.props.model_copy(update={'customStyle': type(component.props.customStyle).model_validate(
        _custom(code, enabled=False))})
    disabled = _pages(component.model_copy(update={'props': disabledProps}))
    for window in (enabled, disabled):
        window.show()
    try:
        for window, bold, markerColor, width in ((enabled, True, '#214d85', 4), (disabled, False, '#11734c', 1)):
            view = window.widgets['p']['direction'][1]
            view.setValue(True)
            qtApp.processEvents()
            assert view.text() == '符合' and view.marker.text() == '●' and view.value.font().bold() == bold
            assert view.value.palette().color(QPalette.WindowText).name() == '#11734c'
            assert view.marker.palette().color(QPalette.WindowText).name() == markerColor
            card = window.cards['p']['direction'].grab().toImage()
            row = card.height() // 2
            assert all(card.pixelColor(x, row).name() == '#72b39c' for x in range(width))
            assert card.pixelColor(width, row).name() == '#f0f9f5'
            view.setValue(False)
            qtApp.processEvents()
            assert view.text() == '偏离' and view.value.font().bold() == bold
            assert view.value.palette().color(QPalette.WindowText).name() != '#11734c'
            if bold:
                assert view.marker.palette().color(QPalette.WindowText).name() == markerColor
            else:
                dashed = window.cards['p']['direction'].grab().toImage()
                edge = [dashed.pixelColor(0, y).name() for y in range(4, dashed.height() - 4)]
                assert '#995305' in edge and LIGHT_THEME['soft']['red'] in edge
    finally:
        enabled.close()
        disabled.close()


def testHostButtonPaintsRightIconAndNativeStates(qtApp):
    control = PageShell(startControl={'label': 'Start', 'appearance': {
        'showIcon': True, 'iconPosition': 'right', 'iconSize': 12, 'iconGap': 6,
        'background': '#087F5B',
        'buttonStates': {'hover': {'background': '#123456'}},
        'customStyle': _custom(
            '*[emoPart="action"]:hover {background:#06664A;}'
            '*[emoPart="action"]:pressed {background:#044D38;}'
            '*[emoPart="action"]:disabled {background:#E8EDF3;}'),
    }}).startControl
    button = AppearanceButton()
    try:
        configureHostButton(button, control, kind='start', fallbackLabel='Start', tooltip='')
        icon = QPixmap(12, 12)
        glyph = QColor('#9B580A')
        icon.fill(glyph)
        button.setIcon(QIcon(icon))
        button.resize(180, 44)
        button.show()
        for state, color in (('default', '#087f5b'), ('hover', '#06664a'),
                             ('pressed', '#044d38'), ('disabled', '#e8edf3')):
            button.setPreviewState(state)
            qtApp.processEvents()
            painted = button.grab().toImage()
            assert painted.pixelColor(8, 22).name() == color
            if state == 'default':
                glyphPixels = [(x, y) for x in range(painted.width()) for y in range(painted.height())
                               if painted.pixelColor(x, y) == glyph]
                assert glyphPixels and min(x for x, _ in glyphPixels) > button.width() // 2
                assert len(glyphPixels) == 12 * 12
        disabledCode = control.model_copy(deep=True)
        disabledCode.appearance.customStyle.qtQss.enabled = False
        configureHostButton(button, disabledCode, kind='start', fallbackLabel='Start', tooltip='')
        button.setPreviewState('hover')
        qtApp.processEvents()
        structured = button.grab().toImage()
        assert structured.pixelColor(8, 22).name() == '#123456'
        assert structured.pixelColor(0, 22).name() == LIGHT_THEME['primary']
    finally:
        button.close()


def testButtonNavigationKeepsRealCheckedAppearanceAfterChangingPage(qtApp):
    props = {'customStyle': _custom('QPushButton[emoPart="action"]:checked {background:#D9E8FA;}')}
    pages = {page: Page(name=page, components=[Component(componentId='nav-' + page,
             type='page_navigation', props=props)]) for page in ('first', 'second')}
    presentation = Presentation(defaultPageId='first', pageOrder=list(pages), pages=pages,
                                navigation={'mode': 'buttons'})
    window = RuntimePages(presentation)
    window.show()
    qtApp.processEvents()
    try:
        first = window.widgets['first']['nav-first'][1]
        assert first.buttons['first'].isChecked()
        QTest.mouseClick(first.buttons['second'], Qt.LeftButton)
        assert window.currentPageId == 'second'
        second = window.widgets['second']['nav-second'][1]
        assert not second.buttons['first'].isChecked()
        selected = second.buttons['second']
        assert selected.isChecked()
        painted = selected.grab().toImage()
        assert painted.pixelColor(8, selected.height() // 2).name() == '#d9e8fa'
        assert '*[emoPart="action"]:checked' in selectorHints(window.cards['second']['nav-second'])
    finally:
        window.close()
