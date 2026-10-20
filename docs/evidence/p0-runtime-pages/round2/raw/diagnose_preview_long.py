import tempfile,time,json
from pathlib import Path
import cv2,numpy as np
from tests.runtime.test_operator_editor_preview import _writePreviewProject
from tests.runtime.runtime_test_utils import waitForTerminal
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='testSuccessfulJobPromotesCurrentAndDirectUpstreamSnapshots-xxxxxxxxxxxxxxxxxxxxxxxxxxx-') as temp:
  root=Path(temp); image=root/'source.png';cv2.imwrite(str(image),np.full((16,18,3),80,np.uint8));_writePreviewProject(root,image)
  service=RuntimeService(dbPath=root/'db.sqlite',workspaceRoot=root/'jobs')
  try:
   assert service.LoadProject(pb.LoadProjectRequest(project_path=str(root)),None).ok
   call=service.jobSupervisor.terminalCallback
   def terminal(job,status):
    print('callback starts, repository status',service.jobRepository.get(job).status,flush=True)
    call(job,status)
    print('callback ends assets',len(service.previewAssetStore._assets),flush=True)
   service.jobSupervisor.terminalCallback=terminal
   job=service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId),None).job_id
   print('wait terminal',waitForTerminal(service,job).status,'assets',len(service.previewAssetStore._assets),flush=True)
   time.sleep(.5)
   print('after 500ms assets',len(service.previewAssetStore._assets),flush=True)
   print('loaded key',service._loadedProjectPreviewKey,'workflow keys',list(service.loadedDocument.workflows),flush=True)
   print('assets',[(a.projectKey,a.workflowId,a.nodeId,a.port,a.portType) for a in service.previewAssetStore._assets.values()],flush=True)
   print(service.ListNodePreviewSources(pb.ListNodePreviewSourcesRequest(project_id=service.loadedProjectId,workflow_id='main',node_id='roi'),None),flush=True)
   print([(e.eventType,e.message) for e in service.eventStore.read(job) if 'preview' in e.eventType],flush=True)
  finally: service.close()
