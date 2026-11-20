"""Resolve project shell settings without changing host control behavior."""
from PySide2.QtCore import QRect, QSize, Qt
from PySide2.QtGui import QIcon
from PySide2.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter

from emo_panel_protocol.models import ControlAppearance
from .appearance import THEME, backgroundColor, borderStyle, foreground, roleStyle
from .native_style import NativeAppearance


class AppearanceButton(QPushButton):
    """Use the native button and style states, with configurable icon layout."""

    def __init__(self, *args):
        super().__init__(*args)
        self._iconRight = False
        self._iconGap = None
        self._authoredIconSize = None
        self._previewState = None

    def setIconLayout(self, position, size, gap):
        if size != self._authoredIconSize:
            self._authoredIconSize = size
            if size is None:
                size = self.style().pixelMetric(QStyle.PM_ButtonIconSize, None, self)
            self.setIconSize(QSize(size, size))
        if (position == 'right', gap) != (self._iconRight, self._iconGap):
            self._iconRight, self._iconGap = position == 'right', gap
            self.updateGeometry()
            self.update()

    def setPreviewState(self, state):
        self._previewState = state
        self.setEnabled(state != 'disabled')
        self.update()

    def sizeHint(self):
        size = super().sizeHint()
        if self._iconGap is not None and not self.icon().isNull() and self.text():
            actualWidth = self.icon().actualSize(self.iconSize()).width()
            delta = actualWidth - self.iconSize().width() + self._iconGap - 4
            size.setWidth(size.width() + delta)
        return size

    def paintEvent(self, event):
        authoredLayout = not self.icon().isNull() and (self._iconRight or self._iconGap is not None)
        if not authoredLayout and self._previewState is None:
            super().paintEvent(event)
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if self._previewState is not None:
            option.state &= ~(QStyle.State_MouseOver | QStyle.State_Sunken)
            if self._previewState in ('hover', 'pressed'):
                option.state |= QStyle.State_MouseOver
            if self._previewState == 'pressed':
                option.state |= QStyle.State_Sunken
        painter = QStylePainter(self)
        if not authoredLayout:
            painter.drawControl(QStyle.CE_PushButton, option)
            return

        icon, text = QIcon(option.icon), option.text
        option.icon, option.text = QIcon(), ''
        painter.drawControl(QStyle.CE_PushButton, option)
        style = self.style()
        content = style.subElementRect(QStyle.SE_PushButtonContents, option, self)
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        pixmap = icon.pixmap(option.iconSize, mode, QIcon.On if self.isChecked() else QIcon.Off)
        iconWidth = round(pixmap.width() / pixmap.devicePixelRatio())
        iconHeight = round(pixmap.height() / pixmap.devicePixelRatio())
        gap = (4 if self._iconGap is None else self._iconGap) if text else 0
        textWidth = min(option.fontMetrics.size(Qt.TextShowMnemonic, text).width(), max(0, content.width() - iconWidth - gap))
        left = content.x() + (content.width() - textWidth - gap - iconWidth) // 2
        iconLeft = left + textWidth + gap if self._iconRight else left
        textLeft = left if self._iconRight else left + iconWidth + gap
        iconTop = content.y() + (content.height() - iconHeight) // 2
        if option.state & QStyle.State_Sunken:
            iconLeft += style.pixelMetric(QStyle.PM_ButtonShiftHorizontal, option, self)
            iconTop += style.pixelMetric(QStyle.PM_ButtonShiftVertical, option, self)
        painter.drawPixmap(iconLeft, iconTop, pixmap)
        option.text = text
        option.rect = QRect(textLeft, content.y(), textWidth, content.height())
        painter.drawControl(QStyle.CE_PushButtonLabel, option)


def shellTextStyle(shell, role):
    settings = getattr(shell.headerTypography, role, None)
    defaults = {'size': 20, 'weight': 'bold', 'color': 'default'} if role == 'title' else {
        'size': 12, 'weight': 'normal', 'color': 'neutral'}
    if settings is not None:
        defaults.update(settings.model_dump(exclude_none=True))
    return (f"font-size:{defaults['size']}px;font-weight:{defaults['weight']};"
            f"color:{foreground(defaults['color'])};background:transparent;")


