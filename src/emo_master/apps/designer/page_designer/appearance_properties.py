"""Native property forms for page composition and the project shell."""
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from emo_master.core.presentation.models import PageAppearance, PageShell, Props, walkComponents


class SettingsDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 620)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        self.fields = QVBoxLayout(panel)
        scroll.setWidget(panel)
        layout.addWidget(scroll, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def group(self, title, enabled=None):
        box = QGroupBox(title)
        if enabled is not None:
            box.setCheckable(True)
            box.setChecked(enabled)
            box.setToolTip('取消勾选可恢复当前默认')
        form = QFormLayout(box)
        self.fields.addWidget(box)
        return box, form

    def run(self, build):
        self.build = build
        self.fields.addStretch()
        return self.resultValue if self.exec_() else None

    def accept(self):
        try:
            self.resultValue = self.build()
        except ValueError as error:
            self.error.setText(str(error))
        else:
            super().accept()


def choice(form, title, choices, value):
    field = QComboBox()
    for label, key in choices:
        field.addItem(label, key)
    field.setCurrentIndex(max(0, field.findData(value)))
    form.addRow(title, field)
    return field.currentData


def size(form, title, maximum, value):
    field = QSpinBox()
    field.setRange(0, maximum)
    field.setValue(value)
    form.addRow(title, field)
    return field.value


def optionalSize(form, title, maximum, value, minimum=0):
    field = QSpinBox()
    field.setRange(minimum - 1, maximum)
    field.setSpecialValueText('使用默认')
    field.setValue(minimum - 1 if value is None else value)
    form.addRow(title, field)
    return lambda: None if field.value() < minimum else field.value()


def line(form, title, value):
    field = QLineEdit(value)
    form.addRow(title, field)
    return field.text


def toggle(form, title, value):
    field = QCheckBox(title)
    field.setChecked(value)
    form.addRow(field)
    return field.isChecked


def surfaceFields(dialog, title, value):
    surface = value or {}
    box, form = dialog.group(title, value is not None)
    kind = choice(form, '类型', [('使用默认', None), ('无边框组', 'none'), ('卡片', 'card')], surface.get('kind'))
    background = choice(form, '背景', [('使用默认', None), ('透明', 'transparent'), ('卡片', 'surface'),
                                      ('浅色', 'subtle')], surface.get('background'))
    border = choice(form, '边框', [('使用默认', None), ('无', 'none'), ('细边框', 'subtle')], surface.get('border'))
    radius = optionalSize(form, '圆角', 32, surface.get('radius'))
    padding = {side: optionalSize(form, label, 64, (surface.get('padding') or {}).get(side))
               for side, label in [('top', '上内边距'), ('right', '右内边距'),
                                   ('bottom', '下内边距'), ('left', '左内边距')]}
    return lambda: dict(kind=kind(), background=background(), border=border(), radius=radius(),
                        padding={key: field() for key, field in padding.items()}) if box.isChecked() else None


def textFields(form, title, value):
    style = value or {}
    default = [('使用默认', None)]
    return {
        'size': optionalSize(form, title + '字号', 96, style.get('size'), 8),
        'weight': choice(form, title + '字重', default + [('常规', 'normal'), ('粗体', 'bold')], style.get('weight')),
        'color': choice(form, title + '颜色', default + [('主文字', 'default'), ('次文字', 'neutral'),
                       ('主色', 'primary'), ('绿', 'green'), ('红', 'red'), ('琥珀', 'amber')], style.get('color')),
    }


def typographyFields(dialog, title, value, roles):
    typography = value or {}
    box, form = dialog.group(title, value is not None)
    fields = {role: textFields(form, label, typography.get(role)) for role, label in roles}
    return lambda: {role: {key: getter() for key, getter in values.items()}
                    for role, values in fields.items()} if box.isChecked() else None


def hostControlFields(dialog, title, value):
    control = value or {}
    _, form = dialog.group(title)
    label = line(form, '按钮文字（留空使用宿主默认）', control.get('label') or '')
    appearance = control.get('appearance') or {}
    custom, form = dialog.group(title + '外观（勾选以自定义）', control.get('appearance') is not None)
    variant = choice(form, '按钮样式', [('使用默认', None), ('主按钮', 'primary'), ('次按钮', 'secondary'),
                                     ('链接', 'link')], appearance.get('style'))
    text = textFields(form, '按钮文字', appearance.get('text'))
    radius = optionalSize(form, '圆角', 32, appearance.get('radius'))
    width = optionalSize(form, '最小宽度', 512, appearance.get('minWidth'))
    height = optionalSize(form, '最小高度', 128, appearance.get('minHeight'))
    icon = toggle(form, '显示动作图标', appearance.get('showIcon', False))
    padding = {side: optionalSize(form, name, 64, (appearance.get('padding') or {}).get(side))
               for side, name in [('top', '上内边距'), ('right', '右内边距'),
                                  ('bottom', '下内边距'), ('left', '左内边距')]}

    def result():
        style = dict(style=variant(), text={key: getter() for key, getter in text.items()},
                     radius=radius(), minWidth=width(), minHeight=height(), showIcon=icon(),
                     padding={key: getter() for key, getter in padding.items()}) if custom.isChecked() else None
        return dict(label=label() or None, appearance=style) if label() or style is not None else None
    return result


def editPageAppearance(parent, presentation, resources):
    dialog = SettingsDialog(parent, '页面设置 · 全局外观')
    appearance = presentation.appearance or PageAppearance()
    outer, form = dialog.group('页面外观（勾选以自定义）', presentation.appearance is not None)
    width = size(form, '内容最大宽度（0 为铺满）', 4096, appearance.contentMaxWidth)
    padding = size(form, '页面内边距', 64, appearance.pagePadding)
    shell = presentation.shell or PageShell()
    header, form = dialog.group('页头（勾选以自定义）', presentation.shell is not None)
    title = line(form, '标题', shell.title)
    subtitle = line(form, '副标题', shell.subtitle)
    logos = [(f'{key} · {resource.path}', key) for key, resource in resources.items()
             if resource.purpose in {'page_image', 'icon'}]
    logo = choice(form, '标志图片', [('无', None), *logos], shell.logoResourceId)
    logoWidth = optionalSize(form, 'Logo 显示宽度', 512, shell.logoWidth, 1)
    logoHeight = optionalSize(form, 'Logo 显示高度', 256, shell.logoHeight, 1)
    diagnostics = toggle(form, '初始展开技术诊断', shell.diagnosticsInitiallyExpanded)
    connection = toggle(form, '显示连接状态', shell.showConnection)
    job = toggle(form, '显示任务状态', shell.showJobState)
    controls = toggle(form, '显示宿主开始 / 停止', shell.showControls)
    shellProps = shell.model_dump(exclude_none=True)
    shellGroupsFrom = dialog.fields.count()
    headerSurface = surfaceFields(dialog, '页头表面（勾选以自定义）', shellProps.get('headerSurface'))
    headerTypography = typographyFields(dialog, '页头文字（勾选以自定义）', shellProps.get('headerTypography'),
                                        [('title', '标题'), ('helper', '副标题')])
    startControl = hostControlFields(dialog, '开始按钮', shellProps.get('startControl'))
    stopControl = hostControlFields(dialog, '停止按钮', shellProps.get('stopControl'))
    for index in range(shellGroupsFrom, dialog.fields.count()):
        group = dialog.fields.itemAt(index).widget()
        group.setEnabled(header.isChecked())
        header.toggled.connect(group.setEnabled)
    _, form = dialog.group('页面导航')
    navigation = choice(form, '导航样式', [('页签', 'tabs'), ('按钮', 'buttons')], presentation.navigation.mode)

    def result():
        return (PageAppearance(contentMaxWidth=width(), pagePadding=padding()) if outer.isChecked() else None,
                PageShell(title=title(), subtitle=subtitle(), logoResourceId=logo(),
                          logoWidth=logoWidth() or 40, logoHeight=logoHeight() or 40,
                          headerSurface=headerSurface(), headerTypography=headerTypography(),
                          startControl=startControl(), stopControl=stopControl(),
                          diagnosticsInitiallyExpanded=diagnostics(), showConnection=connection(),
                          showJobState=job(), showControls=controls()) if header.isChecked() else None,
                navigation())
    return dialog.run(result)


def editComponentAppearance(parent, component, page):
    dialog = SettingsDialog(parent, '组件外观与表现')
    props = component.props.model_dump(exclude_none=True)
    surface = surfaceFields(dialog, '表面（勾选以自定义）', props.get('surface'))
    typography = typographyFields(dialog, '文字（勾选以自定义）', props.get('typography'),
                                   [('title', '标题'), ('value', '主值'), ('helper', '辅助')])
    default = [('使用默认', None)]
    _, form = dialog.group('显示')
    getters = {}
    if component.type in {'text', 'number', 'indicator', 'runtime_status'}:
        getters['valueLayout'] = choice(form, '标题与主值', [('上下', 'vertical'), ('并排', 'horizontal')], props['valueLayout'])
        getters['helperText'] = line(form, '辅助文字', props['helperText'])
    if component.type == 'indicator':
        getters['indicatorView'] = choice(form, '状态样式', [('文字', 'text'), ('徽标', 'badge'), ('状态行', 'row')], props['indicatorView'])
        getters['showMarker'] = choice(form, '状态圆点', default + [('显示', True), ('隐藏', False)], props.get('showMarker'))
        getters['markerPosition'] = choice(form, '圆点位置', [('状态值前', 'value'), ('标题前', 'title')], props['markerPosition'])
        getters['titleFollowsState'] = toggle(form, '标题跟随状态颜色', props['titleFollowsState'])
    if component.type == 'runtime_status' and not component.bindings:
        getters['statusField'] = choice(form, '宿主状态', [('默认连接', None), ('连接', 'connection'),
                                                         ('任务', 'job_state')], props.get('statusField'))
    if component.type == 'navigation_button':
        getters['buttonStyle'] = choice(form, '按钮外观', [('主按钮', 'primary'), ('次按钮', 'secondary'), ('链接', 'link')], props['buttonStyle'])
    if component.type == 'container':
        collapse = props.get('collapse')
        state = choice(form, '展开控制', [('始终展开', None), ('初始展开', False), ('初始折叠', True)],
                       collapse.get('initiallyCollapsed') if collapse else None)
        getters['collapse'] = lambda: {'initiallyCollapsed': state()} if state() is not None else None
    if component.type == 'image':
        image = props.get('imageView', {})
        area, form = dialog.group('图像（勾选以自定义）', 'imageView' in props)
        sizing = choice(form, '尺寸方式', [('宽高比', 'aspect'), ('固定高度', 'fixed')], image.get('sizing', 'aspect'))
        ratio = QDoubleSpinBox()
        ratio.setRange(0, 1000)
        ratio.setDecimals(4)
        ratio.setSpecialValueText('根据图像')
        ratio.setValue(image.get('aspectRatio', 0))
        form.addRow('宽高比', ratio)
        backdrop = choice(form, '留白背景', [('浅色', 'subtle'), ('深色', 'dark'), ('透明', 'transparent')], image.get('background', 'subtle'))
        positions = [('起始', 'start'), ('居中', 'center'), ('末尾', 'end')]
        horizontal = choice(form, '水平对齐', positions, image.get('horizontalAlign', 'center'))
        vertical = choice(form, '垂直对齐', positions, image.get('verticalAlign', 'center'))
        getters['imageView'] = lambda: dict(sizing=sizing(), aspectRatio=ratio.value() or None,
            background=backdrop(), horizontalAlign=horizontal(), verticalAlign=vertical()) if area.isChecked() else None
    if component.type == 'sqlite_table':
        history = props.get('historyView', {})
        _, form = dialog.group('历史列表')
        mode = choice(form, '显示方式', [('表格', 'table'), ('列表', 'list')], history.get('mode', 'table'))
        columns = [('不显示', None)] + [(field, field) for field in component.props.sqlite.columns]
        slots = {key: choice(form, title, columns, history.get(key)) for key, title in
                 [('titleField', '标题字段'), ('subtitleField', '副标题字段'), ('trailingField', '末尾字段')]}
        bars = {key: toggle(form, title, history.get(key, True)) for key, title in
                [('showFilter', '显示筛选栏'), ('showSort', '显示排序控制'), ('showPagination', '显示分页栏')]}
        getters['historyView'] = lambda: {'mode': mode(), **{key: field() for key, field in slots.items()},
                                         **{key: field() for key, field in bars.items()}}
    if component.type in {'text', 'number', 'indicator'}:
        _, form = dialog.group('历史记录字段')
        sources = [('未选择', None)]
        for table in walkComponents(page.components):
            if table.type != 'sqlite_table':
                continue
            key = table.componentId
            name = table.props.title or key
            sources.extend((f'{name} · {field}', dict(componentId=key, kind='field', field=field))
                           for field in table.props.sqlite.columns)
            sources.extend((f'{name} · {title}', dict(componentId=key, kind='meta', meta=field))
                           for title, field in [('位置', 'position'), ('总数', 'count')])
        getters['recordValue'] = choice(form, '使用当前所选记录', sources, props.get('recordValue'))

    def result():
        updated = {key: field() for key, field in getters.items()}
        updated['surface'] = surface()
        updated['typography'] = typography()
        Props.model_validate({**props, **updated})
        return updated
    return dialog.run(result)
