import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const require = createRequire(import.meta.url);
for (const extension of ['.ts', '.tsx']) require.extensions[extension] = (module, path) =>
    module._compile(ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
        module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    }}).outputText, path);
const React = require('react');
const { JSDOM } = require('jsdom');
const { newPresentation, newComponent } = require('../src/editor/model.ts');
const { PagePresentationFields, ComponentPresentationFields, HistoryFields } = require('../src/editor/PresentationFields.tsx');

test('public Web property fields edit shell controls, indicator and history without losing settings', async () => {
    const dom = new JSDOM('<div id="root"></div>', { url: 'http://panel.example' });
    const keys = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'];
    const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    Object.assign(globalThis, { window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true });
    const initial = newPresentation(), pageId = initial.defaultPageId;
    const indicator = newComponent('indicator'), records = newComponent('sqlite_table');
    records.props.historySource = { componentId: 'runtime-records' };
    initial.pages[pageId].components = [indicator, records];
    const sources = { 'runtime-records': { kind: 'table', componentId: 'runtime-records', columns: ['id', 'result'] } };
    let current, uploaded;
    function Forms() {
        const [doc, setDoc] = React.useState(initial);
        current = doc;
        const edit = change => setDoc(previousDoc => {
            const next = structuredClone(previousDoc);
            change(next);
            return next;
        });
        const page = doc.pages[pageId];
        const update = index => change => edit(next => change(next.pages[pageId].components[index]));
        return React.createElement(React.Fragment, null,
            React.createElement('section', { id: 'shell-fields' }, React.createElement(PagePresentationFields, {
                doc, edit, busy: false, uploadLogo: file => {
                    uploaded = file;
                    edit(next => { next.shell.logoResourceId = 'uploaded-logo'; });
                },
            })),
            React.createElement('section', { id: 'indicator-fields' }, React.createElement(ComponentPresentationFields, {
                component: page.components[0], update: update(0),
            })),
            React.createElement('section', { id: 'history-fields' }, React.createElement(HistoryFields, {
                component: page.components[1], page, update: update(1), sources,
            })));
    }
    const { createRoot } = require('react-dom/client');
    const root = createRoot(dom.window.document.getElementById('root'));
    const field = label => dom.window.document.querySelector(`[aria-label="${label}"]`)
        || [...dom.window.document.querySelectorAll('label')].find(item => item.textContent === label)?.querySelector('input,select');
    async function change(label, value) {
        const element = field(label);
        assert.ok(element, `Missing field ${label}`);
        await React.act(async () => {
            if (element.tagName === 'SELECT') {
                element.value = value;
                element.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
            } else {
                Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(element, String(value));
                element.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
            }
        });
    }
    async function toggle(label) {
        assert.ok(field(label), `Missing checkbox ${label}`);
        await React.act(async () => field(label).click());
    }
    try {
        await React.act(async () => root.render(React.createElement(Forms)));
        await change('运行页头配置', 'custom');
        await change('开始按钮文字', '开始检测');
        await change('停止按钮文字', '停止检测');
        await change('开始按钮外观配置', 'custom');
        await change('开始按钮样式', 'primary');
        await change('开始按钮字号', 20);
        await change('开始按钮圆角', 10);
        await change('开始按钮最小宽度', 128);
        await change('Logo 显示宽度', 80);
        await change('Logo 显示高度', 48);
        await change('页头表面配置', 'custom');
        await change('页头背景', 'surface');
        await change('页头圆角', 16);
        await change('页头文字配置', 'custom');
        await change('页头标题字号', 32);
        const file = new dom.window.File(['test-only'], 'logo.png', { type: 'image/png' });
        const upload = field('上传页头图片');
        Object.defineProperty(upload, 'files', { configurable: true, value: [file] });
        await React.act(async () => upload.dispatchEvent(new dom.window.Event('change', { bubbles: true })));
        assert.equal(uploaded, file);
        assert.equal(current.shell.startControl.label, '开始检测');
        assert.equal(current.shell.stopControl.label, '停止检测');
        assert.deepEqual(current.shell.startControl.appearance, { style: 'primary', text: { size: 20 }, radius: 10, minWidth: 128 });
        assert.equal(current.shell.logoResourceId, 'uploaded-logo');
        assert.deepEqual([current.shell.logoWidth, current.shell.logoHeight], [80, 48]);
        assert.equal(current.shell.headerSurface.radius, 16);
        assert.equal(current.shell.headerTypography.title.size, 32);

        await change('判定显示', 'row');
        await change('圆点位置', 'title');
        await toggle('标题跟随状态颜色');
        assert.equal(current.pages[pageId].components[0].props.markerPosition, 'title');
        assert.equal(current.pages[pageId].components[0].props.titleFollowsState, true);

        for (const label of ['显示筛选栏', '显示排序控制', '显示分页栏']) await toggle(label);
        await change('历史显示方式', 'list');
        await change('列表主字段', 'id');
        await change('历史显示方式', 'table');
        await change('历史显示方式', 'list');
        assert.deepEqual(current.pages[pageId].components[1].props.historyView, {
            mode: 'list', titleField: 'id', showFilter: false, showSort: false, showPagination: false,
        });
        for (const label of ['显示筛选栏', '显示排序控制', '显示分页栏']) assert.equal(field(label).checked, false);
        assert.equal(field('列表主字段').value, 'id');
    } finally {
        await React.act(async () => root.unmount());
        dom.window.close();
        for (const [key, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    }
});
