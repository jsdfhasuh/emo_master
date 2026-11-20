"""Run after npm run build. Uses Edge and the editor's mocked draft API."""

import functools
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from editor_review_browser import EDGE, ROOT, Fixture, Handler, boot, component, presentation, save


def appearance_case(browser, origin):
    doc = presentation(two=True)
    page_id = doc['defaultPageId']
    number = component('styled-number')
    number.update(type='number', props={
        'title': '角度偏差', 'decimals': 1, 'unit': '°',
        'surface': {'background': '#F0F5FC', 'borderWidth': 1, 'borderColor': '#B9CCE4', 'borderStyle': 'solid'},
        'typography': {'value': {'size': 52, 'color': '#214D85', 'weight': 'normal'}},
        'customStyle': {'qtQss': {'enabled': True, 'code': '*[emoPart="value"] { font-weight: bold; }'}},
    })
    container = component('parent')
    container.update(type='container', children=[number], props={
        'title': '父容器',
        'customStyle': {'webCss': {'enabled': True, 'code': '''
            [data-emo-part="root"] { color: red; font-family: monospace; }
            [data-emo-part="root"] [data-emo-part="value"] { color: red; }
        '''}},
    })
    indicator = component('styled-indicator')
    indicator.update(type='indicator', props={
        'title': '方向判定', 'indicatorView': 'row', 'showMarker': True, 'markerPosition': 'title',
        'indicatorStates': {
            'true': {'text': '符合', 'color': '#11734C'},
            'false': {'text': '需复核', 'color': '#9B580A'},
        },
        'typography': {'value': {'size': 16, 'weight': 'normal'}},
    })
    doc['pages'][page_id]['components'] = [container, component('neighbor'), indicator]
    for name, target in doc['pages'].items():
        navigation = component(f'nav-{name}')
        navigation.update(type='page_navigation', props={'customStyle': {'webCss': {'enabled': True, 'code': '''
            [data-emo-part="tab"][aria-current="page"], [data-emo-part="action"][aria-current="page"] {
                background: #214D85; color: white;
            }
        '''}}})
        target['components'].append(navigation)

    doc['shell'] = {'startControl': {'label': '开始检测', 'appearance': {
        'background': '#087F5B', 'text': {'size': 14, 'weight': 'normal', 'color': '#FFFFFF'},
        'radius': 8, 'padding': {'top': 8, 'right': 12, 'bottom': 8, 'left': 12},
        'minWidth': 100, 'minHeight': 44, 'showIcon': True, 'iconPosition': 'right', 'iconSize': 12, 'iconGap': 6,
        'borderColor': '#06664A', 'borderWidth': 1, 'borderStyle': 'solid',
        'buttonStates': {'hover': {'background': '#06664A'}, 'pressed': {'background': '#044D38'},
                         'disabled': {'background': '#E8EDF3', 'color': '#8190A3'}},
    }}, 'stopControl': {'label': '停止检测', 'appearance': {
        'showIcon': True, 'iconPosition': 'right', 'iconSize': 12, 'iconGap': 6,
        'customStyle': {'webCss': {'enabled': True, 'code': '''
            [data-emo-part="action"] { background: #FFF7E8; color: #9B580A; border: 1px solid #D8B57B; }
            [data-emo-part="action"]:enabled:hover { background: #FFF2E1; border-color: #C88A2F; }
            [data-emo-part="action"]:enabled:active { background: #F8E0B5; border-color: #995305; }
            [data-emo-part="action"]:disabled { background: #E8EDF3; color: #8190A3; border-color: #D2DCE6; }
        '''}},
    }}}
    fixture = Fixture(doc)
    context, page = boot(browser, origin, fixture)
    try:
        original = page.locator('[data-editor-component="styled-number"]')
        parent = page.locator('[data-editor-component="parent"]')
        expect(parent).to_have_css('color', 'rgb(255, 0, 0)')
        expect(original.locator('[data-emo-part="value"]')).to_have_css('color', 'rgb(33, 77, 133)')
        expect(page.locator('[data-editor-component="neighbor"] [data-emo-part="value"]')).not_to_have_css('color', 'rgb(255, 0, 0)')
        assert 'monospace' not in original.evaluate('(el) => getComputedStyle(el).fontFamily')
        expect(original).to_have_css('border-left-width', '1px')
        original.click()
        open_appearance = page.get_by_role('button', name='高级外观…', exact=True)
        dialog = page.get_by_role('dialog', name='高级外观 · 角度偏差')
        textarea = dialog.get_by_role('textbox', name='CSS 代码')
        enabled = dialog.get_by_label('启用自定义 CSS')
        confirm = dialog.get_by_role('button', name='确定', exact=True)
        sample = dialog.locator('.editor-appearance-sample [data-emo-part="root"]')
        rule = '''[data-emo-part="root"] { border-left-width: 4px; }
            [data-emo-part="value"], [data-emo-part="title"]:hover { font-weight: bold; }'''

        def edit_css():
            open_appearance.click()
            dialog.get_by_text('自定义 CSS', exact=True).click()

        edit_css()
        textarea.fill('@media print { p { color: red; } }')
        enabled.check()
        expect(confirm).to_be_disabled()
        expect(dialog.get_by_role('alert')).to_contain_text('当前代码未应用')
        expect(sample).to_have_css('border-left-width', '1px')
        enabled.uncheck()
        expect(confirm).to_be_enabled()
        confirm.click()
        saved = save(page, fixture)['presentation']
        assert saved['pages'][page_id]['components'][0]['children'][0]['props']['customStyle'] == {
            **number['props']['customStyle'], 'webCss': {'enabled': False, 'code': '@media print { p { color: red; } }'},
        }

        edit_css()
        expect(enabled).not_to_be_checked()
        border_style = dialog.get_by_label('边框线型', exact=True)
        for style in ('dotted', 'dashed'):
            border_style.select_option(style)
            expect(sample).to_have_css('border-left-style', style)
            expect(sample).to_have_css('border-left-width', '1px')
            expect(sample).to_have_css('border-left-color', 'rgb(185, 204, 228)')
        confirm.click()
        expect(original).to_have_css('border-left-style', 'dashed')
        page.get_by_role('button', name='撤销', exact=True).click()
        expect(original).to_have_css('border-left-style', 'solid')

        edit_css()
        textarea.fill(rule)
        enabled.check()
        expect(sample).to_have_css('border-left-width', '4px')
        expect(sample.locator('[data-emo-part="value"]')).to_have_css('font-weight', '700')
        expect(original).to_have_css('border-left-width', '1px')
        dialog.get_by_role('button', name='清空代码').click()
        expect(enabled).to_be_checked()
        expect(sample).to_have_css('border-left-width', '1px')
        page.keyboard.press('Control+z')
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        edit_css()
        expect(textarea).to_have_value('@media print { p { color: red; } }')
        dialog.get_by_role('button', name='关闭高级外观').click()
        expect(original).to_have_css('border-left-width', '1px')

        edit_css()
        textarea.fill(rule)
        enabled.check()
        confirm.click()
        expect(original).to_have_css('border-left-width', '4px')
        page.get_by_role('button', name='撤销', exact=True).click()
        expect(original).to_have_css('border-left-width', '1px')
        page.get_by_role('button', name='重做', exact=True).click()
        expect(original).to_have_css('border-left-width', '4px')
        save(page, fixture)

        page.reload()
        expect(original).to_have_css('border-left-width', '4px')
        original.click()
        page.get_by_role('button', name='复制组件', exact=True).click()
        duplicate = page.locator('.editor-component.selected')
        expect(duplicate).to_have_css('border-left-width', '4px')
        assert original.get_attribute('data-emo-owner') != duplicate.get_attribute('data-emo-owner')
        edit_css()
        dialog.get_by_role('button', name='清空代码').click()
        expect(enabled).to_be_checked()
        confirm.click()
        expect(duplicate).to_have_css('border-left-width', '1px')
        expect(original).to_have_css('border-left-width', '4px')
        final = save(page, fixture)['presentation']['pages'][page_id]['components'][0]['children']
        assert final[1]['props']['customStyle']['qtQss'] == number['props']['customStyle']['qtQss']
        assert final[1]['props']['customStyle']['webCss'] == {'enabled': True, 'code': ''}
        assert final[0]['props']['customStyle']['webCss'] == {'enabled': True, 'code': rule}

        page.locator('[data-editor-component="styled-indicator"]').click()
        open_appearance.click()
        indicator_dialog = page.get_by_role('dialog', name='高级外观 · 方向判定')
        indicator_sample = indicator_dialog.locator('.editor-appearance-sample')
        marker = indicator_sample.locator('[data-emo-part="title"] [data-emo-part="marker"]')
        value = indicator_sample.locator('[data-emo-part="value"]')
        expect(marker).to_be_visible()
        expect(marker).to_have_css('width', '6px')
        expect(marker).to_have_css('background-color', 'rgb(17, 115, 76)')
        expect(value).to_have_text('符合')
        expect(value).to_have_css('color', 'rgb(17, 115, 76)')
        indicator_dialog.get_by_text('自定义 CSS', exact=True).click()
        indicator_dialog.get_by_role('textbox', name='CSS 代码').fill(
            '[data-emo-part="marker"] { width: 10px; height: 10px; }')
        indicator_dialog.get_by_label('启用自定义 CSS').check()
        expect(marker).to_have_css('width', '10px')
        expect(marker).to_have_css('height', '10px')
        expect(value).to_have_css('font-size', '16px')
        expect(value).to_have_css('font-weight', '400')
        expect(value).to_have_css('color', 'rgb(17, 115, 76)')
        indicator_dialog.get_by_label('预览判定值').select_option('false')
        expect(marker).to_have_css('width', '10px')
        expect(marker).to_have_css('background-color', 'rgb(155, 88, 10)')
        expect(value).to_have_text('需复核')
        expect(value).to_have_css('color', 'rgb(155, 88, 10)')
        indicator_dialog.get_by_role('button', name='取消', exact=True).click()
        expect(page.locator('[data-editor-component="styled-indicator"] [data-emo-part="marker"]')).to_have_css('width', '6px')

        # Same host button on the canvas and in the original page-header entry.
        host = page.locator('.editor-canvas-header').get_by_role('button', name='开始检测')
        page.mouse.move(1, 1)
        expect(host).to_have_css('background-color', 'rgb(8, 127, 91)')
        expect(host).to_have_css('flex-direction', 'row-reverse')
        expect(host).to_have_css('gap', '6px')
        expect(host.locator('svg')).to_have_css('width', '12px')
        page.locator('.editor-properties').get_by_text('开始按钮', exact=True).click()
        page.get_by_role('button', name='开始按钮高级外观…', exact=True).click()
        host_dialog = page.get_by_role('dialog', name='高级外观 · 开始按钮')
        host_sample = host_dialog.get_by_role('button', name='开始检测', exact=True)
        host_sample.hover()
        expect(host_sample).to_have_css('background-color', 'rgb(6, 102, 74)')
        expect(host_sample).to_have_css('color', 'rgb(255, 255, 255)')
        expect(host_sample).to_have_css('border-color', 'rgb(6, 102, 74)')
        page.mouse.down()
        expect(host_sample).to_have_css('background-color', 'rgb(4, 77, 56)')
        page.mouse.up()
        host_dialog.get_by_label('预览禁用状态').check()
        expect(host_sample).to_be_disabled()
        expect(host_sample).to_have_css('background-color', 'rgb(232, 237, 243)')
        expect(host_sample).to_have_css('color', 'rgb(129, 144, 163)')
        host_dialog.get_by_role('button',name='取消',exact=True).click()
        expect(host).to_be_enabled()

        stop_host = page.locator('.editor-canvas-header').get_by_role('button', name='停止检测')
        expect(stop_host).to_be_disabled()
        expect(stop_host).to_have_css('background-color', 'rgb(232, 237, 243)')
        page.locator('.editor-properties').get_by_text('停止按钮', exact=True).click()
        page.get_by_role('button', name='停止按钮高级外观…', exact=True).click()
        stop_dialog = page.get_by_role('dialog', name='高级外观 · 停止按钮')
        stop_sample = stop_dialog.get_by_role('button', name='停止检测', exact=True)
        page.mouse.move(1, 1)
        expect(stop_sample).to_have_css('background-color', 'rgb(255, 247, 232)')
        expect(stop_sample).to_have_css('border-color', 'rgb(216, 181, 123)')
        stop_sample.hover()
        expect(stop_sample).to_have_css('background-color', 'rgb(255, 242, 225)')
        expect(stop_sample).to_have_css('border-color', 'rgb(200, 138, 47)')
        page.mouse.down()
        expect(stop_sample).to_have_css('background-color', 'rgb(248, 224, 181)')
        expect(stop_sample).to_have_css('border-color', 'rgb(153, 83, 5)')
        page.mouse.up()
        stop_dialog.get_by_label('预览禁用状态').check()
        expect(stop_sample).to_be_disabled()
        expect(stop_sample).to_have_css('background-color', 'rgb(232, 237, 243)')
        expect(stop_sample).to_have_css('border-color', 'rgb(210, 220, 230)')
        expect(stop_sample).to_have_css('color', 'rgb(129, 144, 163)')
        stop_dialog.get_by_role('button', name='取消', exact=True).click()

        for mode, part, role in (('tabs', 'tab', 'tab'), ('buttons', 'action', 'button')):
            page.get_by_label('页签样式', exact=True).select_option(mode)
            page.get_by_role('button', name='示例预览', exact=True).click()
            preview = page.get_by_role('dialog', name='示例数据预览')
            navigation = preview.get_by_label('页面导航')
            overview = navigation.get_by_role(role, name='A 检测页')
            details = navigation.get_by_role(role, name='详情页')
            expect(overview).to_have_attribute('data-emo-part', part)
            expect(overview).to_have_attribute('aria-current', 'page')
            expect(overview).to_have_css('background-color', 'rgb(33, 77, 133)')
            expect(details).not_to_have_css('background-color', 'rgb(33, 77, 133)')
            details.click()
            expect(details).to_have_attribute('aria-current', 'page')
            expect(details).to_have_css('background-color', 'rgb(33, 77, 133)')
            expect(overview).not_to_have_css('background-color', 'rgb(33, 77, 133)')
            overview.click()
            expect(overview).to_have_css('background-color', 'rgb(33, 77, 133)')
            preview.get_by_role('button', name='关闭预览', exact=True).click()
    finally:
        context.close()


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            executable = os.environ.get('CHROMIUM_PATH') or (EDGE if Path(EDGE).is_file() else None)
            browser = playwright.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
            try:
                appearance_case(browser, f'http://127.0.0.1:{server.server_port}')
                print('Advanced appearance draft, CSS cascade, scope, save, undo, marker, host states, border line styles and navigation passed.')
            finally:
                browser.close()
    finally:
        server.shutdown()
