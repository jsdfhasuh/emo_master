"""Resolve the shared theme into native widget styles and measurements."""
import json
from math import ceil

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QColor, QTextDocument, QTextOption
from PySide2.QtWidgets import QFrame, QLabel, QHBoxLayout, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from emo_panel_protocol.theme import LIGHT_THEME


THEME = LIGHT_THEME
COLORS = dict(THEME['foreground'], default=THEME['text'], primary=THEME['primary'])


def foreground(color):
    return COLORS.get(color, color)


def backgroundColor(color):
    return THEME.get(color, color)


def roleStyle(props, role='value'):
    style = dict(THEME['typography'][role])
    configured = getattr(props.typography, role, None)
    if configured is not None:
        style.update(configured.model_dump(exclude_none=True))
    return f"font-size:{style['size']}px;font-weight:{style['weight']};color:{foreground(style['color'])};"


def borderStyle(fields, *, width, color):
    return (f"border:{fields.get('borderWidth', width)}px "
            f"{fields.get('borderStyle', 'solid')} {fields.get('borderColor', color)};")


def surfaceStyle(card, component, native, *, state=None):
    surface = component.props.surface
    fields = surface.model_dump(exclude_none=True) if surface else {}
    framed = fields.get('kind', 'card' if component.type == 'container' else 'none') == 'card'
    background = fields.get('background', 'surface' if framed else 'transparent')
    border = fields.get('border', 'subtle' if framed else 'none')
    radius = fields.get('radius', 12 if framed else 0)
    fill = indicatorBackground(state) or backgroundColor(background)
    if state is not None:
        fields.update(state.model_dump(include={'borderWidth', 'borderStyle', 'borderColor'}, exclude_none=True))
    stroke = borderStyle(fields, width=1 if border == 'subtle' else 0, color=THEME['border'])
    native.apply(card, 'root', f'background:{fill};{stroke}border-radius:{radius}px;')
    padding = fields.get('padding', {})
    default = 16 if framed else 0
    return tuple(padding.get(side, default) for side in ('left', 'top', 'right', 'bottom'))


def indicatorStyle(props, value):
    if type(value) in (int, bool, str):
        return props.indicatorStates.get(json.dumps(value, ensure_ascii=False))
    return None


def indicatorBackground(state):
    if not state or state.background == 'none':
        return None
    if state.background != 'soft':
        return state.background
    if state.color in THEME['soft']:
        return THEME['soft'][state.color]
    authored = QColor(foreground(state.color))
    return QColor(*[round(0.12 * channel + 0.88 * 255)
                    for channel in (authored.red(), authored.green(), authored.blue())]).name()


def plainLabel(text, style=''):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet(style)
    return label


