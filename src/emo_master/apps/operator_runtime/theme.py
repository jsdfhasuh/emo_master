"""Light visual treatment for the Operator shell and its own tools."""

from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QFontDatabase

from emo_panel_protocol.theme import LIGHT_THEME


_FONT_FAMILIES = ('Microsoft YaHei UI', 'Microsoft YaHei', 'Noto Sans CJK SC')


def applyOperatorTheme(widget, name, *, surface='page', shell=False):
    widget.setObjectName(name)
    widget.setAttribute(Qt.WA_StyledBackground, True)
    families = QFontDatabase().families()
    family = next((candidate for candidate in _FONT_FAMILIES if candidate in families), None)
    if family:
        font = QFont(widget.font())
        font.setFamily(family)
        font.setStyleStrategy(QFont.PreferAntialias)
        widget.setFont(font)
    root = f'#{name}'
    theme = LIGHT_THEME
    page, white, subtle = theme['page'], theme['surface'], theme['subtle']
    text, muted, border, blue = theme['text'], theme['muted'], theme['border'], theme['primary']
    selected = theme['soft']['blue']
    control = '#aab8cb'
    disabled = '#68768a'
    button = f'{root} QPushButton' + ('[operatorShellAction="true"]' if shell else '')
    fontTargets = f'{root} QLabel, {button}' if shell else f'{root} *'
    fontRule = f'{fontTargets} {{ font-family: "{family}"; }}' if family else ''
    outline = f'border: 1px solid {border}; border-radius: 9px;' if surface == 'surface' else ''
    widget.setStyleSheet(f'''
        {root} {{ background: {theme[surface]}; {outline} }}
        {fontRule}
        {root} QWidget[operatorRole="page"] {{ background: {page}; }}
        {root} QLabel {{ color: {text}; background: transparent; }}
        {root} QLabel[operatorRole="title"] {{ font-size: 19px; font-weight: bold; }}
        {root} QLabel[operatorRole="section"] {{ font-size: 14px; font-weight: bold; }}
        {root} QLabel[operatorRole="hint"] {{ color: {muted}; }}
        {root} QLabel[operatorRole="path"] {{ background: {subtle}; padding: 7px 9px; border-radius: 6px; }}
        {root} QGroupBox {{ color: {text}; background: {white}; border: 1px solid {border};
            border-radius: 8px; margin-top: 10px; padding: 8px 4px 3px; font-weight: bold; }}
        {root} QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
            color: {text}; font-size: 14px; }}
        {button}, {root} QToolButton {{ background: {white}; color: {text};
            border: 1px solid {control}; border-radius: 5px; padding: 4px 10px; min-height: 20px; }}
        {button}:enabled:hover, {root} QToolButton:enabled:hover {{
            background: {subtle}; border-color: {blue}; }}
        {button}:enabled:pressed, {button}:checked, {root} QToolButton:enabled:pressed, {root} QToolButton:checked {{
            background: {selected}; color: {blue}; border-color: {blue}; }}
        {button}:focus, {root} QToolButton:focus {{ border-color: {blue}; }}
        {button}:disabled, {root} QToolButton:disabled {{ color: {disabled}; background: {subtle}; border-color: {border}; }}
        {button}[operatorRole="primary"]:enabled {{ background: {blue}; color: {white}; font-weight: bold;
            border-color: {blue}; }}
        {button}[operatorRole="primary"]:enabled:hover, {button}[operatorRole="primary"]:enabled:pressed {{
            background: {theme['dark']}; border-color: {theme['dark']}; }}
        {button}[operatorRole="primary"]:focus {{ border: 1px solid {theme['dark']}; }}
        {button}[operatorRole="caution"]:enabled {{ color: {theme['foreground']['red']}; }}
        {button}[operatorRole="quiet"] {{ color: {text}; background: transparent; border-color: {border}; }}
        {button}[operatorRole="quiet"]:enabled:hover, {button}[operatorRole="quiet"]:focus {{
            color: {blue}; border-color: {blue}; }}
        {button}[operatorRole="quiet"]:disabled {{ color: {disabled}; }}
        {root} QLineEdit, {root} QComboBox, {root} QSpinBox, {root} QDateTimeEdit, {root} QPlainTextEdit,
        {root} QTextBrowser {{ color: {text}; background: {white}; border: 1px solid {control}; border-radius: 5px;
            padding: 4px 7px; selection-background-color: {blue}; selection-color: {white}; }}
        {root} QLineEdit, {root} QComboBox, {root} QSpinBox, {root} QDateTimeEdit {{ min-height: 20px; }}
        {root} QComboBox {{ padding-right: 22px; }}
        {root} QComboBox QAbstractItemView {{ padding: 3px; }}
        {root} QPlainTextEdit[operatorRole="path"], {root} QTextBrowser[operatorRole="path"] {{
            background: {subtle}; border-color: {border}; padding: 5px 7px; }}
        {root} QLineEdit:focus, {root} QComboBox:focus, {root} QSpinBox:focus, {root} QDateTimeEdit:focus,
        {root} QPlainTextEdit:focus, {root} QTextBrowser:focus {{ border-color: {blue}; }}
        {root} QLineEdit:disabled, {root} QComboBox:disabled, {root} QSpinBox:disabled, {root} QDateTimeEdit:disabled {{
            background: {subtle}; color: {disabled}; border-color: {border}; }}
        {root} QCheckBox, {root} QRadioButton {{ color: {text}; padding: 1px 2px; spacing: 6px; }}
        {root} QCheckBox:focus, {root} QRadioButton:focus {{ color: {blue}; }}
        {root} QCheckBox:disabled, {root} QRadioButton:disabled {{ color: {disabled}; }}
        {root} QCheckBox::indicator, {root} QRadioButton::indicator {{ width: 14px; height: 14px;
            background: {white}; border: 1px solid {control}; }}
        {root} QCheckBox::indicator {{ border-radius: 3px; }}
        {root} QRadioButton::indicator {{ border-radius: 8px; }}
        {root} QCheckBox::indicator:checked {{ background: {selected}; border-color: {blue};
            image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png); }}
        {root} QRadioButton::indicator:checked {{ background: {blue}; border: 3px solid {selected};
            width: 10px; height: 10px; }}
        {root} QCheckBox::indicator:enabled:hover, {root} QRadioButton::indicator:enabled:hover,
        {root} QCheckBox::indicator:focus, {root} QRadioButton::indicator:focus {{ border-color: {blue}; }}
        {root} QCheckBox::indicator:disabled, {root} QRadioButton::indicator:disabled {{
            background: {subtle}; border-color: {border}; }}
        {root} QRadioButton::indicator:checked:disabled {{ background: {control}; }}
        {root} QScrollBar:vertical {{ width: 11px; background: {subtle}; margin: 0; }}
        {root} QScrollBar:horizontal {{ height: 11px; background: {subtle}; margin: 0; }}
        {root} QScrollBar::handle {{ border: 2px solid {subtle}; border-radius: 5px; background: {control}; }}
        {root} QScrollBar::handle:vertical {{ min-height: 28px; }}
        {root} QScrollBar::handle:horizontal {{ min-width: 28px; }}
        {root} QScrollBar::handle:hover, {root} QScrollBar::handle:pressed {{ background: {muted}; }}
        {root} QScrollBar::add-line, {root} QScrollBar::sub-line {{ width: 0; height: 0; }}
        {root} QScrollBar::add-page, {root} QScrollBar::sub-page {{ background: none; }}
        {root} QAbstractItemView {{ background: {white}; alternate-background-color: {page}; color: {text};
            border: 1px solid {border}; border-radius: 5px; gridline-color: {border};
            selection-background-color: {selected}; selection-color: {text}; }}
        {root} QAbstractItemView:focus {{ border-color: {blue}; }}
        {root} QTreeView::item {{ padding: 3px 4px; }}
        {root} QAbstractItemView::item:selected {{ background: {selected}; color: {text}; }}
        {root} QHeaderView::section {{ background: {subtle}; color: {muted}; padding: 6px 7px;
            border: 0; border-bottom: 1px solid {border}; font-weight: bold; }}
        {root} QTabWidget::pane {{ background: {white}; border: 1px solid {border}; border-radius: 6px; }}
        {root} QTabBar::tab {{ color: {muted}; background: transparent; padding: 7px 12px;
            border-bottom: 2px solid transparent; }}
        {root} QTabBar#operatorLogSources::tab {{ padding: 8px 16px; }}
        {root} QTabBar::tab:enabled:hover {{ color: {text}; background: {subtle}; }}
        {root} QTabBar::tab:selected {{ color: {blue}; background: {selected}; border-bottom-color: {blue}; }}
        {root} QTabBar::tab:focus {{ border-bottom-color: {theme['dark']}; }}
        {root} QTabBar::tab:disabled {{ color: {disabled}; }}
        {root} QWidget[operatorRole="filterPanel"], {root} QWidget[operatorRole="detailPanel"] {{
            background: {white}; border: 1px solid {border}; border-radius: 6px; }}
        {root} QSplitter::handle {{ background: {border}; }}
        {root} QSplitter::handle:hover {{ background: {control}; }}
    ''')


def setStatusTone(label, tone):
    sheet = f"color: {LIGHT_THEME['foreground'][tone]};"
    if label.styleSheet() != sheet:
        label.setStyleSheet(sheet)
