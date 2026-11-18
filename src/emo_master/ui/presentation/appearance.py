"""Resolve the shared theme into native widget styles and measurements."""
import json
from math import ceil

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QTextDocument, QTextOption
from PySide2.QtWidgets import QLabel, QHBoxLayout, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from emo_panel_protocol.theme import LIGHT_THEME


THEME = LIGHT_THEME
COLORS = dict(THEME['foreground'], default=THEME['text'], primary=THEME['primary'])


def roleStyle(props, role='value'):
    style = dict(THEME['typography'][role])
    configured = getattr(props.typography, role, None)
    if configured is not None:
        style.update(configured.model_dump(exclude_none=True))
    return f"font-size:{style['size']}px;font-weight:{style['weight']};color:{COLORS[style['color']]};"


def surfaceStyle(card, component):
    surface = component.props.surface
    fields = surface.model_dump(exclude_none=True) if surface else {}
    framed = fields.get('kind', 'card' if component.type == 'container' else 'none') == 'card'
    background = fields.get('background', 'surface' if framed else 'transparent')
    border = fields.get('border', 'subtle' if framed else 'none')
    radius = fields.get('radius', 12 if framed else 0)
    card.setStyleSheet(f"QFrame#card {{background:{THEME.get(background, 'transparent')};"
                       f"border:{'1px solid ' + THEME['border'] if border == 'subtle' else '0'};"
                       f"border-radius:{radius}px;}}")
    padding = fields.get('padding', {})
    default = 16 if framed else 0
    return tuple(padding.get(side, default) for side in ('left', 'top', 'right', 'bottom'))


def indicatorStyle(props, value):
    if type(value) in (int, bool, str):
        return props.indicatorStates.get(json.dumps(value, ensure_ascii=False))
    return None


def plainLabel(text, style=''):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet(style)
    return label


class WrappedText(QTextEdit):
    """Selectable plain text whose measured height uses the same wrap policy."""
    def __init__(self, style):
        super().__init__()
        self.setReadOnly(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet('QTextEdit {background:transparent;border:0;padding:0;' + style + '}')
        self.document().setDocumentMargin(0)
        self.measure = QTextDocument(self)
        self.measure.setDocumentMargin(0)
        self.measure.setDefaultTextOption(self.document().defaultTextOption())
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

    def text(self):
        return self.toPlainText()

    def wheelEvent(self, event):
        event.ignore()


class ValueView(QWidget):
    def __init__(self, component):
        super().__init__()
        self.component = component
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
        if props.title:
            row.addWidget(plainLabel(props.title, roleStyle(props, 'title')), 1 if horizontal else 0)
        self.value = WrappedText(roleStyle(props)) if component.type == 'text' else plainLabel('', roleStyle(props))
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        if horizontal and props.title:
            row.addWidget(self.value, 1)
            self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        else:
            row.addWidget(self.value)
        if props.helperText:
            body.addWidget(plainLabel(props.helperText, roleStyle(props, 'helper')))
        self.setValue(props.text if component.type == 'text' and not (component.bindings or props.recordValue) else None)

    def setValue(self, value, error=''):
        props, kind = self.component.props, self.component.type
        self.setToolTip(error)
        if kind == 'indicator':
            state = indicatorStyle(props, value) if not error else None
            if not state and value is not None and not error:
                self.setToolTip('未映射判定值: ' + json.dumps(value, ensure_ascii=False))
            color = state.color if state else 'neutral'
            marker = props.showMarker if props.showMarker is not None else props.indicatorView != 'text'
            style = roleStyle(props) + f'color:{COLORS[color]};'
            if state and state.background == 'soft':
                style += f"background:{THEME['soft'][color]};border-radius:5px;padding:2px 6px;"
            if self.value.styleSheet() != style:
                self.value.setStyleSheet(style)
            self.setText(('●  ' if marker and state else '') + (state.text if state else props.emptyText))
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
