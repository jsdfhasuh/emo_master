"""Resolve project shell settings without changing host control behavior."""
from PySide2.QtCore import Qt
from PySide2.QtGui import QIcon
from PySide2.QtWidgets import QStyle

from emo_panel_protocol.models import ControlAppearance
from emo_panel_protocol.theme import LIGHT_THEME


THEME = LIGHT_THEME
COLORS = dict(THEME['foreground'], default=THEME['text'], primary=THEME['primary'])


def shellTextStyle(shell, role):
    settings = getattr(shell.headerTypography, role, None)
    defaults = {'size': 20, 'weight': 'bold', 'color': 'default'} if role == 'title' else {
        'size': 12, 'weight': 'normal', 'color': 'neutral'}
    if settings is not None:
        defaults.update(settings.model_dump(exclude_none=True))
    return (f"font-size:{defaults['size']}px;font-weight:{defaults['weight']};"
            f"color:{COLORS[defaults['color']]};background:transparent;")


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
    background = THEME.get(fields.get('background', 'surface'), 'transparent')
    border = fields.get('border', 'subtle' if card else 'none')
    radius = fields.get('radius', 12 if card else 0)
    header.setStyleSheet(f"QWidget#pageShellHeader {{background:{background};"
                        f"border:{'1px solid ' + THEME['border'] if border == 'subtle' else '0'};"
                        f"border-radius:{radius}px;}}")
    padding = fields.get('padding', {})
    defaults = {'left': 24, 'top': 12, 'right': 24, 'bottom': 12}
    header.layout().setContentsMargins(*(padding.get(side, defaults[side])
                                        for side in ('left', 'top', 'right', 'bottom')))


def configureHostButton(button, control, *, kind, fallbackLabel, tooltip):
    """Apply presentation only; the host remains responsible for command state."""
    button.setText(control.label if control is not None and control.label is not None else fallbackLabel)
    button.setToolTip(tooltip)
    appearance = (control.appearance if control is not None else None) or ControlAppearance()
    style = appearance.style or ('primary' if kind == 'start' else 'secondary')
    color = '#ffffff' if style == 'primary' else THEME['primary'] if style == 'link' else THEME['text']
    background = THEME['primary'] if style == 'primary' else 'transparent' if style == 'link' else THEME['surface']
    border = THEME['primary'] if style == 'primary' else 'transparent' if style == 'link' else THEME['border']
    text = appearance.text
    if text is not None and text.color is not None:
        color = COLORS[text.color]
    size = text.size if text is not None and text.size is not None else 13
    weight = text.weight if text is not None and text.weight is not None else 'normal'
    padding = appearance.padding.model_dump(exclude_none=True) if appearance.padding else {}
    inset = ' '.join(f"{padding.get(side, 8 if side in ('top', 'bottom') else 16)}px"
                     for side in ('top', 'right', 'bottom', 'left'))
    radius = appearance.radius if appearance.radius is not None else 8
    hover = THEME['primary'] if style == 'primary' else THEME['subtle']
    sheet = (f"QPushButton {{background:{background};color:{color};border:1px solid {border};"
             f"border-radius:{radius}px;padding:{inset};font-size:{size}px;font-weight:{weight};}}"
             f"QPushButton:enabled:hover {{background:{hover};}}"
             f"QPushButton:focus {{border-color:{THEME['primary']};}}"
             f"QPushButton:disabled {{color:{THEME['muted']};background:{THEME['subtle']};"
             f"border-color:{THEME['border']};}}")
    # Operator status refreshes frequently; only repolish when the authored style changes.
    if button.property('pageControlStyle') != sheet:
        button.setStyleSheet(sheet)
        button.setProperty('pageControlStyle', sheet)
    button.setMinimumSize(appearance.minWidth or 0, appearance.minHeight if appearance.minHeight is not None else 32)
    iconKey = kind if appearance.showIcon else 'none'
    if button.property('pageControlIcon') != iconKey:
        button.setIcon(button.style().standardIcon(QStyle.SP_MediaPlay if kind == 'start' else QStyle.SP_MediaStop)
                       if appearance.showIcon else QIcon())
        button.setProperty('pageControlIcon', iconKey)
