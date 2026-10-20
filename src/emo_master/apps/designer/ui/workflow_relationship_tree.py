"""Width-aware call hierarchy with escaped labels and collapsible data routes."""
from __future__ import annotations

from html import escape
from math import ceil

from PySide2.QtCore import QRect, QSize, Qt
from PySide2.QtGui import QColor, QIcon, QAbstractTextDocumentLayout, QPalette, QTextDocument, QTextOption
from PySide2.QtWidgets import (
    QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget,
)

from .icon_map import icon


class _WrappedRelationshipDelegate(QStyledItemDelegate):
    def __init__(self, tree):
        super().__init__(tree)
        self.tree = tree

    def _document(self, option, width, index):
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(option.font)
        textOption = QTextOption()
        textOption.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(textOption)
        payload = index.data(Qt.UserRole) or {}
        if payload.get("itemType") in {"workflow", "call"}:
            # Qt's initStyleOption replaces newlines with Unicode line separators.
            title, separator, subtitle = option.text.replace("\u2028", "\n").partition("\n")
            muted = option.palette.color(QPalette.HighlightedText).name() if (
                option.state & QStyle.State_Selected) else "#64748b"
            document.setHtml(f"<b>{escape(title)}</b>" + (
                f'<br><span style="color:{muted}">{escape(subtitle)}</span>' if separator else ""))
        else:
            document.setPlainText(option.text.replace("\u2028", "\n"))
        document.setTextWidth(max(1, width - 8 - (22 if not option.icon.isNull() else 0)))
        return document

    def sizeHint(self, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        depth = 1
        ancestor = index.parent()
        while ancestor.isValid():
            depth += 1
            ancestor = ancestor.parent()
        width = max(1, self.tree.columnWidth(index.column()) - self.tree.indentation() * depth)
        document = self._document(styled, width, index)
        # Rich bold headings and plain-text fallback can have different line
        # leading on Linux fonts. Reserve enough for both, with the same wrap.
        plain = QTextDocument()
        plain.setDocumentMargin(0)
        plain.setDefaultFont(styled.font)
        plain.setDefaultTextOption(document.defaultTextOption())
        plain.setPlainText(styled.text.replace("\u2028", "\n"))
        plain.setTextWidth(document.textWidth())
        return QSize(width, ceil(max(document.size().height(), plain.size().height())) + 6)

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        # Use the same layout for measurement and painting, including long IDs.
        document = self._document(styled, option.rect.width(), index)
        decoration = QIcon(styled.icon)
        iconWidth = 22 if not decoration.isNull() else 0
        styled.icon = QIcon()
        styled.text = ""
        self.tree.style().drawControl(QStyle.CE_ItemViewItem, styled, painter, self.tree)
        payload = index.data(Qt.UserRole) or {}
        if payload.get("itemType") == "call" and not styled.state & (
                QStyle.State_Selected | QStyle.State_MouseOver):
            painter.fillRect(option.rect, QColor("#edf7f5"))
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = styled.palette
        if styled.state & QStyle.State_Selected:
            context.palette.setColor(QPalette.Text, styled.palette.color(QPalette.HighlightedText))
        painter.save()
        painter.setClipRect(option.rect)
        if iconWidth:
            decoration.paint(painter, QRect(option.rect.left() + 3, option.rect.top() + 4, 16, 16))
        painter.translate(option.rect.left() + 4 + iconWidth, option.rect.top() + 3)
        document.documentLayout().draw(painter, context)
        painter.restore()


class WorkflowRelationshipTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setIndentation(12)
        self.setUniformRowHeights(False)
        self.setIconSize(QSize(16, 16))
        self.setExpandsOnDoubleClick(False)
        self.setItemDelegate(_WrappedRelationshipDelegate(self))
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.header().sectionResized.connect(self._relayout)

    def styleItem(self, item, entry):
        kind = entry.get("itemType")
        warning = entry.get("status") in {"missing", "cycle"}
        if kind == "call":
            loop = entry.get("relation") != "subflow"
            item.setIcon(0, icon("refresh-cw" if loop else "git-branch", "#0f766e"))
            item.setBackground(0, QColor("#edf7f5"))
        elif kind == "workflow":
            item.setIcon(0, icon("git-branch", "#b45309" if warning else "#2563eb"))
        elif kind == "data":
            item.setIcon(0, icon("layout-grid", "#64748b"))
        if kind in {"data", "control", "group"}:
            item.setForeground(0, QColor("#64748b"))
        if warning:
            item.setForeground(0, QColor("#b45309"))

    def _itemsWithKeys(self):
        def visit(parent, path):
            for i in range(parent.childCount()):
                item = parent.child(i)
                payload = item.data(0, Qt.UserRole)
                identity = tuple(str(payload.get(key, "")) for key in (
                    "itemType", "workflowId", "sourceNodeId", "relation"
                )) if isinstance(payload, dict) else (item.text(0),)
                key = (*path, identity)
                yield key, item
                yield from visit(item, key)
        yield from visit(self.invisibleRootItem(), ())

    def captureViewState(self):
        expanded = {}
        selected = None
        for key, item in self._itemsWithKeys():
            expanded[key] = item.isExpanded()
            if item is self.currentItem():
                selected = key
        return expanded, selected, self.verticalScrollBar().value()

    def restoreViewState(self, state):
        expanded, selected, scroll = state or ({}, None, 0)
        items = list(self._itemsWithKeys())
        for key, item in items:
            if key == selected:
                self.setCurrentItem(item)
        # Selecting a descendant can expand its ancestors in Qt.
        for key, item in items:
            payload = item.data(0, Qt.UserRole) or {}
            default = payload.get("itemType") in {"workflow", "call", "ports"}
            item.setExpanded(expanded.get(key, default))
        self.doItemsLayout()
        self.verticalScrollBar().setValue(scroll)

    def _relayout(self, *_args):
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
