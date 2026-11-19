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
const { renderToStaticMarkup } = require('react-dom/server');
const { JSDOM } = require('jsdom');
const { PanelRenderer } = require('../src/renderer/PanelRenderer.tsx');
const { HistoryTable, useHistoryReview } = require('../src/renderer/HistoryView.tsx');
const theme = require('../../contracts/panel-contract-v1/theme.json');
const render = (component, extra = {}) => new JSDOM(renderToStaticMarkup(React.createElement(PanelRenderer, {
    panel: { id: 'panel', pages: [{ id: 'page', title: 'Page', components: [component] }] },
    pageId: 'page', onPage() {}, onAction() {}, ...extra,
}))).window.document;

test('indicator title and separate marker follow the same current mapped state', () => {
    const component = { id: 'state', type: 'indicator', binding: 'value', props: {
        title: 'Inspection', indicatorView: 'row', titleFollowsState: true, markerPosition: 'title',
        typography: { title: { color: 'primary' } },
        indicatorStates: { true: { text: 'Pass', color: 'green' }, false: { text: 'Fail', color: 'red' } },
    }};
    const show = valueJson => render(component, { result: { resultKey: 'r', sources: {
        value: { state: 'AVAILABLE', valueJson },
    }}});
    for (const [value, text, color] of [['true', 'Pass', 'green'], ['false', 'Fail', 'red']]) {
        const doc = show(value), title = doc.querySelector('.panel-field-label');
        assert.equal(title.textContent, 'Inspection');
        assert.ok(title.getAttribute('style').includes(theme.foreground[color]));
        assert.ok(title.querySelector('.panel-indicator-marker').getAttribute('style').includes(theme.foreground[color]));
        assert.equal(doc.querySelector('.panel-indicator').textContent, text);
        assert.equal(doc.querySelector('.panel-indicator .panel-indicator-marker'), null);
    }
    const empty = show('null');
    assert.equal(empty.querySelector('.panel-indicator-marker'), null);
    assert.ok(empty.querySelector('.panel-field-label').getAttribute('style').includes(theme.foreground.neutral));
    component.props.titleFollowsState = false;
    assert.ok(show('true').querySelector('.panel-field-label').getAttribute('style').includes(theme.primary));
    component.props.title = '';
    assert.ok(show('true').querySelector('.panel-indicator .panel-indicator-marker'));
});

test('history filter, sorting and pagination visibility are independent for both display modes', () => {
    const controls = { showFilter: '.history-filter', showSort: '.history-sort', showPagination: '.history-paging' };
    for (const mode of ['list', 'table']) for (const hidden of Object.keys(controls)) {
        const doc = render({ id: 'records', type: 'sqlite_table', history: { componentId: 'records', kind: 'table' },
            props: { columns: [{ key: 'id', title: 'ID' }], historyView: { mode, [hidden]: false } } }, { viewId: 'view' });
        for (const [field, selector] of Object.entries(controls))
            assert.equal(Boolean(doc.querySelector(selector)), field !== hidden, `${mode} ${hidden} ${field}`);
    }
});

test('hidden history toolbars retain external record navigation and refresh to latest', async () => {
    const dom = new JSDOM('<div id="root"></div>', { url: 'http://panel.example' });
    const keys = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT', 'fetch'];
    const previous = new Map(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    Object.assign(globalThis, { window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true });
    let latest = 3;
    const requests = [];
    globalThis.fetch = async path => {
        const params = new URL(path, dom.window.location.origin).searchParams;
        requests.push(params);
        const anchor = params.get('anchorKeyJson');
        const id = anchor ? JSON.parse(anchor)[0] - Number(params.get('anchorStep')) : latest;
        return new Response(JSON.stringify({ columns: [{ name: 'id' }], rows: [
            { rowId: String(id), keyValues: [id], values: [id] },
        ], page: latest - id, pageSize: 1, hasNext: id > 1, stableRowIdentity: true,
        selectedRowId: String(id) }), { headers: { 'Content-Type': 'application/json' } });
    };
    const component = { id: 'records', type: 'sqlite_table', history: { componentId: 'records', kind: 'table', orderBy: 'id', descending: true },
        props: { pageSize: 1, columns: [{ key: 'id', title: 'ID' }], historyView: {
            mode: 'list', titleField: 'id', showFilter: false, showSort: false, showPagination: false,
        } } };
    const page = { id: 'history', title: 'History', components: [component] };
    function Review() {
        const review = useHistoryReview('view', 'scope', 'revision', page);
        return React.createElement(React.Fragment, null,
            React.createElement(HistoryTable, { component, review }),
            React.createElement('output', null, String(review.value({ componentId: 'records', kind: 'field', field: 'id' }) ?? '')),
            ...['history_previous', 'history_next', 'history_first'].map(action => React.createElement('button', {
                key: action, id: action, disabled: !review.canReview('records', action), onClick: () => review.run('records', action),
            }, action)));
    }
    const { createRoot } = require('react-dom/client');
    const root = createRoot(dom.window.document.getElementById('root'));
    const click = async action => React.act(async () => dom.window.document.getElementById(action).click());
    try {
        await React.act(async () => root.render(React.createElement(Review)));
        assert.equal(dom.window.document.querySelector('output').textContent, '3');
        assert.equal(dom.window.document.querySelector('.history-filter,.history-sort,.history-paging'), null);
        await click('history_next');
        assert.equal(dom.window.document.querySelector('output').textContent, '2');
        assert.equal(requests.at(-1).get('anchorKeyJson'), '[3]');
        assert.equal(requests.at(-1).get('anchorStep'), '1');
        await click('history_previous');
        assert.equal(dom.window.document.querySelector('output').textContent, '3');
        latest = 4;
        await click('history_first');
        assert.equal(dom.window.document.querySelector('output').textContent, '4');
        assert.equal(requests.at(-1).has('anchorKeyJson'), false);
    } finally {
        await React.act(async () => root.unmount());
        dom.window.close();
        for (const [key, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    }
});