def applyShellHeader(header, title, subtitle, shell):
    header.setObjectName('pageShellHeader')
    header.setAttribute(Qt.WA_StyledBackground, True)
    title.setStyleSheet(shellTextStyle(shell, 'title'))
    subtitle.setStyleSheet(shellTextStyle(shell, 'helper'))
    surface = shell.headerSurface
    if surface is None:
        header.setStyleSheet('')
        header.layout().setContentsMargins(12, 8, 12, 0)
        return
    fields = surface.model_dump(exclude_none=True)
    card = fields.get('kind', 'none') == 'card'
    background = backgroundColor(fields.get('background', 'surface'))
    border = fields.get('border', 'subtle' if card else 'none')
    radius = fields.get('radius', 12 if card else 0)
    stroke = borderStyle(fields, width=1 if border == 'subtle' else 0, color=THEME['border'])
    header.setStyleSheet(f"QWidget#pageShellHeader {{background:{background};{stroke}border-radius:{radius}px;}}")
    padding = fields.get('padding', {})
    defaults = {'left': 24, 'top': 12, 'right': 24, 'bottom': 12}
    header.layout().setContentsMargins(*(padding.get(side, defaults[side])
                                        for side in ('left', 'top', 'right', 'bottom')))


def _buttonStyle(button, native, style, fields, textStyle, *, host):
    color = '#ffffff' if style == 'primary' else THEME['primary'] if style == 'link' else THEME['text']
    background = THEME['primary'] if style == 'primary' else 'transparent' if style == 'link' else THEME['surface']
    border = THEME['primary'] if style == 'primary' else 'transparent' if style == 'link' else THEME['border']
    structuredStates = fields.get('buttonStates', {})
    values = {**fields, **structuredStates.get('default', {})}
    color = values.get('color', color)
    background = values.get('background', background)
    radius = fields.get('radius', 8 if host else 6)
    padding = fields.get('padding', {})
    vertical, horizontal = (8, 16) if host else (5, 12)
    inset = ' '.join(f"{padding.get(side, vertical if side in ('top', 'bottom') else horizontal)}px"
                     for side in ('top', 'right', 'bottom', 'left'))
    base = (f'{textStyle}background:{background};color:{color};'
            f'{borderStyle(values, width=1, color=border)}border-radius:{radius}px;padding:{inset};')
    states = [(':focus', f"border-color:{THEME['primary']};")]
    if structuredStates:
        for key, selector in (('hover', ':enabled:hover'), ('pressed', ':enabled:pressed'), ('disabled', ':disabled')):
            override = structuredStates.get(key, {})
            if not override:
                continue
            declarations = ''.join(f'{name}:{override[name]};' for name in ('background', 'color') if name in override)
            if {'borderWidth', 'borderStyle', 'borderColor'} & override.keys():
                declarations += borderStyle({**values, **override}, width=1, color=border)
            states.append((selector, declarations))
    else:
        if host:
            hover = THEME['primary'] if style == 'primary' else THEME['subtle']
            states.append((':enabled:hover', f'background:{hover};'))
        states.append((':disabled', f"color:{THEME['muted']};background:{THEME['subtle']};border-color:{THEME['border']};"))
    native.apply(button, 'action', base, rules=states)
    if isinstance(button, AppearanceButton):
        button.setIconLayout(fields.get('iconPosition'), fields.get('iconSize'), fields.get('iconGap'))


def configurePageButton(button, props, native):
    fields = props.model_dump(include={'iconPosition', 'iconSize', 'iconGap', 'buttonStates'}, exclude_none=True)
    role = getattr(props.typography, 'value', None)
    if role and role.color is not None:
        fields['color'] = foreground(role.color)
    _buttonStyle(button, native, props.buttonStyle, fields, roleStyle(props), host=False)


def configureHostButton(button, control, *, kind, fallbackLabel, tooltip):
    """Apply presentation only; the host remains responsible for command state."""
    button.setText(control.label if control is not None and control.label is not None else fallbackLabel)
    button.setToolTip(tooltip)
    appearance = (control.appearance if control is not None else None) or ControlAppearance()
    authored = kind + appearance.model_dump_json(exclude_none=True)
    # Status refreshes frequently while the authored appearance normally stays fixed.
    if button.property('pageControlAppearance') == authored:
        return
    button.setProperty('pageControlAppearance', authored)
    style = appearance.style or ('primary' if kind == 'start' else 'secondary')
    text = appearance.text
    fields = appearance.model_dump(exclude_none=True)
    if text is not None and text.color is not None:
        fields['color'] = foreground(text.color)
    size = text.size if text is not None and text.size is not None else 13
    weight = text.weight if text is not None and text.weight is not None else 'normal'
    _buttonStyle(button, NativeAppearance(button, appearance.customStyle), style,
                 fields, f'font-size:{size}px;font-weight:{weight};', host=True)
    button.setMinimumSize(appearance.minWidth or 0, appearance.minHeight if appearance.minHeight is not None else 32)
    iconKey = kind if appearance.showIcon else 'none'
    if button.property('pageControlIcon') != iconKey:
        button.setIcon(button.style().standardIcon(QStyle.SP_MediaPlay if kind == 'start' else QStyle.SP_MediaStop)
                       if appearance.showIcon else QIcon())
        button.setProperty('pageControlIcon', iconKey)