class WrappedText(QTextEdit):
    """Selectable plain text whose measured height uses the same wrap policy."""
    def __init__(self, style, alignment):
        super().__init__()
        self.setReadOnly(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet('QTextEdit {background:transparent;border:0;padding:0;' + style + '}')
        self.document().setDocumentMargin(0)
        self.measure = QTextDocument(self)
        self.measure.setDocumentMargin(0)
        option = self.document().defaultTextOption()
        option.setAlignment(alignment)
        self.document().setDefaultTextOption(option)
        self.measure.setDefaultTextOption(option)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def sizeHint(self):
        self.measure.setDefaultFont(self.font())
        self.measure.setTextWidth(-1)
        return self.measure.size().toSize()

    def heightForWidth(self, width):
        self.measure.setDefaultFont(self.font())
        self.measure.setTextWidth(max(1, width))
        return ceil(self.measure.size().height())

    def setText(self, text):
        self.measure.setPlainText(text)
        self.setPlainText(text)
        # Invalidate the leaf's layout item before its parent measures the new text.
        self.updateGeometry()

    def text(self):
        return self.toPlainText()

    def wheelEvent(self, event):
        event.ignore()


class ValueView(QWidget):
    def __init__(self, component, card, native):
        super().__init__(card)
        self.component = component
        self.card = card
        self.native = native
        self.appliedState = None
        props = component.props
        body = QVBoxLayout(self)
        body.setSpacing(3)
        body.setContentsMargins(0, 0, 0, 0)
        horizontal = props.valueLayout == 'horizontal' or (
            component.type == 'indicator' and props.indicatorView == 'row')
        row = QHBoxLayout() if horizontal else body
        if horizontal:
            row.setSpacing(8)
            body.addLayout(row)
        self.title = plainLabel(props.title) if props.title else None
        self.marker = None
        self.titleMarker = None
        markerEnabled = props.showMarker if props.showMarker is not None else props.indicatorView != 'text'
        hasMarker = component.type == 'indicator' and markerEnabled
        if self.title is not None:
            self.native.apply(self.title, 'title', roleStyle(props, 'title'))
            if hasMarker and props.markerPosition == 'title':
                titleGroup = QWidget(self)
                titleLayout = QHBoxLayout(titleGroup)
                titleLayout.setContentsMargins(0, 0, 0, 0)
                titleLayout.setSpacing(8)
                self.marker = plainLabel('●')
                self.titleMarker = self.marker
                self.marker.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
                titleLayout.addWidget(self.marker)
                titleLayout.addWidget(self.title, 1)
                row.addWidget(titleGroup, 1 if horizontal else 0)
            else:
                row.addWidget(self.title, 1 if horizontal else 0)
        endAligned = horizontal and bool(props.title)
        alignment = Qt.AlignRight if endAligned else Qt.AlignLeft
        if component.type == 'text':
            self.value = WrappedText('', alignment)
            valueStyle = 'background:transparent;border:0;padding:0;' + roleStyle(props)
        else:
            self.value = plainLabel('')
            self.value.setAlignment(alignment | Qt.AlignVCenter)
            valueStyle = roleStyle(props)
        self.native.apply(self.value, 'value', valueStyle)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        valueWidget = self.value
        self.badge = None
        isBadge = component.type == 'indicator' and props.indicatorView == 'badge'
        if (hasMarker and self.marker is None) or isBadge:
            valueWidget = QFrame(self)
            valueLayout = QHBoxLayout(valueWidget)
            valueLayout.setContentsMargins(0, 0, 0, 0)
            valueLayout.setSpacing(6)
            if hasMarker and self.marker is None:
                self.marker = plainLabel('●')
                valueLayout.addWidget(self.marker, 0, Qt.AlignVCenter)
            valueLayout.addWidget(self.value, 0, Qt.AlignVCenter)
            if isBadge:
                self.badge = valueWidget
        if component.type == 'indicator' and props.indicatorView == 'badge':
            row.addWidget(valueWidget, 0, alignment | Qt.AlignVCenter)
        elif endAligned:
            row.addWidget(valueWidget, 1, alignment if valueWidget is not self.value else Qt.Alignment())
        else:
            row.addWidget(valueWidget, 0, Qt.AlignLeft if valueWidget is not self.value else Qt.Alignment())
        if props.helperText:
            helper = plainLabel(props.helperText)
            self.native.apply(helper, 'helper', roleStyle(props, 'helper'))
            body.addWidget(helper)
        self.setValue(props.text if component.type == 'text' and not (component.bindings or props.recordValue) else None)

    def setValue(self, value, error=''):
        props, kind = self.component.props, self.component.type
        self.setToolTip(error)
        if kind == 'indicator':
            state = indicatorStyle(props, value) if not error else None
            if not state and value is not None and not error:
                self.setToolTip('未映射判定值: ' + json.dumps(value, ensure_ascii=False))
            color = foreground(state.color if state else 'neutral')
            if self.title is not None:
                titleStyle = roleStyle(props, 'title')
                if props.titleFollowsState:
                    titleStyle += f'color:{color};'
                self.native.apply(self.title, 'title', titleStyle)
            if self.marker is not None:
                valueRole = getattr(props.typography, 'value', None)
                size = 12 if self.titleMarker is not None else (
                    valueRole.size if valueRole and valueRole.size is not None else THEME['typography']['value']['size'])
                self.native.apply(self.marker, 'marker', f'font-size:{size}px;color:{color};')
                self.marker.setVisible(state is not None and props.showMarker is not False)
            style = roleStyle(props) + f'color:{color};'
            if props.indicatorView == 'row':
                if state != self.appliedState:
                    self.appliedState = state
                    surfaceStyle(self.card, self.component, self.native, state=state)
            elif self.badge is not None:
                background = indicatorBackground(state)
                stroke = borderStyle(state.model_dump(exclude_none=True) if state else {}, width=0, color=THEME['border'])
                sheet = f'QFrame {{background:{background or "transparent"};border-radius:5px;{stroke}}}'
                if self.badge.styleSheet() != sheet:
                    self.badge.setStyleSheet(sheet)
                    self.badge.layout().setContentsMargins(*((6, 2, 6, 2) if background else (0, 0, 0, 0)))
            self.native.apply(self.value, 'value', style)
            self.setText(state.text if state else props.emptyText)
        elif kind == 'number':
            text = (f'{value:.{props.decimals}f}' if type(value) is float else str(value)) if type(value) in (int, float) else None
            self.setText(error or (text + props.unit if text is not None else props.emptyText))
        else:
            self.setText(error or (props.emptyText if value is None else value if isinstance(value, str)
                                   else json.dumps(value, ensure_ascii=False)))

    def setText(self, text):
        if self.value.text() != text:
            self.value.setText(text)
            self.updateGeometry()

    def text(self):
        return self.value.text()
