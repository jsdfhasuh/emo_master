"""Local-only launcher ownership, separate from reusable Qt rendering."""
import hashlib
import json
from pathlib import Path
import tempfile

import cv2
import numpy as np

from examples.runtime_pages_p2 import sampleProject
from emo_master.core.project.models import ProjectDocument


def pageConfiguration(sources, scopes):
    payload = json.loads((Path(__file__).with_name("p3_pages.json")).read_text(encoding="utf-8"))
    payload.update(dataSources=sources, resultScopes=scopes)
    return payload


def sampleProjectP3(root, count=12):
    raw = sampleProject(root).model_dump()
    pixels = np.zeros((120, 160, 3), np.uint8)
    for x in (15, 65, 115):
        pixels[45:65, x:x+15] = 255
    cv2.imwrite(str(root / "second.png"), pixels)
    data = (root / "second.png").read_bytes()
    raw['resources']['items']['second'] = dict(path='second.png',sha256=hashlib.sha256(data).hexdigest(),size=len(data),purpose='input_image')
    workflow = raw['workflows'].pop('main')
    workflow['nodes'].extend([{'nodeId':'load-b','operatorId':'vision.io.image_loader'},
                             {'nodeId':'select','operatorId':'test.p3.select'}])
    workflow['edges'] = [edge for edge in workflow['edges'] if edge['toNode'] != 'blob'] + [
        {'fromNode':node,'fromPort':port,'toNode':'select','toPort':target}
        for node,port,target in [('load','image','a'),('load-b','image','b'),('load','frame','fa'),('load-b','frame','fb')]] + [
        {'fromNode':'select','fromPort':port,'toNode':'blob','toPort':port} for port in ('image','frame')]
    raw['workflows']['detect'] = workflow
    raw['workflows']['main'] = {'name':'Local sample loop','nodes':[
        {'nodeId':'input','kind':'workflow_input'},
        {'nodeId':'loop','kind':'loop','loop':{'contractVersion':1,'mode':'repeat','bodyWorkflowId':'detect','repeatCount':count,'maxIterations':count}},
        {'nodeId':'output','kind':'workflow_output'}]}
    raw['workflowOrder']=['main','detect']
    raw['resources']['parameterBindings'][0]['target']['workflowId']='detect'
    raw['resources']['parameterBindings'].append({'target':{'workflowId':'detect','nodeId':'load-b','parameterPath':['imagePath']},'resourceId':'second'})
    path=[{'nodeId':'loop','relation':'loop_body'}]
    raw['presentation']['resultScopes']['root'].update(scopeWorkflowId='detect',callPath=path)
    for source in raw['presentation']['dataSources'].values():
        source.update(workflowId='detect',callPath=path)
    raw['presentation']=pageConfiguration(raw['presentation']['dataSources'],raw['presentation']['resultScopes'])
    return ProjectDocument.model_validate(raw)


def pluginRoots(root):
    from examples.p3_selector import Selector
    directory = root / 'plugins' / 'selector'
    directory.mkdir(parents=True)
    (directory/'manifest.json').write_text(json.dumps({'operatorId':'test.p3.select','displayName':'Local image selector',
        'version':'1.0.0','entry':'examples.p3_selector:Selector','category':'test','iconKey':'test',
        'summary':'Controlled local input selection, never installed by production',
        'inputPorts':Selector.meta.inputPorts,'outputPorts':Selector.meta.outputPorts,
        'paramSchema':{'type':'object','properties':{}},'minCoreVersion':'0.1.0','maxCoreVersion':'1.x'}),encoding='utf-8')
    return (str(Path(__file__).resolve().parents[1]/'src/emo_master/plugins/builtins'),str(root/'plugins'))


class LocalDemo:
    def __init__(self, count=12):
        from emo_master.apps.runtime.grpc_server.service import RuntimeService
        from emo_master.apps.runtime.presentation.service import PresentationService
        from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
        import grpc
        self.temp = tempfile.TemporaryDirectory(prefix='emo-p3-')
        root=Path(self.temp.name)
        self.runtime = self.presentation = self.server = None
        try:
            self.project=sampleProjectP3(root,count)
            self.runtime=RuntimeService(dbPath=root/'state.db',workspaceRoot=root/'jobs',pluginRootPaths=pluginRoots(root))
            self.presentation=PresentationService(self.runtime,root/'display')
            self.server=AioRuntimeServer(self.runtime,self.presentation)
            self.address=f'127.0.0.1:{self.server.port}'
            with grpc.insecure_channel(self.address) as channel:
                stub=rpc.DisplayServiceStub(channel)
                prepared=stub.Prepare(pb.DisplayPrepareRequest(project_json=self.project.model_dump_json(),resource_root=str(root)),timeout=15)
                self.jobId=stub.Start(pb.DisplayStartRequest(prepared_id=prepared.prepared_id),timeout=5).job_id
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.server:
            self.server.close()
            self.server=None
        if self.runtime:
            self.runtime.close()
            self.runtime=None
        if self.presentation:
            self.presentation.close()
            self.presentation=None
        self.temp.cleanup()
