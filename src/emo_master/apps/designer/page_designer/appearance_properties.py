"""Native property forms for page composition and the project shell."""
from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea,
    QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.core.presentation.models import PageAppearance, PageShell, Props, walkComponents
from emo_panel_protocol.models import HostControl
from .appearance_preview import ComponentAppearancePreview, CustomQssFields, disclosure


class SettingsDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 620)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.panel = QWidget()
        self.fields = QVBoxLayout(self.panel)
        scroll.setWidget(self.panel)
        self.body = QHBoxLayout()
        self.body.addWidget(scroll, 3)
        layout.addLayout(self.body, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color:#b91c1c')
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

    def run(self, build, refresh=()):
        self.build = build
        if refresh:
            timer = QTimer(self)
            timer.setSingleShot(True)

            def update():
                for preview in refresh:
                    preview()

            def schedule(*_):
                self.error.clear()
                timer.start(135)

            for widgetType, signal in ((QCheckBox, 'toggled'), (QGroupBox, 'toggled'), (QToolButton, 'toggled'),
                                       (QComboBox, 'currentIndexChanged'), (QLineEdit, 'textChanged'),
                                       (QPlainTextEdit, 'textChanged'), (QAbstractSpinBox, 'valueChanged')):
                for widget in self.panel.findChildren(widgetType):
                    getattr(widget, signal).connect(schedule)
            timer.timeout.connect(update)
            timer.start(0)
        self.fields.addStretch()
        try:
            return self.resultValue if self.exec_() else None
        finally:
            self.deleteLater()

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


def colorField(form, title, choices, value, *, default=None):
    field = QComboBox()
    field.setEditable(True)
    field.setInsertPolicy(QComboBox.NoInsert)
    field.lineEdit().setPlaceholderText('#RRGGBB')
    field.setToolTip('可选预设颜色，也可输入 #RRGGBB')
    for label, key in choices:
        field.addItem(label, key)
    if value is not None and field.findData(value) < 0:
        field.addItem(value, value)
    field.setCurrentIndex(max(0, field.findData(value)))
    form.addRow(title, field)

    def result():
        text = field.currentText().strip()
        if field.currentIndex() >= 0 and text == field.itemText(field.currentIndex()):
            return field.currentData()
        return text or default
    return result


def borderFields(form, value):
    return {
        'borderColor': colorField(form, '边框颜色', [('使用默认', None)], value.get('borderColor')),
        'borderWidth': optionalSize(form, '边框宽度', 16, value.get('borderWidth')),
        'borderStyle': choice(form, '边框线型', [('使用默认', None), ('实线', 'solid'),
                            ('虚线', 'dashed'), ('点线', 'dotted')], value.get('borderStyle')),
    }


def fieldValues(fields):
    return {key: getter() for key, getter in fields.items()}


def surfaceFields(dialog, title, value):
    surface = value or {}
    box, form = dialog.group(title, value is not None)
    kind = choice(form, '类型', [('使用默认', None), ('无边框组', 'none'), ('卡片', 'card')], surface.get('kind'))
    background = colorField(form, '背景', [('使用默认', None), ('透明', 'transparent'), ('卡片', 'surface'),
                                            ('浅色', 'subtle')], surface.get('background'))
    border = choice(form, '边框', [('使用默认', None), ('无', 'none'), ('细边框', 'subtle')], surface.get('border'))
    detail = borderFields(form, surface)
    radius = optionalSize(form, '圆角', 32, surface.get('radius'))
    padding = {side: optionalSize(form, label, 64, (surface.get('padding') or {}).get(side))
               for side, label in [('top', '上内边距'), ('right', '右内边距'),
                                   ('bottom', '下内边距'), ('left', '左内边距')]}
    return lambda: dict(kind=kind(), background=background(), border=border(), radius=radius(),
                        padding=fieldValues(padding), **fieldValues(detail)) if box.isChecked() else None


def textFields(form, title, value):
    style = value or {}
    default = [('使用默认', None)]
    return {
        'size': optionalSize(form, title + '字号', 96, style.get('size'), 8),
        'weight': choice(form, title + '字重', default + [('常规', 'normal'), ('粗体', 'bold')], style.get('weight')),
        'color': colorField(form, title + '颜色', default + [('主文字', 'default'), ('次文字', 'neutral'),
                       ('主色', 'primary'), ('绿', 'green'), ('红', 'red'), ('琥珀', 'amber')], style.get('color')),
    }


def typographyFields(dialog, title, value, roles):
    typography = value or {}
    box, form = dialog.group(title, value is not None)
    fields = {role: textFields(form, label, typography.get(role)) for role, label in roles}
    return lambda: {**typography, **{role: fieldValues(values) for role, values in fields.items()}} if box.isChecked() else None


BUTTON_STATES = [('默认', 'default'), ('悬停', 'hover'), ('按下', 'pressed'), ('禁用', 'disabled')]
DEFAULT_BACKGROUND = [('使用默认', None), ('透明', 'transparent')]


def iconFields(form, value):
    return {
        'iconPosition': choice(form, '图标位置', [('使用默认', None), ('左侧', 'left'), ('右侧', 'right')], value.get('iconPosition')),
        'iconSize': optionalSize(form, '图标大小', 64, value.get('iconSize'), 8),
        'iconGap': optionalSize(form, '图文间距', 32, value.get('iconGap')),
    }


def stateForms(form, caption, entries, configure):
    selector = QComboBox()
    form.addRow(caption, selector)
    stack = QStackedWidget()
    form.addRow(stack)
    fields = {}
    for label, key, value in entries:
        selector.addItem(label, key)
        page = QWidget()
        state = QFormLayout(page)
        state.setContentsMargins(0, 0, 0, 0)
        fields[key] = configure(state, value)
        stack.addWidget(page)
    selector.currentIndexChanged.connect(stack.setCurrentIndex)
    return selector.currentData, fields


def buttonStatesFields(form, value):
    def configure(state, current):
        return {
            'background': colorField(state, '状态背景', DEFAULT_BACKGROUND, current.get('background')),
            'color': colorField(state, '状态文字', [('使用默认', None)], current.get('color')),
            **borderFields(state, current),
        }
    active, fields = stateForms(form, '按钮状态', [(label, key, value.get(key, {}))
                                                   for label, key in BUTTON_STATES], configure)

    def result():
        result = {}
        for name, settings in fields.items():
            state = {key: item for key, item in fieldValues(settings).items() if item is not None}
            if state:
                result[name] = state
        return result or None
    return active, result


def indicatorStyleFields(form, value):
    def configure(state, current):
        return {
            'color': colorField(state, '状态文字 / 圆点颜色',
                [('中性', 'neutral'), ('绿', 'green'), ('红', 'red'), ('琥珀', 'amber'), ('蓝', 'blue')], current['color'],
                default='neutral'),
            'background': colorField(state, '状态背景', [('无', 'none'), ('浅底', 'soft')], current['background'], default='none'),
            **borderFields(state, current),
        }
    active, fields = stateForms(form, '预览业务状态', [(style['text'] or key, key, style)
                                                       for key, style in value.items()], configure)
    return active, lambda: {key: {**style, **fieldValues(fields[key])} for key, style in value.items()}


def hostControlFields(dialog, title, kind, value):
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
    panel = QWidget()
    expand = disclosure('高级外观…', panel)
    expand.setObjectName(kind + 'AdvancedAppearance')
    form.addRow(expand)
    form.addRow(panel)
    details = QFormLayout(panel)
    details.setContentsMargins(0, 0, 0, 0)
    background = colorField(details, '按钮背景', DEFAULT_BACKGROUND, appearance.get('background'))
    border = borderFields(details, appearance)
    iconLayout = iconFields(details, appearance)
    active, states = buttonStatesFields(details, appearance.get('buttonStates', {}))
    qss = CustomQssFields(appearance.get('customStyle'))
    qss.setObjectName(kind + 'HostQss')
    details.addRow(qss)
    from emo_master.ui.presentation.shell import AppearanceButton
    sample = AppearanceButton()
    sample.setObjectName(kind + 'AppearancePreview')
    details.addRow('局部预览', sample)

    def result(*, preview=False):
        style = dict(style=variant(), text=fieldValues(text),
                     radius=radius(), minWidth=width(), minHeight=height(), showIcon=icon(),
                     padding=fieldValues(padding), background=background(), buttonStates=states(),
                     customStyle=qss.result(preview=preview), **fieldValues(border),
                     **fieldValues(iconLayout)) if custom.isChecked() else None
        return dict(label=label() or None, appearance=style) if label() or style is not None else None

    def updatePreview():
        if not expand.isChecked() or not expand.isEnabled():
            return
        from emo_master.ui.presentation.native_style import selectorHints
        from emo_master.ui.presentation.shell import configureHostButton
        try:
            control = HostControl.model_validate(result(preview=True) or {})
        except ValueError:
            return
        configureHostButton(sample, control, kind=kind, fallbackLabel=title.removesuffix('按钮'), tooltip='局部外观预览')
        sample.setPreviewState(active())
        qss.setSelectors(selectorHints(sample))
    return result, updatePreview


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
    startControl, updateStart = hostControlFields(dialog, '开始按钮', 'start', shellProps.get('startControl'))
    stopControl, updateStop = hostControlFields(dialog, '停止按钮', 'stop', shellProps.get('stopControl'))
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
    return dialog.run(result, (updateStart, updateStop))


def editComponentAppearance(parent, component, page, *, advanced=False, presentation=None, pageId=None,
                            assets=None, projectRoot=None, view=None):
    import json

    dialog = SettingsDialog(parent, '组件高级外观' if advanced else '组件外观与表现')
    props = component.props.model_dump(exclude_none=True)
    qss = CustomQssFields(props.get('customStyle'), expanded=advanced)
    if advanced:
        dialog.fields.addWidget(qss)
    surface = surfaceFields(dialog, '表面（勾选以自定义）', props.get('surface'))
    roles = []
    if ((props['title'] and component.type != 'navigation_button') or component.type == 'page_title'
            or (component.type == 'page_navigation' and props['text'])):
        roles.append(('title', '标题'))
    mainText = {'navigation_button': '按钮文字', 'page_navigation': '导航项文字',
                'table': '表格文字', 'sqlite_table': '记录文字'}.get(component.type)
    if mainText or component.type in ('text', 'number', 'indicator', 'runtime_status', 'clock', 'sqlite_value'):
        roles.append(('value', mainText or '主值'))
    if component.type in ('text', 'number', 'indicator', 'runtime_status', 'image', 'table', 'sqlite_table', 'sqlite_value'):
        roles.append(('helper', '辅助'))
    typography = (typographyFields(dialog, '文字（勾选以自定义）', props.get('typography'), roles)
                  if roles else lambda: props.get('typography'))
    default = [('使用默认', None)]
    if component.type in ('text', 'number', 'indicator', 'runtime_status', 'navigation_button', 'container'):
        _, form = dialog.group('显示')
    getters = {}
    activeState = lambda: None
    activeButtonState = lambda: None
    if component.type in {'text', 'number', 'indicator', 'runtime_status'}:
        getters['valueLayout'] = choice(form, '标题与主值', [('上下', 'vertical'), ('并排', 'horizontal')], props['valueLayout'])
        getters['helperText'] = line(form, '辅助文字', props['helperText'])
    if component.type == 'indicator':
        getters['indicatorView'] = choice(form, '状态样式', [('文字', 'text'), ('徽标', 'badge'), ('状态行', 'row')], props['indicatorView'])
        getters['showMarker'] = choice(form, '状态圆点', default + [('显示', True), ('隐藏', False)], props.get('showMarker'))
        getters['markerPosition'] = choice(form, '圆点位置', [('状态值前', 'value'), ('标题前', 'title')], props['markerPosition'])
        getters['titleFollowsState'] = toggle(form, '标题跟随状态颜色', props['titleFollowsState'])
        if props['indicatorStates']:
            _, statesForm = dialog.group('状态外观')
            activeState, getters['indicatorStates'] = indicatorStyleFields(statesForm, props['indicatorStates'])
    if component.type == 'runtime_status' and not component.bindings:
        getters['statusField'] = choice(form, '宿主状态', [('默认连接', None), ('连接', 'connection'),
                                                         ('任务', 'job_state')], props.get('statusField'))
    if component.type == 'navigation_button':
        getters['buttonStyle'] = choice(form, '按钮外观', [('主按钮', 'primary'), ('次按钮', 'secondary'), ('链接', 'link')], props['buttonStyle'])
        if component.props.iconResourceId:
            getters.update(iconFields(form, props))
        _, statesForm = dialog.group('按钮状态外观')
        activeButtonState, getters['buttonStates'] = buttonStatesFields(statesForm, props.get('buttonStates', {}))
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
        backdrop = colorField(form, '留白背景', [('浅色', 'subtle'), ('深色', 'dark'), ('透明', 'transparent')],
                              image.get('background', 'subtle'), default='subtle')
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

    if not advanced:
        dialog.fields.addWidget(qss)

    def result(*, preview=False):
        updated = fieldValues(getters)
        updated['surface'] = surface()
        updated['typography'] = typography()
        updated['customStyle'] = qss.result(preview=preview)
        Props.model_validate({**props, **updated})
        return updated

    if presentation is None:
        return dialog.run(result)

    dialog.resize(850, 680)
    previewBox = QGroupBox('局部预览')
    previewLayout = QVBoxLayout(previewBox)
    previewLayout.setContentsMargins(8, 8, 8, 8)
    previewHint = QLabel(('当前画面数据' if view and view.scopes else '示例数据') + ' · 不执行业务动作')
    previewHint.setWordWrap(True)
    previewHint.setStyleSheet('color:#64748b')
    previewLayout.addWidget(previewHint)
    dialog.body.addWidget(previewBox, 2, Qt.AlignTop)
    stage = None
    shownProps = None

    def refreshPreview():
        nonlocal stage, shownProps
        from emo_master.ui.presentation.native_style import selectorHints
        try:
            values = Props.model_validate({**props, **result(preview=True)})
        except ValueError:
            return
        if stage is None or shownProps != values:
            if stage is not None:
                stage.hide()
                previewLayout.removeWidget(stage)
                stage.deleteLater()
            draft = component.model_copy(update={'props': values})
            stage = ComponentAppearancePreview(presentation, pageId, draft, assets=assets,
                                               projectRoot=projectRoot, view=view)
            previewLayout.addWidget(stage, 1)
            shownProps = values
        state = activeState()
        if state is not None:
            stage.widgets[pageId][component.componentId][1].setValue(json.loads(state))
        state = activeButtonState()
        if state is not None:
            stage.widgets[pageId][component.componentId][1].setPreviewState(state)
        qss.setSelectors(selectorHints(stage.componentCard))
    return dialog.run(result, (refreshPreview,))
