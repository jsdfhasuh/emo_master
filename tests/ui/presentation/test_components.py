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


@pytest.mark.parametrize('reason',['OPTIONAL_ABSENT','BRANCH_SKIPPED','NODE_FAILED','EXPORT_TIMEOUT','RESOURCE_EXPIRED'])
def testNewUnavailableClearsOldValue(qtApp,tmp_path,reason):
    window=RuntimePages(sampleProjectP3(tmp_path).presentation)
    view=resultView(capture=window.expectedCapture)
    window.submit(view)
    scope=view.scopes['root']
    failed=scope.result.model_copy(update={'sources':(ClosedSource(sourceId='count',state='UNAVAILABLE',reasonCode=reason,reason=reason),),'status':'INCOMPLETE'})
    window.submit(replace(view,scopes=MappingProxyType({'root':replace(scope,result=failed)})))
    assert window.widgets['overview']['overview-count'][1].text()==reason
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
