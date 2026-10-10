"""Native workspace buttons with one bounded, interruptible slide animation."""
from PySide2.QtCore import Qt, QSize, QRectF, Property, QPropertyAnimation, QEasingCurve
from PySide2.QtGui import QColor, QPainter, QPen
from PySide2.QtWidgets import QWidget, QHBoxLayout, QToolButton, QSizePolicy


class WorkspaceSwitch(QWidget):
    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.setObjectName('workspaceSelector')
        self.setAccessibleName('设计工作区')
        self.setToolTip('选择流程设计或页面设计；切换前校验当前编辑内容')
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._index = 0
        self._position = 0.0
        self._actions = tuple(actions)
        self.buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for action in self._actions:
            button = QToolButton(self)
            button.setObjectName('workspaceChoice')
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(action.text())
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(button, 1)
            self.buttons.append(button)
        self.setStyleSheet('''
            QToolButton#workspaceChoice {
                background: transparent; border: 1px solid transparent;
                border-radius: 5px; padding: 3px 12px; color: #626b78;
            }
            QToolButton#workspaceChoice:hover { background: #e3eaf5; color: #1748b5; }
            QToolButton#workspaceChoice:checked,
            QToolButton#workspaceChoice:checked:hover {
                background: transparent; color: #1748b5; font-weight: 600;
            }
            QToolButton#workspaceChoice:focus { border-color: #2563eb; }
            QToolButton#workspaceChoice:disabled { color: #9299a4; }
        ''')
        self.animation = QPropertyAnimation(self, b'slidePosition', self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.setAccessibleDescription(self.text())

    def sizeHint(self):
        metrics = self.fontMetrics()
        # Keep a readable four-em target even when the platform's CJK fallback
        # reports narrow/missing glyphs. A fixed 92 px floor must scale with the
        # font too, rather than masking FontChange at a larger text size.
        # An em is the font's pixel size, not its taller line-spacing height.
        # Using line height over-allocates on Linux and hides Stop at 480 px.
        em = self.fontInfo().pixelSize()
        width = max(92, max(max(metrics.horizontalAdvance(a.text()), 4 * em)
                            for a in self._actions) + 32)
        return QSize(width * len(self.buttons) + 8, max(36, metrics.height() + 16))

    def minimumSizeHint(self):
        return self.sizeHint()

    def currentIndex(self):
        return self._index

    def text(self):
        # Retain the previous selector's read-only current-workspace summary.
        return '当前：' + self._actions[self._index].text()

    def setCurrentIndex(self, index):
        if self._index == index:
            return
        self.animation.stop()
        self._index = index
        self.setAccessibleDescription(self.text())
        if self.isVisible():
            self.animation.setStartValue(self._position)
            self.animation.setEndValue(float(index))
            self.animation.start()
        else:
            self.slidePosition = float(index)

    def _getPosition(self):
        return self._position

    def _setPosition(self, value):
        self._position = float(value)
        self.update()

    slidePosition = Property(float, _getPosition, _setPosition)

    def selectedRect(self):
        first, last = self.buttons[0].geometry(), self.buttons[-1].geometry()
        return QRectF(first.x() + (last.x() - first.x()) * self._position,
                      first.y(), first.width(), first.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor('#d8e0ed'), 1))
        painter.setBrush(QColor('#f0f3f8'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#dce8ff' if self.isEnabled() else '#e5e7eb'))
        painter.drawRoundedRect(self.selectedRect(), 5, 5)

    def hideEvent(self, event):
        self.animation.stop()
        self.slidePosition = float(self._index)
        super().hideEvent(event)
