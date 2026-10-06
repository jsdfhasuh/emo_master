"""Opt-in view-only samples. No SessionView, identities, bindings or project writes."""
from PySide2.QtCore import Qt, QRect
from PySide2.QtGui import QImage, QPainter, QColor, QPen
from emo_master.core.presentation.models import TableColumn
from .table import CollectionView


def sampleImage():
    image = QImage(320, 180, QImage.Format_ARGB32)
    image.fill(QColor('#e9f1fb'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#2563eb'), 2))
    painter.setBrush(QColor('#c4dcfb'))
    painter.drawRoundedRect(QRect(36, 35, 102, 85), 10, 10)
    painter.setBrush(QColor('#b7e4cd'))
    painter.drawEllipse(QRect(187, 48, 68, 68))
    painter.setPen(QColor('#334155'))
    painter.drawText(QRect(0, 138, 320, 30), Qt.AlignCenter, '示例数据 · 非检测图像')
    painter.end()
    return image


def populate(renderer):
    from .renderer import ImageView, COLORS, appearanceStyle
    for component, widget in renderer.widgets.get(renderer.currentPageId, {}).values():
        props = component.props
        if isinstance(widget, ImageView):
            if renderer._designImage.isNull():
                renderer._designImage = sampleImage()
            widget.setImage(renderer._designImage, '', '示例数据')
        elif isinstance(widget, CollectionView):
            columns = props.columns or [TableColumn(title='序号', fieldPath=['index']),
                                        TableColumn(title='示例值', fieldPath=['value'])]
            widget.model.columns = columns
            rows = []
            for index in range(3):
                row = {}
                for column in columns:
                    current = row
                    for part in column.fieldPath[:-1]:
                        if not isinstance(current.get(part), dict):
                            current[part] = {}
                        current = current.setdefault(part, {})
                    if column.fieldPath:
                        current.setdefault(column.fieldPath[-1], index + 1)
                rows.append(row)
            widget.model.replace(rows, '')
            widget.message.setText('示例数据 · 3 行；真实表格按已配置列显示')
        elif component.type == 'number':
            widget.setText(f'{128.5:.{props.decimals}f}' + props.unit)
        elif component.type == 'indicator':
            style = next(iter(props.indicatorStates.values()), None)
            widget.setText((style.text if style else '● OK') + ' · 示例')
            widget.setStyleSheet(appearanceStyle(props) + 'color:' + COLORS[style.color if style else 'green'] + ';')
        elif component.type == 'runtime_status':
            widget.setText('示例数据 · 客户端连接状态')
        elif component.type == 'text' and (component.bindings or not props.text):
            widget.setText(props.text or '示例文字 · 检测信息')
    renderer.displayed.clear()
    renderer.banner.setText('示例数据 · 非检测结果')
    renderer.identity.setText('示例数据没有检测身份')


def clear(renderer):
    from .renderer import ImageView, appearanceStyle
    for rows in renderer.widgets.values():
        for component, widget in rows.values():
            if isinstance(widget, ImageView):
                widget.setImage(QImage(), '', '已绑定 · 等待明确任务结果' if component.bindings else '未绑定')
            elif isinstance(widget, CollectionView):
                widget.model.columns = component.props.columns
                widget.clear('尚无集合')
            elif component.type != 'navigation_button':
                widget.setStyleSheet(appearanceStyle(component.props))
                widget.setText(component.props.text if component.type == 'text' and not component.bindings
                               else '已绑定 · 等待明确任务结果' if component.bindings else '未绑定')
    renderer._designImage = QImage()
    renderer.banner.setText('编辑布局 · 无示例数据 · 等待明确选择任务')
    renderer.identity.setText('尚无已显示结果')
