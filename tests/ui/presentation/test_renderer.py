from types import MappingProxyType
import threading
import time

import numpy as np
import pytest
from PySide2.QtGui import QColor

from emo_master.clients.runtime.view_state import ScopeView, SessionView
from emo_master.core.presentation.results import ClosedResult, ResultIdentity, ClosedSource
from emo_master.core.presentation.models import Presentation
from emo_master.ui.presentation.images import ownedImage
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3


def resultView(ordinal=1, count="2", image=None, generation=1):
    identity=ResultIdentity(runtimeInstanceId="runtime",jobId="job",resultScopeId="root",invocationId=str(ordinal),
        resultKey=f"result-{ordinal}",resultOrdinal=ordinal,executionRevision="a"*64,capturePlanRevision="b"*64,mode="debug")
    result=ClosedResult(identity=identity,expectedSourceIds=("count",),sources=(ClosedSource(sourceId="count",state="AVAILABLE",valueJson=count),),
                        status="COMPLETE",executionTerminal="COMPLETED")
    scope=ScopeView(result,MappingProxyType({} if image is None else {"image":image}),MappingProxyType({}),time.perf_counter_ns())
    return SessionView(ordinal,generation,"runtime","job","CONNECTED","",MappingProxyType({"root":scope}),MappingProxyType({}),MappingProxyType({"root":ordinal}))


@pytest.mark.parametrize("channels", [1,3,4])
def testQImageOwnsCorrectChannelsAndNonContiguousStride(qtApp,channels):
    array=np.zeros((8,12,channels),np.uint8)
    color=[17] if channels==1 else [17,45,203] if channels==3 else [17,45,203,127]
    array[:]=color
    pixels=array[::2,::2]
    image=ownedImage(pixels)
    array[:]=0
    del pixels
    pixel=QColor(image.pixelColor(2,2))
    assert pixel.getRgb()==((17,17,17,255) if channels==1 else (203,45,17,255) if channels==3 else (203,45,17,127))
    assert (image.width(),image.height())==(6,4)


def testQtConversionRejectsWorkerThread(qtApp):
    errors=[]
    def work():
        try:
            ownedImage(np.zeros((2,2),np.uint8))
        except RuntimeError as error:
            errors.append(str(error))
    worker=threading.Thread(target=work)
    worker.start()
    worker.join(1)
    assert errors==["Qt presentation requires the GUI thread"]


def testConfigCreatesDifferentLayoutIdsAndActualBoundValues(qtApp,tmp_path):
    project=sampleProjectP3(tmp_path)
    config=project.presentation
    window=RuntimePages(config)
    window.show()
    window.submit(resultView(count="0"))
    assert window.widgets['overview']['overview-count'][1].text()=="0 个"
    assert window.displayed['root'].result.identity.resultKey=='result-1'
    window.navigate('detail')
    assert window.widgets['detail']['detail-count'][1].text()=="0 个"
    raw=config.model_dump()
    raw['pages']['alternate']=raw['pages'].pop('overview')
    raw['pageOrder']=['detail','alternate']
    raw['defaultPageId']='alternate'
    raw['pages']['detail']['components'][-1]['actions']['clicked']['pageId']='alternate'
    raw['pages']['alternate']['layout']['columns']=4
    second=RuntimePages(Presentation.model_validate(raw))
    assert second.currentPageId=='alternate'
    second.submit(resultView(2,'3'))
    assert second.widgets['alternate']['overview-count'][1].text()=='3 个'
    window.close()
    second.close()


def testEmptyUnboundAndUnsupportedAreExplicit(qtApp,tmp_path):
    empty=RuntimePages(Presentation())
    assert empty.currentPageId is None
    config=sampleProjectP3(tmp_path).presentation
    # Construct a valid unsupported source; never infer a fake Runtime value.
    from emo_master.core.presentation.models import DataSource
    config.dataSources['count']=DataSource(kind='global_counter',resultScopeId='root',name='counter',expectedType='integer')
    window=RuntimePages(config)
    window.submit(resultView())
    assert '不支持此来源' in window.widgets['overview']['overview-count'][1].text()
