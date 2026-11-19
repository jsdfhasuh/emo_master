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


def surfaceStyle(card, component, *, statusBackground=None):
    surface = component.props.surface
    fields = surface.model_dump(exclude_none=True) if surface else {}
    framed = fields.get('kind', 'card' if component.type == 'container' else 'none') == 'card'
    background = fields.get('background', 'surface' if framed else 'transparent')
    border = fields.get('border', 'subtle' if framed else 'none')
    radius = fields.get('radius', 12 if framed else 0)
    fill = statusBackground or THEME.get(background, 'transparent')
    card.setStyleSheet(f"QFrame#card {{background:{fill};"
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
    def __init__(self, component, card):
        super().__init__(card)
        self.component = component
        self.card = card
        self.statusBackground = None
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
        self.title = plainLabel(props.title, roleStyle(props, 'title')) if props.title else None
        self.titleMarker = None
        if self.title is not None:
            if component.type == 'indicator' and props.markerPosition == 'title':
                titleGroup = QWidget(self)
                titleLayout = QHBoxLayout(titleGroup)
                titleLayout.setContentsMargins(0, 0, 0, 0)
                titleLayout.setSpacing(8)
                self.titleMarker = plainLabel('●')
                self.titleMarker.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
                titleLayout.addWidget(self.titleMarker)
                titleLayout.addWidget(self.title, 1)
                row.addWidget(titleGroup, 1 if horizontal else 0)
            else:
                row.addWidget(self.title, 1 if horizontal else 0)
        endAligned = horizontal and bool(props.title)
        alignment = Qt.AlignRight if endAligned else Qt.AlignLeft
        if component.type == 'text':
            self.value = WrappedText(roleStyle(props), alignment)
        else:
            self.value = plainLabel('', roleStyle(props))
            self.value.setAlignment(alignment | Qt.AlignVCenter)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        if component.type == 'indicator' and props.indicatorView == 'badge':
            row.addWidget(self.value, 0, alignment | Qt.AlignVCenter)
        elif endAligned:
            row.addWidget(self.value, 1)
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
            if self.title is not None:
                titleStyle = roleStyle(props, 'title')
                if props.titleFollowsState:
                    titleStyle += f'color:{COLORS[color]};'
                self.title.setStyleSheet(titleStyle)
            if self.titleMarker is not None:
                self.titleMarker.setStyleSheet(f'font-size:12px;color:{COLORS[color]};')
                self.titleMarker.setVisible(bool(marker and state))
            style = roleStyle(props) + f'color:{COLORS[color]};'
            background = THEME['soft'][color] if state and state.background == 'soft' else None
            if props.indicatorView == 'row':
                if background != self.statusBackground:
                    self.statusBackground = background
                    surfaceStyle(self.card, self.component, statusBackground=background)
            elif props.indicatorView == 'badge' and background:
                style += f"background:{background};border-radius:5px;padding:2px 6px;"
            if self.value.styleSheet() != style:
                self.value.setStyleSheet(style)
            valueMarker = marker and state and self.titleMarker is None
            self.setText(('●  ' if valueMarker else '') + (state.text if state else props.emptyText))
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
