"""Labelled simulation tests of value rendering, not real production verdicts."""
from dataclasses import replace
from types import MappingProxyType

import pytest
from PySide2.QtCore import Qt

from emo_master.core.presentation.models import Props, Component, Placement
from emo_master.core.presentation.results import ClosedSource
from emo_master.ui.presentation.table import CollectionView
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3
from tests.ui.presentation.test_renderer import resultView


def testStrictPropsDefaultsAndMappings():
    assert Props().pageSize==20 and Props().columns==[]
    for invalid in ({'pageSize':101},{'indicatorStates':{'1':{'text':'OK'}}},
                    {'columns':[{'title':'x','fieldPath':['x'],'unknown':True}]}):
        with pytest.raises(ValueError):
            Props.model_validate(invalid)


@pytest.mark.parametrize('value,expected', [('"00:03:42"', '00:03:42'),
                                         ('{"n":2}', '{"n": 2}'), ('null', '--')])
def testBoundTextDisplaysStringsWithoutJsonQuotes(qtApp, tmp_path, value, expected):
    config = sampleProjectP3(tmp_path).presentation
    component = next(c for c in config.pages['overview'].components if c.type == 'number')
    component.type = 'text'
    component.props.emptyText = '--'
    config.dataSources['count'].expectedType = 'string'
    window = RuntimePages(config)
    window.submit(resultView(count=value, capture=window.expectedCapture))
    assert window.widgets['overview']['overview-count'][1].text() == expected
    window.close()


def testSansUsesWindowsCjkSansFamily(qtApp, monkeypatch):
    from emo_master.ui.presentation import renderer
    monkeypatch.setattr(renderer.sys, 'platform', 'win32')
    assert "font-family:'Microsoft YaHei UI'" in renderer.appearanceStyle(Props(fontFamily='sans'))


