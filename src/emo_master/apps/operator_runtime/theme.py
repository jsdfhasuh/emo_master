"""Light visual treatment for the Operator shell and its own tools."""

from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QFontDatabase

from emo_panel_protocol.theme import LIGHT_THEME


def applyOperatorTheme(widget, name, *, surface='page', shell=False):
    widget.setObjectName(name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    if QFontDatabase().hasFamily("Microsoft YaHei UI"):
        font = QFont(widget.font())
        font.setFamily("Microsoft YaHei UI")
        widget.setFont(font)
    root = f'#{name}'
    theme = LIGHT_THEME
    page, white, subtle = theme['page'], theme['surface'], theme['subtle']
    text, muted, border, blue = theme['text'], theme['muted'], theme['border'], theme['primary']
    selected = theme['soft']['blue']
    button = f'{root} QPushButton' + ('[operatorShellAction="true"]' if shell else '')
    outline = f'border: 1px solid {border}; border-radius: 9px;' if surface == 'surface' else ''
    widget.setStyleSheet(f'''
        {root} {{ background: {theme[surface]}; {outline} }}
        {root} QLabel {{ color: {text}; background: transparent; }}
        {root} QLabel[operatorRole="title"] {{ font-size: 17px; font-weight: bold; }}
        {root} QLabel[operatorRole="section"] {{ font-size: 14px; font-weight: bold; }}
        {root} QLabel[operatorRole="hint"] {{ color: {muted}; }}
        {root} QLabel[operatorRole="path"] {{ background: {subtle}; padding: 7px 9px; border-radius: 6px; }}
        {root} QGroupBox {{ color: {text}; background: {white}; border: 1px solid {border};
            border-radius: 9px; margin-top: 12px; padding: 10px 4px 3px; font-weight: bold; }}
        {root} QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {text}; }}
        {button}, {root} QToolButton {{ background: {white}; color: {text}; border: 1px solid {border};
            border-radius: 6px; padding: 5px 10px; min-height: 18px; }}
        {button}:enabled:hover, {root} QToolButton:enabled:hover {{ background: {subtle}; border-color: {blue}; }}
        {button}:enabled:pressed, {root} QToolButton:enabled:pressed {{ background: {selected}; }}
        {button}:focus, {root} QToolButton:focus {{ border-color: {blue}; }}
        {button}:disabled, {root} QToolButton:disabled {{ color: {muted}; background: {subtle}; }}
        {button}[operatorRole="primary"]:enabled {{ background: {blue}; color: {white}; border-color: {blue}; }}
        {button}[operatorRole="primary"]:focus, {button}[operatorRole="primary"]:enabled:hover {{
            border-color: {theme['dark']}; }}
        {root} QLineEdit, {root} QComboBox, {root} QSpinBox, {root} QPlainTextEdit, {root} QTextBrowser {{
            color: {text}; background: {white}; border: 1px solid {border}; border-radius: 6px; padding: 4px; }}
        {root} QPlainTextEdit[operatorRole="path"] {{ background: {subtle}; padding: 5px; }}
        {root} QLineEdit:focus, {root} QComboBox:focus, {root} QSpinBox:focus, {root} QPlainTextEdit:focus,
        {root} QTextBrowser:focus {{ border-color: {blue}; }}
        {root} QLineEdit:disabled, {root} QComboBox:disabled, {root} QSpinBox:disabled {{
            background: {subtle}; color: {muted}; }}
        {root} QAbstractItemView {{ background: {white}; alternate-background-color: {page}; color: {text};
            border: 1px solid {border}; border-radius: 5px; gridline-color: {border};
            selection-background-color: {selected}; selection-color: {text}; }}
        {root} QAbstractItemView:focus {{ border-color: {blue}; }}
        {root} QHeaderView::section {{ background: {subtle}; color: {text}; padding: 4px 6px;
            border: 0; border-bottom: 1px solid {border}; }}
        {root} QTabWidget::pane {{ background: {white}; border: 1px solid {border}; border-radius: 6px; }}
        {root} QTabBar::tab {{ color: {muted}; background: {page}; padding: 6px 12px;
            border-bottom: 2px solid transparent; }}
        {root} QTabBar::tab:selected {{ color: {blue}; background: {selected}; border-bottom-color: {blue}; }}
        {root} QTabBar::tab:focus {{ border-bottom-color: {blue}; }}
        {root} QSplitter::handle {{ background: {subtle}; }}
    ''')


def setStatusTone(label, tone):
    sheet = f"color: {LIGHT_THEME['foreground'][tone]};"
    if label.styleSheet() != sheet:
        label.setStyleSheet(sheet)