def testSpanningImageKeepsAdjacentScalarFooterCompact(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    page = config.pages['overview']
    page.layout.columns = 2
    page.components = [
        Component(componentId='live', type='image', layout=Placement(row=0, column=0, rowSpan=2)),
        Component(componentId='crop', type='image', layout=Placement(row=0, column=1)),
        Component(componentId='value', type='number', layout=Placement(row=1, column=1)),
    ]
    window = RuntimePages(config)
    grid = window.pages['overview'].widget().layout()
    assert grid.rowStretch(0) == 1
    assert grid.rowStretch(1) == 0
    window.close()


def testCollectionPagingSortingEmptyAndNewIdentity(qtApp):
    widget=CollectionView(Props.model_validate({'pageSize':2,'columns':[{'title':'值','fieldPath':['v']}]}))
    widget.submit({'items':[{'v':10},{'v':2},{'v':0}]},'N','')
    assert widget.model.rowCount()==2
    widget.model.sort(0,Qt.AscendingOrder)
    assert widget.model.data(widget.model.index(0,0))=='0'
    widget.move(1)
    assert widget.model.data(widget.model.index(0,0))=='10'
    widget.submit([{'v':99}],'N+1','')
    assert widget.model.page==0 and widget.model.rowCount()==1
    assert widget.model.data(widget.model.index(0,0))=='99'
    widget.submit([],'empty','')
    assert widget.model.rowCount()==0 and '0 行' in widget.pageLabel.text()
    widget.submit(None,'failure','BRANCH_SKIPPED')
    assert widget.message.text()=='BRANCH_SKIPPED' and widget.model.key==''
    widget.close()


@pytest.mark.parametrize('value,expected',[('false','不合格'),('true','合格'),('null','—'),('"other"','未映射判定值: "other"')])
def testIndicatorOnlyUsesExplicitValueNeverComplete(qtApp,tmp_path,value,expected):
    config=sampleProjectP3(tmp_path).presentation
    component=next(c for c in config.pages['overview'].components if c.type=='number')
    component.type='indicator'
    component.props=Props.model_validate({'indicatorStates':{'true':{'text':'合格','color':'green'},'false':{'text':'不合格','color':'red'}}})
    config.dataSources['count'].expectedType='boolean'
    window=RuntimePages(config,label='模拟组件测试，不代表业务规则')
    window.submit(resultView(count=value,capture=window.expectedCapture))
    assert window.widgets['overview']['overview-count'][1].text()==expected
    window.close()


@pytest.mark.parametrize('reason,message',[
    ('OPTIONAL_ABSENT','本次未提供此项结果'), ('BRANCH_SKIPPED','本次未执行此项检测'),
    ('NODE_FAILED','本次处理异常，请联系技术人员'), ('EXPORT_TIMEOUT','结果读取超时，请检查连接'),
    ('RESOURCE_EXPIRED','此结果已过期，请等待新的结果')])
def testNewUnavailableClearsOldValue(qtApp,tmp_path,reason,message):
    window=RuntimePages(sampleProjectP3(tmp_path).presentation)
    view=resultView(capture=window.expectedCapture)
    window.submit(view)
    scope=view.scopes['root']
    failed=scope.result.model_copy(update={'sources':(ClosedSource(sourceId='count',state='UNAVAILABLE',reasonCode=reason,reason=reason),),'status':'INCOMPLETE'})
    window.submit(replace(view,scopes=MappingProxyType({'root':replace(scope,result=failed)})))
    assert window.widgets['overview']['overview-count'][1].text()==message
    assert window.widgets['overview']['overview-count'][1].toolTip()==reason
    window.close()


def testProjectedCollectionAndBoundedWidgetCount(qtApp,tmp_path):
    widget=CollectionView(Props.model_validate({'columns':[{'title':'已投影值','fieldPath':[]}]}))
    widget.submit([0,False,None],'projected','')
    assert [widget.model.data(widget.model.index(i,0)) for i in range(3)]==['0','false','null']
    widget.close()
    config=sampleProjectP3(tmp_path).presentation
    config.pages['overview'].components=[Component(componentId=f't{i}',type='table',layout=Placement(row=i)) for i in range(5)]
    window=RuntimePages(config)
    window.submit(resultView(capture=window.expectedCapture))
    assert 'UI_TABLE_BUDGET' in window.widgets['overview']['t4'][1].text()
    window.close()


def testEvictHiddenPageBeforeAllocatingTableQuota(qtApp,tmp_path):
    from emo_master.core.presentation.models import Page
    config=sampleProjectP3(tmp_path).presentation
    config.pages={key:Page(name=key,components=[Component(componentId=f'{key}-{i}',type='table',layout=Placement(row=i)) for i in range(2)])
                  for key in ('a','b','c')}
    config.pageOrder=['a','b','c']
    config.defaultPageId='a'
    window=RuntimePages(config)
    window.navigate('b')
    window.navigate('c')
    assert set(window.pages)=={'b','c'}
    assert all(isinstance(w,CollectionView) for _c,w in window.widgets['c'].values())
    window.close()


def testCollectionFitsSidebarAndKeepsPreciseValueTooltip(qtApp):
    widget = CollectionView(Props.model_validate({'pageSize': 5, 'columns': [
        {'title': 'Label', 'fieldPath': ['label']},
        {'title': 'Confidence', 'fieldPath': ['confidence']},
    ]}))
    rows = [{'label': 'face', 'confidence': 0.7617098093032837}]
    widget.submit(rows, 'result', '')
    widget.resize(380, 220)
    widget.show()
    qtApp.processEvents()
    header = widget.table.horizontalHeader()
    assert header.sectionSize(1) > header.sectionSize(0)
    assert header.length() == widget.table.viewport().width()
    assert not widget.table.wordWrap()
    index = widget.model.index(0, 1)
    assert widget.model.data(index) == '0.7617098093032837'
    assert widget.model.data(index, Qt.ToolTipRole) == '<qt>0.7617098093032837</qt>'
    compactHeight = widget.table.height()
    assert compactHeight >= widget.table.minimumHeight()
    widget.resize(380, 480)
    qtApp.processEvents()
    assert widget.table.height() > compactHeight + 100
    assert widget.model.rows == rows and widget.model.key == 'result'
    widget.close()


def testCollectionTooltipEscapesMarkupAndRetainsTextLimit(qtApp):
    import html

    widget = CollectionView(Props.model_validate({'columns': [{'title': 'Label', 'fieldPath': []}]}))
    value = '<img src="file:///not-a-runtime-asset.png">' + 'x' * 1100
    widget.submit([value], 'result', '')
    index = widget.model.index(0, 0)
    visible = widget.model.data(index)
    tooltip = widget.model.data(index, Qt.ToolTipRole)
    assert len(visible) < len(value)
    assert tooltip == '<qt>' + html.escape(visible) + '</qt>'
    assert '<img' not in tooltip
    widget.close()


def testImageSidebarTableFitsPreviewWithoutOuterScroll(qtApp, tmp_path, monkeypatch):
    from PySide2.QtCore import QRect
    from emo_master.core.presentation.models import Grid, Page

    monkeypatch.setattr(qtApp.primaryScreen(), 'availableGeometry', lambda: QRect(0, 0, 1920, 1080))
    config = sampleProjectP3(tmp_path).presentation
    config.pages['overview'] = Page(name='Inspection', layout=Grid(columns=12, spacing=8), components=[
        Component(componentId='overlay', type='image', bindings={'image': 'image'},
                  layout=Placement(row=0, column=0, rowSpan=3, columnSpan=8)),
        Component(componentId='count', type='number', bindings={'value': 'count'},
                  props=Props(title='Count', fontSize=18),
                  layout=Placement(row=0, column=8, columnSpan=4)),
        Component(componentId='raw', type='image', bindings={'image': 'image'},
                  layout=Placement(row=1, column=8, columnSpan=4)),
        Component(componentId='table', type='table', bindings={'rows': 'blobs'},
                  props=Props(title='Detections', columns=[{'title': 'Area', 'fieldPath': ['area']}]),
                  layout=Placement(row=2, column=8, columnSpan=4)),
    ])
    window = RuntimePages(config)
    window.setSimulationState('OK')
    window.resize(1024, 768)
    window.show()
    qtApp.processEvents()
    page = window.pages['overview']
    assert page.verticalScrollBar().maximum() == 0
    assert page.horizontalScrollBar().maximum() == 0
    window.close()
